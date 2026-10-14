"""守卫：飞书聊天体验补丁（docker/hermes/feishu_markdown_card.py + patch-feishu.py）。

两块补丁（同一手术脚本，构建时应用、锚点失配即构建失败）：

① **markdown 回复卡片化**：飞书 ``post`` + ``md`` 渲染器对 GFM 表格原样露出
   生管道符源码、标题降级为普通文字（实测：向 DM 发探针 → API 拉回服务端存储
   逐行 dump）。卡片（interactive, Card JSON 2.0）能真渲染。上游 PR #21000/
   #29630 均未 merge ⇒ 构建时内联纯函数模块 + 改写 adapter.py 四处锚点。
② **话题（omt_ thread）回复锚点**：gateway 只给 Telegram/Slack 填
   ``metadata["reply_to_message_id"]``；飞书缺失时适配器把 thread_id 当
   receive_id 发（无效字段 → 99992402），**bot 在话题里的回复永远送不出去**
   （2026-10-14 实测：无锚点 99992402 / 补锚点成功，文本与卡片均验证）。

本测试钉住三块不变量：
① 纯函数模块行为（payload 形状 / 表格上限 4 / kill switch 矩阵 / 超限返回 None）
② 手术脚本：两种骨架（adapter + gateway）可应用、二次运行幂等、任一锚点漂移
   非零退出且**两文件都不被写**（原子性）
③ compose 四个 hermes 服务都透传 FEISHU_MARKDOWN_CARD
容器侧行为守卫另见 tests/test-feishu-card-patch.sh。

Run: uv run --with pytest --with pyyaml pytest tests/test_feishu_markdown_card.py -v
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "docker" / "hermes" / "feishu_markdown_card.py"
PATCHER_PATH = REPO_ROOT / "docker" / "hermes" / "patch-feishu.py"
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def card_mod():
    return _load(MODULE_PATH, "feishu_markdown_card")


@pytest.fixture(scope="module")
def patcher():
    return _load(PATCHER_PATH, "patch_feishu")


def _table(rows: int = 1) -> str:
    lines = ["| 表头1 | 表头2 |", "|---|---|"]
    lines += [f"| A{i} | B{i} |" for i in range(rows)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ① 纯函数模块
# ---------------------------------------------------------------------------


class TestCardPayloadBuilder:
    def test_card_payload_shape_preserves_markdown_verbatim(self, card_mod):
        md = "## 标题\n\n**粗体** / *斜体*\n\n" + _table()
        payload = card_mod.build_markdown_card_payload(md, env={})
        assert payload is not None
        body = json.loads(payload)
        assert body["schema"] == "2.0"
        # 2.0 要求 update_multi 恒为 true（飞书文档：暂时仅支持设为 true）
        assert body["config"] == {"update_multi": True}
        assert body["body"]["elements"] == [{"tag": "markdown", "content": md}]

    def test_markdown_without_tables_still_cardifies(self, card_mod):
        assert card_mod.build_markdown_card_payload("**hi** *x*\n- a\n- b\n", env={}) is not None

    @pytest.mark.parametrize("n", [1, 2, 3, 4])
    def test_up_to_four_tables_cardify(self, card_mod, n):
        md = "\n\n".join(_table() for _ in range(n))
        assert card_mod.build_markdown_card_payload(md, env={}) is not None

    def test_five_tables_return_none(self, card_mod):
        # 飞书文档：单个富文本组件中最多 4 个表格 → 超限回落 post（无回归）
        md = "\n\n".join(_table() for _ in range(5))
        assert card_mod.build_markdown_card_payload(md, env={}) is None

    def test_table_inside_code_fence_not_counted(self, card_mod):
        fenced = "```\n" + "\n\n".join(_table() for _ in range(6)) + "\n```\n\n" + _table()
        # 围栏内 6 个"表格"是字面文本；真表格只有 1 个 → 仍卡片化
        assert card_mod.build_markdown_card_payload(fenced, env={}) is not None

    def test_pipe_row_without_separator_is_not_a_table(self, card_mod):
        md = "| a | b |\n| c | d |\n"  # 没有 |---| 分隔行 → 不是 GFM 表格
        assert card_mod.build_markdown_card_payload(md, env={}) is not None

    def test_empty_and_whitespace_return_none(self, card_mod):
        assert card_mod.build_markdown_card_payload("", env={}) is None
        assert card_mod.build_markdown_card_payload("   \n\t\n", env={}) is None

    @pytest.mark.parametrize("value", ["0", "off", "false", "no", "disabled", " OFF ", "False"])
    def test_kill_switch_disabled_values(self, card_mod, value):
        assert card_mod.build_markdown_card_payload("**hi**", env={card_mod.MARKDOWN_CARD_ENV: value}) is None

    def test_kill_switch_default_on(self, card_mod):
        assert card_mod.markdown_cards_enabled(env={}) is True
        assert card_mod.build_markdown_card_payload("**hi**", env={}) is not None

    @pytest.mark.parametrize("value", ["1", "on", "yes", "true", "ON"])
    def test_kill_switch_enabled_values(self, card_mod, value):
        assert card_mod.build_markdown_card_payload("**hi**", env={card_mod.MARKDOWN_CARD_ENV: value}) is not None

    def test_oversized_payload_returns_none(self, card_mod):
        big = "字" * 12000  # ~36KB UTF-8 > 28000 → 回落 post
        assert card_mod.build_markdown_card_payload(big, env={}) is None
        assert card_mod.build_markdown_card_payload("字" * 100, env={}) is not None

    def test_never_raises_on_pathological_input(self, card_mod):
        for bad in (None, 123, object(), b"bytes", "\x00" * 10, "```\nunclosed fence"):
            result = card_mod.build_markdown_card_payload(bad, env={})
            assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# ② 手术脚本（锚点以 patcher 模块常量为准 —— 测试即手术的 spec）
# ---------------------------------------------------------------------------


def _adapter_skeleton(patcher) -> str:
    """最小 adapter 骨架，内嵌四个真实锚点（与 adapter.py 同缩进深度）。"""
    return "".join(
        [
            "import json\n",
            "import re\n",
            "\n",
            "\n",
            '_MULTISPACE_RE = re.compile(r"[ \\t]{2,}")\n',
            patcher.A1_OLD,
            "\n",
            "def _build_markdown_post_rows(content):\n",
            "    return []\n",
            "\n",
            "\n",
            patcher.A2_OLD,
            "\n",
            "\n",
            "class Fin:\n",
            "    async def send(self):\n",
            "        try:\n",
            "            pass\n",
            "        except Exception:\n",
            "            pass\n",
            patcher.A3_OLD,
            # A3 区域结束于外层 try 的中段（真实文件里后面还有 return/except）——补齐让骨架语法闭合
            "            return last_response\n",
            "        except Exception as exc:\n",
            "            raise\n",
            "\n",
            "    def _retry(self):\n",
            "        for attempt in range(3):\n",
            "            try:\n",
            "                pass\n",
            "            except Exception as exc:\n",
            "                last_error = exc\n",
            patcher.A4_OLD,
            "                    raise\n",
            "                if attempt >= 2:\n",
            "                    raise\n",
        ]
    )


def _gateway_skeleton(patcher) -> str:
    """最小 gateway 骨架，内嵌 A5 锚点。"""
    return "".join(
        [
            "from enum import Enum\n",
            "\n",
            "\n",
            "class Platform(Enum):\n",
            "    SLACK = 'slack'\n",
            "    FEISHU = 'feishu'\n",
            "\n",
            "\n",
            "class GatewayRunner:\n",
            "    def _thread_metadata_for_target(self, platform, chat_id, thread_id, *,\n",
            "                                    chat_type=None, reply_to_message_id=None, adapter=None):\n",
            "        if thread_id is None:\n",
            "            return None\n",
            "        metadata = {'thread_id': thread_id}\n",
            patcher.A5_OLD,
            "\n",
        ]
    )


def _run_patcher(adapter_target: Path, gateway_target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(PATCHER_PATH),
            "--adapter-path", str(adapter_target),
            "--gateway-path", str(gateway_target),
            "--module", str(MODULE_PATH),
        ],
        capture_output=True,
        text=True,
    )


def _skeletons(patcher, tmp_path) -> tuple[Path, Path, str, str]:
    adapter = tmp_path / "adapter.py"
    gateway = tmp_path / "run.py"
    a_text = _adapter_skeleton(patcher)
    g_text = _gateway_skeleton(patcher)
    adapter.write_text(a_text, encoding="utf-8")
    gateway.write_text(g_text, encoding="utf-8")
    return adapter, gateway, a_text, g_text


class TestPatchScript:
    def test_skeletons_parse(self, patcher):
        ast.parse(_adapter_skeleton(patcher))
        ast.parse(_gateway_skeleton(patcher))

    def test_apply_patch_rewrites_all_anchors_in_both_files(self, patcher, tmp_path):
        adapter, gateway, _, _ = _skeletons(patcher, tmp_path)
        result = _run_patcher(adapter, gateway)
        assert result.returncode == 0, result.stderr

        out = adapter.read_text(encoding="utf-8")
        assert patcher.SENTINEL in out
        assert "def build_markdown_card_payload(markdown, env=None):" in out
        assert "card_payload = build_markdown_card_payload(chunk)" in out
        # A1/A3/A4 被替换；A2 原文保留（作为内联块的结尾）
        assert patcher.A1_OLD not in out
        assert patcher.A3_OLD not in out
        assert patcher.A4_OLD not in out
        assert patcher.A2_OLD in out
        assert patcher.A4_NEW in out
        ast.parse(out)

        gw = gateway.read_text(encoding="utf-8")
        assert patcher.TOPIC_MARKER in gw
        assert patcher.A5_OLD not in gw
        assert "if platform == Platform.FEISHU and reply_to_message_id is not None:" in gw
        ast.parse(gw)

    def test_second_run_is_idempotent(self, patcher, tmp_path):
        adapter, gateway, _, _ = _skeletons(patcher, tmp_path)
        assert _run_patcher(adapter, gateway).returncode == 0
        a_after = adapter.read_text(encoding="utf-8")
        g_after = gateway.read_text(encoding="utf-8")
        second = _run_patcher(adapter, gateway)
        assert second.returncode == 0
        assert "already patched" in second.stdout
        assert adapter.read_text(encoding="utf-8") == a_after
        assert gateway.read_text(encoding="utf-8") == g_after

    def test_adapter_anchor_drift_fails_loud_and_writes_nothing(self, patcher, tmp_path):
        adapter, gateway, _, g_text = _skeletons(patcher, tmp_path)
        mangled = _adapter_skeleton(patcher).replace("post type is incorrect", "post type is INCORRECT")
        adapter.write_text(mangled, encoding="utf-8")
        result = _run_patcher(adapter, gateway)
        assert result.returncode != 0
        assert "anchor A1" in result.stderr
        assert "drift" in result.stderr
        # 原子性：adapter 没被写、gateway 也没被写
        assert adapter.read_text(encoding="utf-8") == mangled
        assert gateway.read_text(encoding="utf-8") == g_text

    def test_gateway_anchor_drift_fails_loud_and_writes_nothing(self, patcher, tmp_path):
        adapter, gateway, a_text, _ = _skeletons(patcher, tmp_path)
        mangled = _gateway_skeleton(patcher).replace("Platform.SLACK", "Platform.SLACK!!")
        gateway.write_text(mangled, encoding="utf-8")
        result = _run_patcher(adapter, gateway)
        assert result.returncode != 0
        assert "anchor A5" in result.stderr
        assert adapter.read_text(encoding="utf-8") == a_text
        assert gateway.read_text(encoding="utf-8") == mangled

    def test_upstream_merged_card_support_refuses_to_double_patch(self, patcher, tmp_path):
        adapter, gateway, _, g_text = _skeletons(patcher, tmp_path)
        # 模拟上游自己 merge 了卡片支持（无哨兵但已引用同名函数）
        fake = _adapter_skeleton(patcher).replace(
            "def _build_markdown_post_rows(content):",
            "def build_markdown_card_payload(markdown, env=None):\n    return None\n\n\ndef _build_markdown_post_rows(content):",
        )
        adapter.write_text(fake, encoding="utf-8")
        result = _run_patcher(adapter, gateway)
        assert result.returncode != 0
        assert "merged" in result.stderr
        assert adapter.read_text(encoding="utf-8") == fake
        assert gateway.read_text(encoding="utf-8") == g_text


# ---------------------------------------------------------------------------
# ③ compose 透传 kill switch
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


class TestComposeKillSwitch:
    @pytest.mark.parametrize("name", ["hermes", "hermes-coder", "hermes-finance", "hermes-daoyuan"])
    def test_kill_switch_passed_to_all_four_profiles(self, services: dict, name: str):
        env = [e for e in (services[name].get("environment") or []) if isinstance(e, str)]
        assert "FEISHU_MARKDOWN_CARD=${FEISHU_MARKDOWN_CARD:-1}" in env, (
            f"{name} 必须透传 FEISHU_MARKDOWN_CARD（镜像级补丁的 per-container 回退开关）"
        )
