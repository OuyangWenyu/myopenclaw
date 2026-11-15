"""Static wiring guards for the Hermes voice capability."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "docker" / "hermes" / "voice"


def _load():
    spec = importlib.util.spec_from_file_location("voice_config", VOICE_DIR / "voice_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load()


def test_image_ships_the_capability():
    dockerfile = (ROOT / "docker" / "hermes" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY voice/ /opt/hermes-voice/" in dockerfile
    assert (VOICE_DIR / "ds_voice.py").is_file()
    assert (VOICE_DIR / "voice_config.py").is_file()


def test_image_allows_the_auto_tts_temp_dir():
    dockerfile = (ROOT / "docker" / "hermes" / "Dockerfile").read_text(encoding="utf-8")
    assert "HERMES_WRITE_SAFE_ROOT=/opt/data:/tmp/hermes_voice" in dockerfile


def test_fragment_uses_the_image_path_and_single_key():
    fragment = vc.render_fragment(activate=True, auto_tts="true", voice="stella")
    assert "/opt/hermes-voice/ds_voice.py tts" in fragment
    assert "/opt/hermes-voice/ds_voice.py asr" in fragment
    assert "/opt/data/bin" not in fragment
    assert "DEEPSEEK_API_KEY" in fragment
    assert "env_passthrough:" in fragment


def test_start_sh_wires_all_hermes_configs():
    start = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert "voice_config.py" in start
    assert "hermes/profiles/*/config.yaml" in start
    assert "hermes-agents/*/profiles/*/config.yaml" in start
    assert "--auto-tts true" in start


def test_bind_agent_installs_the_capability():
    bind = (ROOT / "scripts" / "bind_agent.py").read_text(encoding="utf-8")
    assert "ensure_voice_config" in bind
    assert "auto_tts=\"true\"" in bind or "auto_tts='true'" in bind


def test_docs_are_published():
    assert (ROOT / "docs" / "voice.md").is_file()
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "voice.md" in mkdocs
