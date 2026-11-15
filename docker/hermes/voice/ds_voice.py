#!/usr/bin/env python3
"""DeepSeek TTS/ASR bridge for Hermes command providers.

Runtime contract (stdlib only; no third-party imports):

    ds_voice.py tts --text-file PATH --out PATH --format wav --voice stella
    ds_voice.py tts --text "..."      --out PATH --format wav --voice stella
    ds_voice.py asr --in PATH --out PATH
    ds_voice.py asr --in PATH --stdout

API key: only ``DEEPSEEK_API_KEY``.  Hermes command providers must declare it in
their ``env_passthrough`` list; the gateway process already holds it.

Exit codes:
    0 success
    2 usage / text too long
    3 missing DEEPSEEK_API_KEY
    4 upstream HTTP or network failure after retries
    5 unsupported / invalid audio input
    6 empty transcript
    7 ffmpeg missing or transcode failed

DeepSeek limits enforced here: TTS 10,000 billed characters (Han = 2), ASR
8 MiB / 120 seconds, WAV 8-48 kHz or Ogg Opus.  HTTP 429/503/504/408 and
network failures are retried up to three times with jittered backoff.
"""

import argparse
import http.client
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_TTS_BILLED_CHARS = 10_000
MAX_TTS_BODY_BYTES = 64 * 1024
MAX_ASR_BYTES = 8 * 1024 * 1024
MAX_ASR_SECONDS = 120
SUPPORTED_WAV_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}
RETRY_HTTP_CODES = {408, 429, 503, 504}


class UpstreamError(Exception):
    """HTTP or transport failure that should surface as exit code 4."""

    def __init__(self, message):
        super().__init__(message)


def log(args, message):
    if not getattr(args, "quiet", False):
        print(f"ds_voice: {message}", file=sys.stderr)


def die(code, message):
    print(f"ds_voice: {message}", file=sys.stderr)
    raise SystemExit(code)


def api_key():
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        die(3, "DEEPSEEK_API_KEY is not set; add it to the provider's env_passthrough")
    return key


def api_url(base_url, path):
    return base_url.rstrip("/") + path


def is_han(char):
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
    )


def billed_chars(text):
    """DeepSeek TTS billing: Han characters count as 2, everything else as 1."""
    return sum(2 if is_han(ch) else 1 for ch in text)


def retry_delay(attempt, headers=None):
    if headers is not None:
        raw = headers.get("Retry-After")
        if raw and str(raw).strip().isdigit():
            return min(int(str(raw).strip()), 10)
    return min((2 ** attempt) + random.uniform(0, 0.5), 10)


def _should_retry_http(code):
    return code in RETRY_HTTP_CODES


def _transport_error(exc):
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError,
                            http.client.HTTPException, OSError))


def download_to_temp(url, data, headers, timeout, retries, out_dir, suffix):
    """POST and stream the response body into a temp file in *out_dir*.

    Returns the temp path.  Retries only on retryable status / transport errors;
    a partial file is removed before each retry.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    last_detail = "unknown error"
    for attempt in range(retries):
        fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), prefix=".ds_voice_", suffix=suffix)
        os.close(fd)
        try:
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                with open(tmp_name, "wb") as handle:
                    shutil.copyfileobj(response, handle, 64 * 1024)
            if os.path.getsize(tmp_name) == 0:
                raise UpstreamError("empty response body")
            return tmp_name
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            last_detail = f"HTTP {exc.code}: {detail}"
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if _should_retry_http(exc.code) and attempt < retries - 1:
                time.sleep(retry_delay(attempt, exc.headers))
                continue
            raise UpstreamError(last_detail)
        except Exception as exc:  # noqa: BLE001 - transport/protocol errors only
            if not _transport_error(exc):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            last_detail = f"{type(exc).__name__}: {exc}"
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if attempt < retries - 1:
                time.sleep(retry_delay(attempt))
                continue
            raise UpstreamError(last_detail)
    raise UpstreamError(last_detail)


def post_json(url, data, headers, timeout, retries):
    """POST and parse a JSON object, with the same retry policy."""
    last_detail = "unknown error"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError as exc:
                raise UpstreamError(f"invalid JSON response: {exc}")
            if not isinstance(parsed, dict):
                raise UpstreamError("response is not a JSON object")
            return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            last_detail = f"HTTP {exc.code}: {detail}"
            if _should_retry_http(exc.code) and attempt < retries - 1:
                time.sleep(retry_delay(attempt, exc.headers))
                continue
            raise UpstreamError(last_detail)
        except Exception as exc:  # noqa: BLE001 - transport/protocol errors only
            if not _transport_error(exc):
                raise
            last_detail = f"{type(exc).__name__}: {exc}"
            if attempt < retries - 1:
                time.sleep(retry_delay(attempt))
                continue
            raise UpstreamError(last_detail)
    raise UpstreamError(last_detail)


def atomic_replace(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".ds_voice_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_tts_text(args):
    if args.text_file:
        try:
            text = Path(args.text_file).read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            die(2, f"cannot read input text: {exc}")
    else:
        text = (args.text or "").strip()
    if not text:
        die(2, "text is empty")
    return text


def validate_wav(path):
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
        return header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    except OSError:
        return False


def command_tts(args):
    text = read_tts_text(args)
    billed = billed_chars(text)
    if billed > MAX_TTS_BILLED_CHARS:
        die(2, f"text is {billed} billed chars; DeepSeek TTS accepts at most "
               f"{MAX_TTS_BILLED_CHARS} (Han counts as 2)")

    voice = (args.voice or "stella").strip() or "stella"
    if voice not in ("stella", "tide"):
        die(2, f"unsupported voice {voice!r}; use stella or tide")

    api_format = "pcm" if args.format == "pcm" else "wav"
    payload = json.dumps({
        "text": text,
        "voice_id": voice,
        "format": api_format,
        "filter_markdown": True,
    }, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_TTS_BODY_BYTES:
        die(2, f"request body is {len(payload)} bytes; DeepSeek TTS accepts at most "
               f"{MAX_TTS_BODY_BYTES}")

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "Authorization": "Bearer " + api_key(),
        "Content-Type": "application/json",
    }
    started = time.time()
    tmp_name = download_to_temp(
        api_url(args.base_url, "/v1/tools/tts"), payload, headers,
        args.timeout, args.retries, out.parent, "." + api_format,
    )
    try:
        size = os.path.getsize(tmp_name)
        if api_format == "wav":
            if not validate_wav(tmp_name):
                die(4, "DeepSeek returned a non-WAV body for format=wav")
            if size < 1000:
                die(4, f"response too small ({size} bytes); synthesis likely failed")
        elif size < 500:
            die(4, f"response too small ({size} bytes); synthesis likely failed")

        if args.format == "mp3":
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                die(7, "ffmpeg not found; use --format wav")
            converted_fd, converted = tempfile.mkstemp(
                dir=str(out.parent), prefix=".ds_voice_", suffix=".mp3"
            )
            os.close(converted_fd)
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", tmp_name,
                 "-codec:a", "libmp3lame", "-q:a", "4", converted],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if result.returncode != 0 or os.path.getsize(converted) < 100:
                detail = result.stderr.decode("utf-8", "replace")[:300]
                try:
                    os.unlink(converted)
                except OSError:
                    pass
                die(7, f"ffmpeg conversion failed: {detail or 'no output'}")
            atomic_replace(converted, out)
        else:
            atomic_replace(tmp_name, out)
        log(args, f"tts ok voice={voice} chars_billed={billed} bytes={size} "
                  f"secs={time.time() - started:.2f} out={out}")
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def ffprobe_duration(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        value = result.stdout.strip()
        return float(value) if value else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _wav_info_manual(path):
    """Parse a WAV header without trusting the data-chunk size.

    DeepSeek TTS streams WAV with a placeholder data size (observed
    ``0x7FFFFFFF``), so ``wave.getnframes()`` reports a bogus duration.  Use
    the actual file length for the data chunk instead.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            header = handle.read(12)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return None
            position = 12
            rate = channels = bits = 0
            data_size = None
            while position + 8 <= size:
                handle.seek(position)
                chunk = handle.read(8)
                if len(chunk) < 8:
                    break
                chunk_id = chunk[:4]
                chunk_size = int.from_bytes(chunk[4:8], "little")
                if chunk_id == b"fmt ":
                    fmt = handle.read(16)
                    if len(fmt) < 16:
                        return None
                    channels = int.from_bytes(fmt[2:4], "little")
                    rate = int.from_bytes(fmt[4:8], "little")
                    bits = int.from_bytes(fmt[14:16], "little")
                elif chunk_id == b"data":
                    data_offset = position + 8
                    if chunk_size in (0, 0x7FFFFFFF, 0xFFFFFFFF) or data_offset + chunk_size > size:
                        chunk_size = size - data_offset
                    data_size = chunk_size
                    break
                position += 8 + chunk_size + (chunk_size & 1)
            if data_size is None or rate <= 0 or channels <= 0 or bits <= 0 or bits % 8:
                return None
            duration = data_size / float(rate * channels * (bits // 8))
            return bits // 8, rate, duration
    except (OSError, ValueError):
        return None


def wav_info(path):
    """Return (sampwidth, framerate, duration) for a readable WAV, else None."""
    manual = _wav_info_manual(path)
    if manual is not None:
        return manual
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            width = handle.getsampwidth()
            duration = frames / float(rate) if rate else 0.0
            return width, rate, duration
    except (wave.Error, EOFError, OSError):
        return None


def transcode_to_16k_wav(src):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None, "ffmpeg not found"
    fd, dst = tempfile.mkstemp(prefix="ds_voice_asr_", suffix=".wav")
    os.close(fd)
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
             "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", dst],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None, f"ffmpeg failed to start: {exc}"
    if result.returncode != 0 or os.path.getsize(dst) < 100:
        detail = result.stderr.decode("utf-8", "replace")[:300]
        try:
            os.unlink(dst)
        except OSError:
            pass
        return None, f"ffmpeg transcode failed: {detail or 'no output'}"
    return dst, None


def prepare_asr_audio(path):
    """Return (upload_path, cleanup_path_or_None).

    Passes supported WAV/Ogg through; transcodes anything else when ffmpeg is
    available.  Enforces 8 MiB / 120 s before upload.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        die(5, f"cannot read input audio: {exc}")
    if size <= 0:
        die(5, "input audio is empty")
    if size > MAX_ASR_BYTES:
        die(5, f"input is {size / 1048576:.1f} MiB; DeepSeek ASR accepts at most 8 MiB")

    cleanup = None
    upload = path
    info = wav_info(path)
    if info is not None:
        width, rate, duration = info
        if duration > MAX_ASR_SECONDS + 1:
            die(5, f"audio is {duration:.0f}s; DeepSeek ASR accepts at most "
                   f"{MAX_ASR_SECONDS}s")
        if width == 2 and rate in SUPPORTED_WAV_RATES:
            return upload, cleanup
        if width != 2 or rate not in SUPPORTED_WAV_RATES:
            converted, error = transcode_to_16k_wav(path)
            if error:
                die(5, f"WAV {rate} Hz / {width * 8}-bit is not accepted; {error}")
            upload, cleanup = Path(converted), converted
    else:
        duration = ffprobe_duration(path)
        if duration is not None and duration > MAX_ASR_SECONDS + 1:
            die(5, f"audio is {duration:.0f}s; DeepSeek ASR accepts at most "
                   f"{MAX_ASR_SECONDS}s")
        with open(path, "rb") as handle:
            magic = handle.read(4)
        if magic == b"OggS" and not shutil.which("ffmpeg"):
            # Ogg Opus is accepted as-is; without ffmpeg we cannot inspect the codec.
            return upload, cleanup
        converted, error = transcode_to_16k_wav(path)
        if error:
            if magic == b"OggS":
                # ffmpeg absent: let DeepSeek decide whether this Ogg is Opus.
                return upload, cleanup
            die(5, f"unsupported audio container; {error}")
        upload, cleanup = Path(converted), converted

    new_info = wav_info(upload)
    if new_info is not None:
        _, _, new_duration = new_info
        if new_duration > MAX_ASR_SECONDS + 1:
            die(5, f"transcoded audio is {new_duration:.0f}s; DeepSeek ASR accepts "
                   f"at most {MAX_ASR_SECONDS}s")
    new_size = upload.stat().st_size
    if new_size > MAX_ASR_BYTES:
        die(5, f"transcoded audio is {new_size / 1048576:.1f} MiB; DeepSeek ASR "
               f"accepts at most 8 MiB")
    return upload, cleanup


def multipart_body(path):
    boundary = "----dsvoice" + uuid.uuid4().hex
    suffix = path.suffix.lower()
    if suffix == ".wav":
        content_type = "audio/wav"
        filename = "audio.wav"
    elif suffix in (".ogg", ".opus"):
        content_type = "audio/ogg"
        filename = "audio.ogg"
    else:
        content_type = "application/octet-stream"
        filename = "audio" + suffix
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    with open(path, "rb") as handle:
        payload = handle.read()
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + payload + tail
    headers = {
        "Authorization": "Bearer " + api_key(),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    return body, headers


def command_asr(args):
    upload, cleanup = prepare_asr_audio(args.input)
    try:
        body, headers = multipart_body(upload)
        started = time.time()
        data = post_json(api_url(args.base_url, "/v1/tools/asr"), body, headers,
                         args.timeout, args.retries)
        text = (data.get("text") or "").strip()
        if not text:
            die(6, f"empty transcript (request_id={data.get('request_id')})")
        if args.stdout:
            sys.stdout.write(text + "\n")
        else:
            write_text_atomic(args.out, text + "\n")
        log(args, f"asr ok duration_ms={data.get('duration_ms')} bytes={os.path.getsize(upload)} "
                  f"secs={time.time() - started:.2f} request_id={data.get('request_id')}")
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass


def add_common_arguments(parser, timeout):
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="DeepSeek API base URL (tests / self-hosted mock)")
    parser.add_argument("--timeout", type=int, default=timeout)
    parser.add_argument("--retries", type=int, default=3,
                        help="total attempts for retryable failures (default 3)")
    parser.add_argument("--quiet", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(
        description="DeepSeek TTS/ASR bridge for Hermes command providers")
    sub = parser.add_subparsers(dest="command", required=True)

    tts = sub.add_parser("tts", help="synthesize text to an audio file")
    group = tts.add_mutually_exclusive_group(required=True)
    group.add_argument("--text-file")
    group.add_argument("--text")
    tts.add_argument("--out", required=True)
    tts.add_argument("--format", choices=("wav", "mp3", "pcm"), default="wav")
    tts.add_argument("--voice", choices=("stella", "tide"), default="stella")
    add_common_arguments(tts, 180)

    asr = sub.add_parser("asr", help="transcribe an audio file")
    asr.add_argument("--in", "--input", dest="input", required=True)
    asr_out = asr.add_mutually_exclusive_group(required=True)
    asr_out.add_argument("--out")
    asr_out.add_argument("--stdout", action="store_true")
    add_common_arguments(asr, 300)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.retries < 1:
        die(2, "--retries must be at least 1")
    try:
        if args.command == "tts":
            command_tts(args)
        else:
            command_asr(args)
    except UpstreamError as exc:
        die(4, str(exc))


if __name__ == "__main__":
    main()
