"""Mock-server E2E tests for docker/hermes/voice/ds_voice.py (no real API key)."""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker" / "hermes" / "voice" / "ds_voice.py"


def wav_bytes(seconds=0.1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test server
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.path, body))
        if self.path == "/v1/tools/tts":
            self.server.tts_count += 1
            if self.server.fail_tts_once and self.server.tts_count == 1:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            payload = wav_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/v1/tools/asr":
            payload = json.dumps({
                "request_id": "req-test",
                "text": "今天上海天气怎么样？",
                "duration_ms": 1000,
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.requests = []
    httpd.tts_count = 0
    httpd.fail_tts_once = False
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def run(args, key="test-key", base_url=None):
    env = os.environ.copy()
    if key is None:
        env.pop("DEEPSEEK_API_KEY", None)
    else:
        env["DEEPSEEK_API_KEY"] = key
    full = [sys.executable, str(SCRIPT)] + list(args)
    if base_url:
        full += ["--base-url", base_url]
    return subprocess.run(full, capture_output=True, text=True, env=env, timeout=60)


def base(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


def test_tts_writes_valid_wav(tmp_path, server):
    out = tmp_path / "speech.wav"
    result = run(["tts", "--text", "你好", "--out", str(out)], base_url=base(server))
    assert result.returncode == 0, result.stderr
    assert out.read_bytes()[:4] == b"RIFF"
    assert "tts ok" in result.stderr


def test_tts_text_file(tmp_path, server):
    text = tmp_path / "text.txt"
    text.write_text("今天上海天气怎么样？", encoding="utf-8")
    out = tmp_path / "speech.wav"
    result = run(["tts", "--text-file", str(text), "--out", str(out)], base_url=base(server))
    assert result.returncode == 0, result.stderr
    assert out.stat().st_size > 1000


def test_asr_stdout(tmp_path, server):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(wav_bytes(0.5))
    result = run(["asr", "--in", str(audio), "--stdout"], base_url=base(server))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "今天上海天气怎么样？"
    assert "asr ok" in result.stderr


def test_asr_writes_file(tmp_path, server):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(wav_bytes(0.5))
    out = tmp_path / "text.txt"
    result = run(["asr", "--in", str(audio), "--out", str(out)], base_url=base(server))
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8").strip() == "今天上海天气怎么样？"


def test_missing_key_exits_3(tmp_path, server):
    out = tmp_path / "speech.wav"
    result = run(["tts", "--text", "你好", "--out", str(out)], key=None, base_url=base(server))
    assert result.returncode == 3
    assert "DEEPSEEK_API_KEY" in result.stderr


def test_too_long_text_exits_2(tmp_path, server):
    out = tmp_path / "speech.wav"
    result = run(["tts", "--text", "汉" * 5001, "--out", str(out)], base_url=base(server))
    assert result.returncode == 2
    assert "10000" in result.stderr or "10,000" in result.stderr


def test_retry_on_429(tmp_path, server):
    server.fail_tts_once = True
    out = tmp_path / "speech.wav"
    result = run(["tts", "--text", "你好", "--out", str(out)], base_url=base(server))
    assert result.returncode == 0, result.stderr
    assert server.tts_count == 2


def test_invalid_audio_exits_5_or_7(tmp_path, server):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not audio" * 50)
    result = run(["asr", "--in", str(bad), "--stdout"], base_url=base(server))
    assert result.returncode in (5, 7)
    assert result.stderr.strip()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_tts_mp3(tmp_path, server):
    out = tmp_path / "speech.mp3"
    result = run(["tts", "--text", "你好", "--out", str(out), "--format", "mp3"],
                 base_url=base(server))
    assert result.returncode == 0, result.stderr
    assert out.stat().st_size > 100
