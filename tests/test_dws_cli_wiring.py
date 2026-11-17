"""compose 守卫：钉钉 CLI（dws）只给默认 profile（爱玛士）。

背景（2026-11-16 用户明确要求）：钉钉管理员开通 CLI 权限后，**只要 Hermes 这
一个 agent** 具备钉钉能力，且必须按仓库现有框架接线 —— 镜像装二进制、manifest
声明 env/mount、start.sh 链 skill，不手工改容器。

隔离判据（与 OneDrive / 邮箱同一套原则）：四个 hermes 容器共享 `/opt/data`，
所以个人钉钉 token **不能**落在 `$HOME/.dws`（= `/opt/data/.dws`）—— 那等于把
token 交给 coder / 道元 / finance。实际做法：宿主机 `~/.dws-cli` 只挂给 `hermes`
为 `/opt/dws-config`，并只在该服务的 env 里设 `DWS_CONFIG_DIR`。其余 profile
既无 token 目录也无 `DWS_CONFIG_DIR`，`dws` 二进制虽在镜像里却调不通。

本测试钉住：
① 镜像 pin 住 `dingtalk-workspace-cli@<v>`，且该版本满足 skill 声明的下限；
② 只有 `hermes` 挂 `~/.dws-cli` + `./skills/dws`，且只有它设 `DWS_CONFIG_DIR`；
③ 其余三个 profile 不挂、不设；
④ skill 源码只有仓库这份（`skills/dws`），start.sh 只链进默认 profile；
⑤ 文档与 mkdocs nav 有对应条目（免得下一轮排查又靠口头知识）。

Run: uv run --with pytest --with pyyaml pytest tests/test_dws_cli_wiring.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "docker" / "hermes" / "Dockerfile"
START_SH = REPO_ROOT / "scripts" / "start.sh"
SKILL_DIR = REPO_ROOT / "skills" / "dws"
DOC = REPO_ROOT / "docs" / "dingtalk-cli.md"

DEFAULT_PROFILE = "hermes"
OTHER_PROFILES = ("hermes-coder", "hermes-daoyuan", "hermes-finance")

NPM_PACKAGE = "dingtalk-workspace-cli"
_PIN_RE = re.compile(rf"{NPM_PACKAGE}@(\d+\.\d+\.\d+)")
_SKILL_MIN_RE = re.compile(r'cli_version:\s*"([><=~^]*)\s*(\d+\.\d+\.\d+)"')


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _env(services: dict, name: str) -> list[str]:
    return [e for e in (services[name].get("environment") or []) if isinstance(e, str)]


def _volumes(services: dict, name: str) -> list[str]:
    return [v for v in (services[name].get("volumes") or []) if isinstance(v, str)]


class TestImageInstall:
    def test_npm_version_is_pinned(self):
        pins = _PIN_RE.findall(DOCKERFILE.read_text())
        assert pins, f"Dockerfile 必须 pin `npm install -g {NPM_PACKAGE}@<v>`"
        assert len(set(pins)) == 1, f"pin 出现多份且不一致: {sorted(set(pins))}"

    def test_pin_satisfies_skill_requirement(self):
        """skill 自带 `cli_version` 下限；pin 低了 skill 的 --help 守卫就是假的。"""
        pinned = _parse(_PIN_RE.findall(DOCKERFILE.read_text())[0])
        match = _SKILL_MIN_RE.search((SKILL_DIR / "SKILL.md").read_text())
        assert match, "skills/dws/SKILL.md 缺少 cli_version 声明（上游改了格式？）"
        op, lower = match.group(1), _parse(match.group(2))
        assert op in ("", ">="), f"未知的 cli_version 约束: {op}"
        assert pinned >= lower, f"pin {pinned} 低于 skill 要求的 {lower}"


class TestOnlyDefaultProfile:
    def test_default_profile_mounts_token_dir_and_skill(self, services: dict):
        vols = _volumes(services, DEFAULT_PROFILE)
        assert any(v.endswith(".dws-cli:/opt/dws-config") for v in vols), (
            "默认 profile 必须挂载 ~/.dws-cli → /opt/dws-config（token 持久化）"
        )
        assert any("skills/dws:/opt/hermes-skills/dws" in v for v in vols), (
            "默认 profile 必须挂载 skills/dws 供 start.sh 链进 ~/.hermes/skills"
        )

    def test_default_profile_sets_config_dir(self, services: dict):
        assert "DWS_CONFIG_DIR=/opt/dws-config" in _env(services, DEFAULT_PROFILE), (
            "默认 profile 必须设 DWS_CONFIG_DIR；否则 dws 落到 /opt/data 共享卷"
        )

    def test_default_profile_sets_keychain_dir(self, services: dict):
        """token 真正落在 DWS_KEYCHAIN_DIR，不是 DWS_CONFIG_DIR。

        2026-11-16 实测：dws 把凭据写进 `$DWS_KEYCHAIN_DIR/dws-cli/`，默认是
        `$HOME/.local/share/dws-cli` —— 容器里就是 /root，重建即丢；而
        DWS_CONFIG_DIR 只放 app.json / identity / logs / cache。只设前者的现象
        是「登录成功、重建后变未登录」，所以两个都必须钉在挂载卷上。
        """
        assert "DWS_KEYCHAIN_DIR=/opt/dws-config/keychain" in _env(
            services, DEFAULT_PROFILE
        ), "默认 profile 必须设 DWS_KEYCHAIN_DIR，否则 token 写进 /root 且重建即丢"

    @pytest.mark.parametrize("name", OTHER_PROFILES)
    def test_other_profiles_have_no_dingtalk_access(self, services: dict, name: str):
        vols = _volumes(services, name)
        assert not any(".dws-cli" in v for v in vols), (
            f"{name} 不能挂载钉钉 token 目录（2026-11-16：只有爱玛士可以用钉钉 CLI）"
        )
        assert not any("skills/dws" in v for v in vols), (
            f"{name} 不应挂载 dws skill"
        )
        # 目录变量可以存在，但只能指向容器内 /tmp：它们的 $HOME=/opt/data 是四个
        # profile 共享的数据卷（还进云备份），随手跑一次 dws 往里写 dek/cache
        # 就是跨 profile 残留（2026-11-16 实测发现过）。
        for entry in _env(services, name):
            key, _, value = entry.partition("=")
            if key not in ("DWS_CONFIG_DIR", "DWS_KEYCHAIN_DIR"):
                continue
            assert value.startswith("/tmp/"), (
                f"{name} 的 {key}={value} 必须是容器内 /tmp（一次性）；"
                "指向 /opt/data 会污染共享数据卷，指向 /opt/dws-config 等于偷 token"
            )


class TestSharedVolumeHygiene:
    """其余 profile 必须挂 `dws-off` bundle，把 dws 状态钉在 /tmp。

    没有这个 bundle 时 dws 用 `$HOME/.dws` / `$HOME/.local/share/dws-cli`，
    而 $HOME=/opt/data 是四个容器共享、且进云备份（`hermes/scripts/backup.sh`）
    的数据卷 —— coder 里随手一次 `dws` 调用就会把 dek 和 1.5MB 工具缓存写进去。
    """

    def test_off_bundle_targets_tmp(self):
        base = (REPO_ROOT / "agents" / "hermes" / "base.yaml").read_text()
        match = re.search(
            r"^  dws-off:\n((?:    [A-Z_]+: .*\n)+)", base, re.M
        )
        assert match, "base.yaml 必须定义 dws-off bundle"
        block = match.group(1)
        assert "DWS_CONFIG_DIR: /tmp/" in block and "DWS_KEYCHAIN_DIR: /tmp/" in block, (
            "dws-off 必须把两个目录都指到 /tmp"
        )

    @pytest.mark.parametrize("agent", ("coder", "daoyuan", "finance"))
    def test_off_bundle_is_applied(self, agent: str):
        manifest = (REPO_ROOT / "agents" / "hermes" / f"{agent}.yaml").read_text()
        assert re.search(r"^  - dws-off$", manifest, re.M), (
            f"{agent}.yaml 的 bundles 必须包含 dws-off（否则会往共享卷里写 dws 状态）"
        )


class TestSkillWiring:
    def test_skill_source_is_versioned_in_repo(self):
        skill = SKILL_DIR / "SKILL.md"
        assert skill.is_file(), "仓库里必须有 skills/dws（唯一一份源码，按 CLI 版本落盘）"
        text = skill.read_text()
        assert re.search(r"^name:\s*dws\s*$", text, re.M), "skill 名必须是 dws"
        products = SKILL_DIR / "references" / "products"
        assert len(list(products.glob("*.md"))) >= 15, (
            "官方 skill 的 17 个产品参考文件应完整落盘（分批拷贝会漏）"
        )

    def test_start_sh_links_skill_into_default_profile_only(self):
        text = START_SH.read_text()
        assert re.search(
            r'ensure_skill_symlink\s+"\$\{HOME\}/\.hermes/skills/dws"\s+'
            r'"/opt/hermes-skills/dws"',
            text,
        ), "start.sh 必须把 dws skill 链进 ~/.hermes/skills（默认 profile）"
        for profile in ("coder", "daoyuan", "finance"):
            assert f"profiles/{profile}/skills/dws" not in text, (
                f"不得把 dws skill 链进 {profile} —— 只有爱玛士有钉钉能力"
            )

    def test_start_sh_creates_host_token_dir(self):
        assert '"${HOME}/.dws-cli"' in START_SH.read_text(), (
            "start.sh 必须在 compose up 前建 ~/.dws-cli，否则 Docker 以 root 建目录"
        )


class TestDocs:
    def test_page_records_scoping_and_auth(self):
        assert DOC.is_file(), "缺少 docs/dingtalk-cli.md"
        text = DOC.read_text()
        for needle in ("DWS_CONFIG_DIR", "dws auth login --device", "~/.dws-cli", "爱玛士"):
            assert needle in text, f"文档必须交代 {needle}"

    def test_mkdocs_nav_and_claude_md_reference_the_page(self):
        assert "dingtalk-cli.md" in (REPO_ROOT / "mkdocs.yml").read_text()
        assert "dingtalk-cli.md" in (REPO_ROOT / "CLAUDE.md").read_text()
