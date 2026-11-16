"""Unit tests for docker/hermes/voice/voice_config.py.

The injector is stdlib-only in production; these tests use PyYAML only to
prove the edited documents are still valid YAML.
"""

import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "docker" / "hermes" / "voice"


def _load():
    spec = importlib.util.spec_from_file_location("voice_config", VOICE_DIR / "voice_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load()


BASE = """\
# keep this comment
model:
  default: deepseek-flash
  provider: deepseek
tts:
  provider: edge
  edge:
    voice: en-US-AriaNeural
stt:
  enabled: true
  local:
    model: base
  provider: local
voice:
  record_key: ctrl+b
  auto_tts: false
"""

LEGACY = """\
tts:
  provider: edge
  providers:
    deepseek:
      type: command
      command: "python3 /opt/data/bin/ds_tts.py --text-file {input_path} --out {output_path} --format {format} --voice {voice}"
      output_format: wav
      voice: tide
      voice_compatible: true
      max_text_length: 4000
      timeout: 180
      env_passthrough:
      - OLD_KEY
stt:
  enabled: true
  providers:
    deepseek:
      type: command
      command: "python3 /opt/data/bin/ds_asr.py --in {input_path} --out {output_path}"
      format: txt
      normalize: false
      timeout: 300
      env_passthrough:
      - OLD_KEY
  provider: local
"""


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_appends_capability_when_missing(tmp_path):
    path = _write(tmp_path, "model:\n  default: deepseek-flash\n")
    status = vc.ensure_voice_config(path, activate=True, backup=False)
    assert status.startswith("written")
    data = _load_yaml(path)
    assert data["tts"]["provider"] == "deepseek"
    assert data["stt"]["provider"] == "deepseek"
    assert data["stt"]["enabled"] is True
    assert data["tts"]["providers"]["deepseek"]["env_passthrough"] == ["DEEPSEEK_API_KEY"]
    assert data["stt"]["providers"]["deepseek"]["env_passthrough"] == ["DEEPSEEK_API_KEY"]


def test_replaces_legacy_defaults_and_preserves_other_settings(tmp_path):
    path = _write(tmp_path, BASE)
    status = vc.ensure_voice_config(path, activate=True, backup=False)
    assert "tts" in status and "stt" in status
    data = _load_yaml(path)
    assert data["tts"]["provider"] == "deepseek"
    assert data["stt"]["provider"] == "deepseek"
    assert data["tts"]["edge"]["voice"] == "en-US-AriaNeural"
    assert data["stt"]["local"]["model"] == "base"
    assert data["voice"]["record_key"] == "ctrl+b"


def test_migrates_legacy_provider_block(tmp_path):
    path = _write(tmp_path, LEGACY)
    status = vc.ensure_voice_config(path, activate=True, backup=False)
    assert status.startswith("written")
    data = _load_yaml(path)
    tts = data["tts"]["providers"]["deepseek"]
    stt = data["stt"]["providers"]["deepseek"]
    assert "/opt/hermes-voice/ds_voice.py tts" in tts["command"]
    assert "/opt/hermes-voice/ds_voice.py asr" in stt["command"]
    assert tts["env_passthrough"] == ["DEEPSEEK_API_KEY"]
    assert stt["env_passthrough"] == ["DEEPSEEK_API_KEY"]
    assert "normalize" not in stt
    assert tts["voice"] == "tide"
    assert tts["output_format"] == "wav"
    assert data["tts"]["provider"] == "deepseek"
    assert data["stt"]["provider"] == "deepseek"


def test_preserves_explicit_operator_provider(tmp_path):
    text = """\
tts:
  provider: elevenlabs
  providers:
    elevenlabs:
      type: command
      command: "say {input_path}"
stt:
  provider: openai
"""
    path = _write(tmp_path, text)
    vc.ensure_voice_config(path, activate=True, backup=False)
    data = _load_yaml(path)
    assert data["tts"]["provider"] == "elevenlabs"
    assert data["stt"]["provider"] == "openai"
    assert data["tts"]["providers"]["elevenlabs"]["command"] == "say {input_path}"
    assert data["tts"]["providers"]["deepseek"]["env_passthrough"] == ["DEEPSEEK_API_KEY"]


def test_stt_disabled_is_not_touched(tmp_path):
    text = """\
stt:
  enabled: false
  provider: local
"""
    path = _write(tmp_path, text)
    vc.ensure_voice_config(path, activate=True, backup=False)
    data = _load_yaml(path)
    assert data["stt"]["enabled"] is False
    assert data["stt"]["provider"] == "local"
    assert "providers" not in data["stt"]


def test_inline_section_is_refused_without_writing(tmp_path):
    text = "tts: {provider: edge}\n"
    path = _write(tmp_path, text)
    status = vc.ensure_voice_config(path, activate=True, backup=False)
    assert status.startswith("error:inline-section")
    assert path.read_text(encoding="utf-8") == text


def test_idempotent(tmp_path):
    path = _write(tmp_path, BASE)
    first = vc.ensure_voice_config(path, activate=True, auto_tts="true", backup=False)
    assert first.startswith("written")
    after_first = path.read_text(encoding="utf-8")
    second = vc.ensure_voice_config(path, activate=True, auto_tts="true", backup=False)
    assert second == "unchanged"
    assert path.read_text(encoding="utf-8") == after_first


def test_comments_and_crlf_are_preserved(tmp_path):
    text = "# keep this comment\r\nmodel:\r\n  default: deepseek-flash\r\n"
    path = _write(tmp_path, text)
    vc.ensure_voice_config(path, activate=True, backup=False)
    with open(path, "r", encoding="utf-8", newline="") as handle:
        edited = handle.read()
    assert "# keep this comment" in edited
    assert "\r\n" in edited
    assert "\n" not in edited.replace("\r\n", "")
    _load_yaml(path)


def test_auto_tts_preserve_and_set(tmp_path):
    path = _write(tmp_path, BASE)
    vc.ensure_voice_config(path, activate=True, auto_tts="preserve", backup=False)
    assert _load_yaml(path)["voice"]["auto_tts"] is False
    vc.ensure_voice_config(path, activate=True, auto_tts="true", backup=False)
    assert _load_yaml(path)["voice"]["auto_tts"] is True


def test_check_detects_legacy_and_canonical(tmp_path):
    path = _write(tmp_path, LEGACY)
    assert vc._check_text(path.read_text(encoding="utf-8"))
    vc.ensure_voice_config(path, activate=True, backup=False)
    assert vc._check_text(path.read_text(encoding="utf-8")) == []
