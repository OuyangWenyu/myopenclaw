"""TDAI 共享记忆接线守卫（含道元）。

背景（2026-11-11）：道元此前服务于公共用户，刻意不接 TDAI 共享记忆，只留 Hermes
内置记忆。飞书收紧为 owner-only（``DAOYUAN_FEISHU_ALLOWED_USERS`` 单条 open_id +
``FEISHU_GROUP_POLICY=allowlist``）后，道元的讨论也纳入共享记忆。

两个坑，各有断言：

1. **只加 env 不够**：provider 注入原本只认 ``provider: ''``（Hermes 默认配置的形状）。
   道元 profile 是 ``start.sh`` 手写的最小配置，``memory:`` 段里只有
   ``memory_enabled: true``，于是每次启动都走 else 分支静默跳过 —— 容器日志里是
   ``⚠️ /opt/data/profiles/daoyuan: 未找到可替换的 provider 字段，跳过``。
2. **只改 start.sh 模板也不够**：heredoc 只在文件不存在时生效，已有机器（含从快照
   恢复的）不会重建配置，必须由 entrypoint 幂等补齐。

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_memory_wiring.py -v
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
WRAPPER = REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
START_SH = REPO_ROOT / "scripts" / "start.sh"

HERMES_SERVICES = ("hermes", "hermes-coder", "hermes-daoyuan", "hermes-finance")

DAOYUAN_MINIMAL = """\
model:
  default: deepseek-flash
  provider: deepseek
memory:
  memory_enabled: true
onboarding:
  seen:
    profile_build_offered: true
cron:
  wrap_response: false
"""

HERMES_DEFAULT = """\
memory:
  memory_enabled: true
  user_profile_enabled: true
  provider: ''
delegation:
  provider: ''
"""


def _injection_script() -> str:
    """从 entrypoint-wrapper 提取 provider 注入的 python 片段。"""
    m = re.search(r"python3 - \"\$\{config\}\" << 'PYEOF'\n(.*?)\nPYEOF", WRAPPER.read_text(), re.S)
    assert m, "entrypoint-wrapper 中未找到 provider 注入的 PYEOF 片段（锚点被改名？）"
    return m.group(1)


def _run_injection(config: Path) -> str:
    script = config.parent / "_inject.py"
    script.write_text(_injection_script())
    out = subprocess.run(
        [sys.executable, str(script), str(config)],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture()
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


# ── compose：每个 hermes profile 都要拿到 Gateway 地址 ──────────────
class TestComposeGatewayEnv:
    @pytest.mark.parametrize("svc", HERMES_SERVICES)
    def test_gateway_host_port_present(self, services: dict, svc: str) -> None:
        env = services[svc]["environment"]
        assert "MEMORY_TENCENTDB_GATEWAY_HOST=tdai-memory" in env, (
            f"{svc} 缺 MEMORY_TENCENTDB_GATEWAY_HOST —— provider 会退回 127.0.0.1:8420"
        )
        assert "MEMORY_TENCENTDB_GATEWAY_PORT=8420" in env

    def test_daoyuan_no_longer_claimed_isolated(self) -> None:
        """注释别悄悄回退成「道元不共享」。"""
        text = COMPOSE.read_text()
        assert "道元不共享 TDAI memory" not in text


# ── entrypoint 注入逻辑：三种配置形状 + 幂等 ─────────────────────────
class TestProviderInjection:
    def test_inserts_missing_provider_into_minimal_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(DAOYUAN_MINIMAL)

        assert _run_injection(cfg) == "injected"

        data = yaml.safe_load(cfg.read_text())
        assert data["memory"] == {
            "provider": "memory_tencentdb",
            "memory_enabled": True,
        }
        # 其它段不受影响
        assert data["model"]["default"] == "deepseek-flash"
        assert data["cron"] == {"wrap_response": False}

    def test_replaces_empty_hermes_default_provider(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(HERMES_DEFAULT)

        assert _run_injection(cfg) == "injected"

        data = yaml.safe_load(cfg.read_text())
        assert data["memory"]["provider"] == "memory_tencentdb"
        # delegation.provider 不得被误伤（section 限定）
        assert data["delegation"]["provider"] == ""

    def test_keeps_operator_chosen_provider(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("memory:\n  provider: local\n  memory_enabled: true\n")

        assert _run_injection(cfg) == "kept"
        assert yaml.safe_load(cfg.read_text())["memory"]["provider"] == "local"

    def test_missing_memory_section_is_reported_not_appended(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        original = "model:\n  default: deepseek-flash\n"
        cfg.write_text(original)

        assert _run_injection(cfg) == "no-section"
        assert cfg.read_text() == original

    def test_idempotent(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(DAOYUAN_MINIMAL)

        _run_injection(cfg)
        first = cfg.read_text()
        _run_injection(cfg)

        assert cfg.read_text() == first
        assert yaml.safe_load(first)["memory"]["provider"] == "memory_tencentdb"


# ── start.sh：新机器首建的道元配置就得带上 provider ────────────────
class TestStartShTemplate:
    def test_daoyuan_template_sets_provider(self) -> None:
        text = START_SH.read_text()
        m = re.search(
            r'cat > "\$\{DAOYUAN_CONFIG\}" << \'YAML\'\n(.*?)\nYAML', text, re.S
        )
        assert m, "start.sh 中未找到道元 profile 的 heredoc"
        data = yaml.safe_load(m.group(1))
        assert data["memory"]["provider"] == "memory_tencentdb"
        assert data["memory"]["memory_enabled"] is True
