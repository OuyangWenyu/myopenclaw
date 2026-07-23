#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml


HELPER = Path(__file__).resolve().parents[1] / "docker" / "hermes" / "configure-yuque-mcp.py"


def run(home: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["MCP_YUQUE_MCP_API_KEY"] = "fixture-key"
    env["YUQUE_SKILL_SOURCE"] = str(home / "managed-skill-source")
    if os.name == "nt":
        env["YUQUE_MANAGE_SKILL_LINK"] = "false"
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        (home / "config.yaml").write_text("existing:\n  keep: true\n", encoding="utf-8")
        (home / "managed-skill-source").mkdir()
        run(home)
        first = (home / "config.yaml").read_bytes()
        run(home)
        assert (home / "config.yaml").read_bytes() == first
        config = yaml.safe_load(first)
        server = config["mcp_servers"]["yuque-mcp"]
        assert server["managed_by"] == "myopenclaw"
        assert server["timeout"] == 900
        assert config["existing"]["keep"] is True
        assert (home / ".env").read_text().count("MCP_YUQUE_MCP_API_KEY=") == 1
        skill_link = home / "skills" / "yuque-knowledge"
        if os.name != "nt":
            assert skill_link.is_symlink()
            assert skill_link.resolve() == (home / "managed-skill-source").resolve()
        run(home, "--disable")
        disabled = yaml.safe_load((home / "config.yaml").read_text())
        assert "yuque-mcp" not in disabled.get("mcp_servers", {})
        assert "MCP_YUQUE_MCP_API_KEY=" not in (home / ".env").read_text()
        if os.name != "nt":
            assert not skill_link.exists() and not skill_link.is_symlink()

    if os.name != "nt":
      with tempfile.TemporaryDirectory() as raw:
          home = Path(raw)
          (home / "managed-skill-source").mkdir()
          user_skill = home / "skills" / "yuque-knowledge"
          user_skill.mkdir(parents=True)
          (user_skill / "USER").write_text("keep", encoding="utf-8")
          run(home, "--disable")
          assert (user_skill / "USER").read_text(encoding="utf-8") == "keep"

      with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        (home / "managed-skill-source").mkdir()
        other = home / "other-skill"
        other.mkdir()
        skill_link = home / "skills" / "yuque-knowledge"
        skill_link.parent.mkdir(parents=True)
        skill_link.symlink_to(other, target_is_directory=True)
        run(home, "--disable")
        assert skill_link.is_symlink() and skill_link.resolve() == other.resolve()

    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        original = "mcp_servers:\n  yuque-mcp:\n    url: http://user.example/sse\nother: keep\n"
        (home / "config.yaml").write_text(original, encoding="utf-8")
        result = run(home, check=False)
        assert result.returncode != 0
        assert (home / "config.yaml").read_text(encoding="utf-8") == original
        run(home, "--disable")
        assert (home / "config.yaml").read_text(encoding="utf-8") == original

    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        original = "- invalid\n- config\n"
        (home / "config.yaml").write_text(original, encoding="utf-8")
        result = run(home, check=False)
        assert result.returncode != 0
        assert (home / "config.yaml").read_text(encoding="utf-8") == original
        assert not (home / ".env").exists()

    print("Hermes Yuque lifecycle fixtures passed")


if __name__ == "__main__":
    main()
