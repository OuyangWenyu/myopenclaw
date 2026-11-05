"""检查器纯函数单测（2026-10-13 补零覆盖）。

`scripts/check_server_monitoring_mcp.py` 是验收判据本身，却一直没有任何测试引用它——
改坏判定语义（比如把「缺交付工具」放过）不会有任何守卫发现。这里只测**纯函数**：
工具面判定与异常展开，不触网、不做 MCP 会话。

工具面语义（2026-10-13 与服务器现实对齐后）：
- 9 个核心运行工具 + 3 个交付/CI 工具都必须存在（AGENTS.md 纪律引用了它们）——
  缺任何一个判失败；
- 服务端新增、客户端尚未纳入策略的工具不算失败，仅在提示里列出
  （是否纳入是决策项，不是故障）。

Run: uv run --with pytest --with pyyaml pytest tests/test_check_server_monitoring_mcp.py -v
"""

from __future__ import annotations

import builtins
import importlib.util
import ssl
import sys
from pathlib import Path

import pytest

BUILTINS_EXCEPTION_GROUP = getattr(builtins, "ExceptionGroup", None)

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "scripts" / "check_server_monitoring_mcp.py"

_ALL_NINE_CORE = {
    "list_servers",
    "get_server_status",
    "list_containers",
    "get_container",
    "get_container_provenance",
    "get_prometheus_status",
    "get_project_runtime_status",
    "get_gfs_status",
    "get_runtime_failure_evidence",
}
_ALL_THREE_DELIVERY = {"list_projects", "get_project_overview", "get_project_build"}


def _load_checker():
    """按路径加载检查器模块（scripts/ 不是包，只能 importlib）。"""
    spec = importlib.util.spec_from_file_location("check_server_monitoring_mcp_under_test", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


class TestToolSets:
    def test_core_tools_are_the_nine_runtime_tools(self):
        assert set(checker.CORE_TOOLS) == _ALL_NINE_CORE

    def test_delivery_tools_are_the_three_ci_tools(self):
        assert set(checker.DELIVERY_TOOLS) == _ALL_THREE_DELIVERY

    def test_sets_are_disjoint_and_required_is_their_union(self):
        assert not (set(checker.CORE_TOOLS) & set(checker.DELIVERY_TOOLS))
        assert set(checker.REQUIRED_TOOLS) == _ALL_NINE_CORE | _ALL_THREE_DELIVERY


class TestEvaluateTools:
    def test_all_twelve_present_passes(self):
        ok, detail = checker.evaluate_tools(set(checker.REQUIRED_TOOLS))
        assert ok is True
        assert "12" in detail

    def test_missing_core_tool_fails(self):
        names = set(checker.REQUIRED_TOOLS) - {"list_containers"}
        ok, detail = checker.evaluate_tools(names)
        assert ok is False
        assert "list_containers" in detail

    def test_missing_delivery_tool_fails(self):
        """交付工具被删/改名同样要红 —— 只报"9 核心在"会静默放行。"""
        names = set(checker.REQUIRED_TOOLS) - {"get_project_build"}
        ok, detail = checker.evaluate_tools(names)
        assert ok is False
        assert "get_project_build" in detail

    def test_unknown_extra_tools_pass_with_note(self):
        """服务端升级新增工具 ≠ 故障（客户端是否纳入是决策项），仅提示。"""
        names = set(checker.REQUIRED_TOOLS) | {"get_future_thing", "another_new_tool"}
        ok, detail = checker.evaluate_tools(names)
        assert ok is True
        assert "get_future_thing" in detail
        assert "另有 2 个未纳入" in detail  # 额外个数要真钉住（"12/12" 里也有个 2，别自欺）

    def test_missing_and_extra_together_reports_both(self):
        """缺必需（红）与多未知（提示）可以同时发生 —— 两类信息都要报出来。"""
        names = (set(checker.REQUIRED_TOOLS) - {"get_gfs_status"}) | {"brand_new_tool"}
        ok, detail = checker.evaluate_tools(names)
        assert ok is False
        assert "缺核心运行工具" in detail
        assert "brand_new_tool" in detail

    def test_empty_set_fails_and_lists_both_groups(self):
        ok, detail = checker.evaluate_tools(set())
        assert ok is False
        assert "list_servers" in detail and "list_projects" in detail


class TestHintFor:
    def test_plain_connection_error_hint(self):
        hint = checker.hint_for(ConnectionRefusedError(61, "Connection refused"))
        assert "连接被拒" in hint

    @pytest.mark.skipif(BUILTINS_EXCEPTION_GROUP is None, reason="需要 Python ≥3.11 的 ExceptionGroup")
    def test_exception_group_is_unwrapped(self):
        """mcp SDK 的会话失败包成 ExceptionGroup —— 不拆包时最常见的
        连接被拒/TLS 失败只回显 'unhandled errors in a TaskGroup'，没法诊断。"""
        group = BUILTINS_EXCEPTION_GROUP(
            "unhandled errors in a TaskGroup", [ConnectionRefusedError(61, "Connection refused")]
        )
        hint = checker.hint_for(group)
        assert "连接被拒" in hint

    @pytest.mark.skipif(BUILTINS_EXCEPTION_GROUP is None, reason="需要 Python ≥3.11 的 ExceptionGroup")
    def test_nested_group_with_tls_error(self):
        import ssl as _ssl

        inner = BUILTINS_EXCEPTION_GROUP("inner", [_ssl.SSLCertVerificationError("certificate verify failed")])
        outer = BUILTINS_EXCEPTION_GROUP("outer", [inner])
        hint = checker.hint_for(outer)
        assert "TLS 校验失败" in hint

    def test_no_match_returns_empty(self):
        assert checker.hint_for(ValueError("something odd")) == ""


class TestDescribeException:
    def test_plain_exception_keeps_single_form(self):
        text = checker.describe_exception(ValueError("bad value"))
        assert "ValueError" in text and "bad value" in text

    @pytest.mark.skipif(BUILTINS_EXCEPTION_GROUP is None, reason="需要 Python ≥3.11 的 ExceptionGroup")
    def test_group_shows_leaf_exceptions(self):
        group = BUILTINS_EXCEPTION_GROUP(
            "unhandled errors in a TaskGroup", [ConnectionRefusedError(61, "Connection refused")]
        )
        text = checker.describe_exception(group)
        assert "ConnectionRefusedError" in text
        assert "Connection refused" in text


class TestTlsTrustModes:
    """2026-10-30：端点从「内网自签」扩为「校园域名公网证书 / 内网自签」双模式。

    旧检查器把「不带内网 CA 也能连上」判成失败（反证自签），域名入口会被误报；
    新语义是——系统信任库能验通就是公网证书；否则加载仓库 CA 能验通就是内网自签；
    两者都不通才失败。
    """

    def test_public_certificate_passes_with_system_store(self):
        ok, detail = checker.evaluate_tls_trust(system_trusted=True, extra_trusted=False)
        assert ok is True
        assert "公网证书" in detail

    def test_internal_ca_passes_when_system_store_rejects(self):
        ok, detail = checker.evaluate_tls_trust(system_trusted=False, extra_trusted=True)
        assert ok is True
        assert "内网自签" in detail

    def test_both_fail_is_red(self):
        ok, detail = checker.evaluate_tls_trust(system_trusted=False, extra_trusted=False)
        assert ok is False
        assert "均不认可" in detail

    def test_network_failure_is_not_misreported_as_certificate_failure(self):
        """超时/拒连不是证书问题——报成「证书校验失败」会把人引向错误的排查方向。"""
        ok, detail = checker.evaluate_tls_trust(
            system_trusted=False, extra_trusted=False, certificate_failure=False)
        assert ok is False
        assert "不是证书问题" in detail
        assert "证书校验失败" not in detail

    def test_build_ssl_context_is_a_verifying_client_context(self):
        for ca in (None, checker.DEFAULT_CA):
            ctx = checker.build_ssl_context(ca)
            assert isinstance(ctx, ssl.SSLContext)
            assert ctx.verify_mode == ssl.CERT_REQUIRED
            assert ctx.check_hostname is True

    def test_build_ssl_context_adds_extra_ca_on_top_of_system_store(self):
        """域名叫公网证书、IP 叫自签证书——同一个上下文必须同时信任两者。

        `ssl.create_default_context(cafile=...)` 只加载给定文件、**不叠加**系统信任库；
        检查器因此会把公网证书判死。这里把「叠加」行为钉住。
        """
        system = ssl.create_default_context()
        combined = checker.build_ssl_context(checker.DEFAULT_CA)
        try:
            before = system.cert_store_stats()["x509_ca"]
            after = combined.cert_store_stats()["x509_ca"]
        except (AttributeError, KeyError):
            pytest.skip("当前 OpenSSL 不提供 cert_store_stats")
        assert after >= before, "加载仓库 CA 不得挤掉系统信任库"


class TestResolveConfigCaOptional:
    """CA 现在是可选输入：域名入口走公网证书，直连内网才需要仓库 CA。"""

    @staticmethod
    def _env_file(tmp_path: Path) -> Path:
        path = tmp_path / ".env.tianyi-bot"
        path.write_text(
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL=https://mcp.example.com:8765/mcp\n"
            "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN=" + "x" * 64 + "\n"
        )
        return path

    def test_https_without_default_ca_is_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(checker, "DEFAULT_CA", tmp_path / "not-there.pem")
        args = checker.build_parser().parse_args(["--env-file", str(self._env_file(tmp_path))])
        url, token, ca_path = checker.resolve_config(args)
        assert url.startswith("https://")
        assert token
        assert ca_path is None, "公网证书端点不应被强制要求内网 CA"

    def test_explicit_missing_ca_is_still_a_config_error(self, tmp_path):
        args = checker.build_parser().parse_args([
            "--env-file", str(self._env_file(tmp_path)),
            "--ca", str(tmp_path / "explicit-missing.pem"),
        ])
        with pytest.raises(checker.ScriptError, match="CA 证书不存在"):
            checker.resolve_config(args)


class TestHostAllowlistHint:
    def test_421_hint_points_at_server_side_allowlist(self):
        """421 不是客户端能靠 headers 绕过的（Host 是 forbidden header）——
        提示必须指向服务端 MCP_ALLOWED_HOSTS，而不是旧的「URL 必须裸 IP」。"""
        hint = checker.hint_for(Exception("HTTP 421 Invalid Host header"))
        assert "Host 白名单" in hint
        assert "MCP_ALLOWED_HOSTS" in hint
        assert "裸 IP" not in hint
