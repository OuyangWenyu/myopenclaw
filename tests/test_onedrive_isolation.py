"""compose 守卫：OneDrive 的 rclone 配置只许挂给默认 profile（hermes / 爱玛士）。

背景（2026-10-30 定案）：个人 Microsoft 账号（outlook.com）用不了 M365 插件
（application 模式要 Entra 租户，且插件 pin `requires_hermes >=0.21.3`，本机
v0.21.2 连装都过不了），改走 rclone。四个 hermes 容器共享 `/opt/data`，所以
OneDrive token **不能**写共享的 `~/.hermes/rclone/rclone.conf` —— 那等于把个人
OneDrive 交给 coder / daoyuan / finance。实际做法：宿主机
`~/.hermes-onedrive/rclone.conf`（gdrive + onedrive，600 权限）只挂载给
`hermes` 容器为 `/opt/onedrive`，并把它的 `RCLONE_CONFIG` 指过去；其余 profile
继续用共享 `/opt/data/rclone/rclone.conf`（只有 gdrive）。

本测试钉住：
① 默认 hermes 必须挂载 ~/.hermes-onedrive 且 RCLONE_CONFIG 指向 /opt/onedrive；
② 其余三个 profile 不许挂载该目录、RCLONE_CONFIG 仍是共享路径；
③ 任何服务都不许再出现 RCLONE_CONFIG_ONEDRIVE_* 环境变量（旧的 env-remote
   方案会被 rclone 把空 `[onedrive]` 段写回共享配置，已废弃）。

Run: uv run --with pytest --with pyyaml pytest tests/test_onedrive_isolation.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"

DEFAULT_PROFILE = "hermes"
OTHER_PROFILES = ("hermes-coder", "hermes-daoyuan", "hermes-finance")


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _env(services: dict, name: str) -> list[str]:
    return [e for e in (services[name].get("environment") or []) if isinstance(e, str)]


def _volumes(services: dict, name: str) -> list[str]:
    return [v for v in (services[name].get("volumes") or []) if isinstance(v, str)]


class TestOneDriveIsolation:
    def test_default_profile_mounts_private_config(self, services: dict):
        vols = _volumes(services, DEFAULT_PROFILE)
        assert any("${HOME}/.hermes-onedrive:/opt/onedrive" in v for v in vols)
        assert "RCLONE_CONFIG=/opt/onedrive/rclone.conf" in _env(services, DEFAULT_PROFILE)

    @pytest.mark.parametrize("name", OTHER_PROFILES)
    def test_other_profiles_do_not_mount_private_config(self, services: dict, name: str):
        assert not any(".hermes-onedrive" in v for v in _volumes(services, name)), (
            f"{name} 不能挂载 OneDrive 私有配置（2026-10-30 用户明确要求："
            "只有默认 profile 爱玛士可以访问）"
        )
        assert "RCLONE_CONFIG=/opt/data/rclone/rclone.conf" in _env(services, name)

    def test_no_legacy_env_remote_anywhere(self, services: dict):
        for name in services:
            entries = _env(services, name)
            assert not any(e.startswith("RCLONE_CONFIG_ONEDRIVE_") for e in entries), (
                f"{name} 不应再使用 env-remote（rclone 会把空段写回共享配置）"
            )
