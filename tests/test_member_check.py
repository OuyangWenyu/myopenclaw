"""member-check 检查器纯函数单测。

`docker/member-check/member_check.py` 是「成员接入自检」的判定核心——它以退出码
对外承诺「能力通不通」，改坏判定语义（把 401 放过、把 ⚪ 跳过算成失败）不会有任何
别的东西发现。这里只测**纯函数**：能力解析 / 配置错误判定 / 凭据清洗（token 不进
输出）/ 异常→提示翻译 / 状态码→提示映射 / 退出码聚合 / 报告渲染，不触网。

语义契约（2026-10-14 定，独立 review 后加固）：
- URL 空 = ⚪ 跳过，**不算失败**；
- URL 非空但 token 缺失 = 配置错误（退出码 2）；
- token 含引号/空白/控制字符（不可能合法）= 配置错误（2）；长度 <32 仅警告；
- CA：显式 <能力>_MCP_CA 优先；server-monitoring 走 https 未显式指定时回落默认内网 CA；
  配置了 CA 但文件不存在 = 配置错误（2）；
- 退出码：0=无失败（⚪ 允许）/ 1=有探测失败 / 2=配置或脚本错误；未知状态按失败（fail-closed）；
- **报告不含 token 值，也不含 URL 的 userinfo/query**（含异常/服务端回显文本）。

Run: uv run --with pytest pytest tests/test_member_check.py -v
"""

from __future__ import annotations

import builtins
import importlib.util
import ssl
import sys
import urllib.parse
from pathlib import Path

import pytest

BUILTINS_EXCEPTION_GROUP = getattr(builtins, "ExceptionGroup", None)

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "docker" / "member-check" / "member_check.py"


def _load_checker():
    """按路径加载检查器模块（docker/member-check 含连字符，只能 importlib）。"""
    spec = importlib.util.spec_from_file_location("member_check_under_test", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()

TEST_CA = "/ca/server-monitoring-ca.pem"


def CA_OK(_path: str) -> bool:
    return True


def CA_MISSING(_path: str) -> bool:
    return False


def _full_env(**overrides) -> dict[str, str]:
    env = {
        "SERVER_MONITORING_MCP_URL": "https://10.48.0.81:8765/mcp",
        "SERVER_MONITORING_MCP_TOKEN": "t" * 40,
        "YUQUE_MCP_URL": "https://chitu.example:18001/sse",
        "YUQUE_MCP_TOKEN": "y" * 40,
        "REPO_SCANNER_MCP_URL": "https://chitu.example:8001/mcp",
        "REPO_SCANNER_MCP_TOKEN": "r" * 40,
    }
    env.update(overrides)
    return env


def _caps(env=None, default_ca=TEST_CA):
    return checker.parse_capabilities(_full_env() if env is None else env, default_ca)


class TestInferTransport:
    def test_sse_suffix(self):
        assert checker.infer_transport("https://x.example:18001/sse") == "sse"

    def test_mcp_suffix_is_streamable_http(self):
        assert checker.infer_transport("https://x.example:8001/mcp") == "streamable-http"

    def test_other_suffix_defaults_to_streamable_http(self):
        assert checker.infer_transport("https://x.example:8001/") == "streamable-http"

    def test_sse_with_query_still_sse(self):
        # 只看 path 不看 query —— `…/sse?x=1` 必须仍是 SSE
        assert checker.infer_transport("https://x.example:18001/sse?tenant=abc") == "sse"


class TestParseCapabilities:
    def test_fixed_order_and_names(self):
        assert [c.name for c in _caps()] == ["server-monitoring", "yuque", "repo-scanner"]

    def test_full_env_parses_urls_and_tokens(self):
        caps = {c.name: c for c in _caps()}
        assert caps["yuque"].url == "https://chitu.example:18001/sse"
        assert caps["yuque"].token == "y" * 40
        assert caps["yuque"].transport == "sse"
        assert caps["server-monitoring"].transport == "streamable-http"

    def test_blank_url_is_off_but_keeps_entry(self):
        caps = {c.name: c for c in _caps(_full_env(REPO_SCANNER_MCP_URL="  "))}
        assert caps["repo-scanner"].url == ""
        assert len(caps) == 3  # ⚪ 也要出现在报告里，不能消失

    def test_missing_vars_are_off_not_error(self):
        caps = {c.name: c for c in _caps({})}
        assert all(c.url == "" for c in caps.values())

    def test_token_whitespace_stripped(self):
        caps = {c.name: c for c in _caps(_full_env(YUQUE_MCP_TOKEN="  y" + "z" * 39 + "  "))}
        assert caps["yuque"].token == "y" + "z" * 39

    def test_default_ca_only_for_https_server_monitoring(self):
        caps = {c.name: c for c in _caps()}
        assert caps["server-monitoring"].ca_path == TEST_CA
        assert caps["yuque"].ca_path == ""
        assert caps["repo-scanner"].ca_path == ""

    def test_http_server_monitoring_does_not_get_ca(self):
        caps = {c.name: c for c in _caps(_full_env(SERVER_MONITORING_MCP_URL="http://10.48.0.81:8765/mcp"))}
        assert caps["server-monitoring"].ca_path == ""

    def test_explicit_per_capability_ca_is_respected(self):
        caps = {c.name: c for c in _caps(_full_env(YUQUE_MCP_CA="/ca/other-ca.pem"))}
        assert caps["yuque"].ca_path == "/ca/other-ca.pem"

    def test_explicit_ca_overrides_monitoring_default(self):
        caps = {c.name: c for c in _caps(_full_env(SERVER_MONITORING_MCP_CA="/ca/custom.pem"))}
        assert caps["server-monitoring"].ca_path == "/ca/custom.pem"


class TestConfigErrors:
    def test_url_without_token_is_error_naming_both_vars(self):
        errors = checker.config_errors(_caps(_full_env(YUQUE_MCP_TOKEN="")), CA_OK)
        assert len(errors) == 1
        assert "YUQUE_MCP_URL" in errors[0] and "YUQUE_MCP_TOKEN" in errors[0]

    def test_off_capability_with_token_only_is_not_error(self):
        errors = checker.config_errors(_caps(_full_env(YUQUE_MCP_URL="")), CA_OK)
        assert errors == []

    def test_https_server_monitoring_without_ca_is_error(self):
        errors = checker.config_errors(_caps(), CA_MISSING)
        assert len(errors) == 1
        assert "CA" in errors[0]

    def test_ca_not_required_when_server_monitoring_off(self):
        errors = checker.config_errors(_caps(_full_env(SERVER_MONITORING_MCP_URL="")), CA_MISSING)
        assert errors == []

    def test_explicit_ca_missing_is_error(self):
        errors = checker.config_errors(_caps(_full_env(YUQUE_MCP_CA="/ca/nope.pem")),
                                       lambda p: p != "/ca/nope.pem")
        assert len(errors) == 1
        assert "语雀" in errors[0] and "/ca/nope.pem" in errors[0]

    def test_quoted_token_is_config_error(self):
        errors = checker.config_errors(_caps(_full_env(YUQUE_MCP_TOKEN='"quoted-token-value-1234567890123456"')), CA_OK)
        assert len(errors) == 1
        assert "引号" in errors[0]

    def test_all_good_no_errors(self):
        assert checker.config_errors(_caps(), CA_OK) == []


class TestTokenShape:
    def test_clean_token_is_fine(self):
        assert checker.token_shape_problem("A" * 40) is None

    def test_double_quote_detected(self):
        problem = checker.token_shape_problem('"token"')
        assert problem and "引号" in problem

    def test_inner_space_detected(self):
        assert checker.token_shape_problem("abc def") is not None

    def test_control_char_detected(self):
        assert checker.token_shape_problem("abc\tdef") is not None


class TestTokenLengthWarning:
    def test_short_token_warns(self):
        warning = checker.token_length_warning("abc123")
        assert warning and "长度仅 6" in warning

    def test_long_token_no_warning(self):
        assert checker.token_length_warning("A" * 40) is None

    def test_empty_token_no_warning(self):
        assert checker.token_length_warning("") is None


class TestDisplayUrl:
    def test_strips_userinfo_and_query(self):
        assert checker.display_url("https://user:pw@h.example:8443/mcp?token=abc&x=1") == \
            "https://h.example:8443/mcp"

    def test_keeps_port_and_path(self):
        assert checker.display_url("http://yuque-mcp:18001/sse") == "http://yuque-mcp:18001/sse"

    def test_fragment_stripped(self):
        assert checker.display_url("https://h.example/mcp#frag") == "https://h.example/mcp"

    def test_non_url_passthrough(self):
        assert checker.display_url("not-a-url") == "not-a-url"

    def test_invalid_port_still_strips_credentials(self):
        # 非法端口走 ValueError 回退路径——回退也不能原样回吐 userinfo/query
        out = checker.display_url("https://user:secretpw@h.example:badport/mcp?token=abc123xyz")
        assert "secretpw" not in out and "token=abc123xyz" not in out


class TestScrub:
    def test_replaces_secret(self):
        assert checker.scrub("failed with key sk-abc123456789", ["sk-abc123456789"]) == "failed with key ***"

    def test_short_secrets_ignored_to_avoid_mosaic(self):
        assert checker.scrub("hello world", ["a", "lo"]) == "hello world"

    def test_empty_secrets_noop(self):
        assert checker.scrub("text", ["", None]) == "text"

    def test_scrub_covers_url_parts_for_denormalized_exception_text(self):
        """httpx2 异常把 URL 原样写进文本；即使与 cap.url 逐字不同（归一化差异），
        userinfo/query 分段也要被洗掉——_probe_one 的清洗集包含 URL 及其分段。"""
        url = "http://user:secretpw@127.0.0.1:9/sse?token=abc123xyz"
        denormalized = "HTTPStatusError: 401 for url 'http://user:secretpw@127.0.0.1:9/sse/?token=abc123xyz'"
        out = checker.scrub(denormalized, ("t" * 40, *checker.url_secrets(url)))
        assert "secretpw" not in out and "abc123xyz" not in out


class TestUrlSecrets:
    def test_clean_url_yields_nothing(self):
        # 干净 URL 不洗——展示地址（如 https://10.48.0.81:8765/mcp）是诊断信息，
        # 无脑清洗会把真机报告打成 [***]（2026-10-14 复验发现）
        assert checker.url_secrets("https://10.48.0.81:8765/mcp") == ()
        assert checker.url_secrets("http://yuque-mcp:18001/sse") == ()

    def test_userinfo_yields_parts(self):
        secrets = checker.url_secrets("https://user:secretpw@h.example/mcp")
        assert "user" in secrets and "secretpw" in secrets

    def test_query_yields_parts_and_raw(self):
        url = "https://h.example/mcp?token=abc123xyz"
        secrets = checker.url_secrets(url)
        assert url in secrets and "token=abc123xyz" in secrets


class TestHintFor:
    def test_401_maps_to_token_hint(self):
        assert "token" in checker.hint_for(Exception("server returned HTTP 401 Unauthorized")).lower()

    def test_401_substring_in_number_does_not_false_positive(self):
        # `\b401\b`：4010 之类的数字不能被误判成鉴权失败
        assert checker.hint_for(Exception("error code 4010")) == ""

    def test_connection_refused_maps_to_unreachable_hint(self):
        hint = checker.hint_for(ConnectionRefusedError("Connection refused"))
        assert "连不通" in hint or "连接被拒" in hint

    def test_cert_verify_maps_to_ca_hint(self):
        exc = ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")
        assert "CA" in checker.hint_for(exc)

    def test_timeout_maps_to_timeout_hint(self):
        assert "超时" in checker.hint_for(TimeoutError("timed out")) or \
               "超时" in checker.hint_for(Exception("ReadTimeout"))

    def test_synthetic_streamable_error_gets_fallback_hint(self):
        # streamable 传输层合成的文案（不带状态码）必须有兜底提示（状态码由旁路探测补）
        hint = checker.hint_for(Exception("Server returned an error response"))
        assert hint and "状态码" in hint

    def test_exception_group_flattened(self):
        if BUILTINS_EXCEPTION_GROUP is None:
            pytest.skip("Python < 3.11 无 ExceptionGroup")
        group = BUILTINS_EXCEPTION_GROUP("unhandled errors in a TaskGroup", [
            ValueError("HTTP 401 Unauthorized"),
            ConnectionRefusedError("Connection refused"),
        ])
        hint = checker.hint_for(group)
        assert "token" in hint.lower()
        assert "连不通" in hint or "连接被拒" in hint

    def test_unknown_exception_still_returns_a_string(self):
        assert isinstance(checker.hint_for(Exception("some novel failure")), str)


class TestStatusHint:
    def test_401_and_403_map_to_auth(self):
        assert "鉴权" in checker.status_hint(401)
        assert "鉴权" in checker.status_hint(403)

    def test_404_maps_to_path(self):
        assert "路径" in checker.status_hint(404)

    def test_421_maps_to_host_whitelist(self):
        assert "Host" in checker.status_hint(421)

    def test_400_means_auth_passed(self):
        assert "鉴权已通过" in checker.status_hint(400)

    def test_none_returns_empty(self):
        assert checker.status_hint(None) == ""


class TestExitCode:
    def _result(self, status):
        return checker.ProbeResult(name="x", label="X", status=status, detail="d")

    def test_all_pass_or_off_is_zero(self):
        assert checker.summarize_exit_code([self._result("pass"), self._result("off")]) == 0

    def test_any_fail_is_one(self):
        assert checker.summarize_exit_code([self._result("pass"), self._result("fail")]) == 1

    def test_off_only_is_zero(self):
        assert checker.summarize_exit_code([self._result("off")]) == 0

    def test_unknown_status_is_fail_closed(self):
        assert checker.summarize_exit_code([self._result("typo-status")]) == 1


class TestMainExitCode:
    def test_main_exit_2_on_url_without_token(self, tmp_path, monkeypatch, capsys):
        """main() 级的退出码 2 契约（此前只有 config_errors 层被钉，把 main 里的
        `return 2` 改坏套件仍会绿）。"""
        monkeypatch.delenv("YUQUE_MCP_TOKEN", raising=False)
        env_file = tmp_path / ".env.member"
        env_file.write_text("YUQUE_MCP_URL=http://yuque.example/sse\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["member_check.py", "--env-file", str(env_file)])
        assert checker.main() == 2
        assert "YUQUE_MCP_TOKEN" in capsys.readouterr().err

    def test_main_exit_2_on_missing_env_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["member_check.py", "--env-file", str(tmp_path / "nope")])
        assert checker.main() == 2
        assert "不存在" in capsys.readouterr().err


class TestFormatReport:
    def _results(self):
        return [
            checker.ProbeResult("server-monitoring", "服务器监控", "pass", "12 个工具可用"),
            checker.ProbeResult("yuque", "语雀知识库", "fail", "MCP 会话失败: HTTP 401 → 鉴权失败"),
            checker.ProbeResult("repo-scanner", "仓库动态", "off", "未配置（跳过）"),
        ]

    def test_symbols_per_status(self):
        report = checker.format_report(self._results())
        assert "✓" in report and "✗" in report and "⚪" in report

    def test_counts_line(self):
        report = checker.format_report(self._results())
        assert "1 通过" in report and "1 失败" in report and "1 跳过" in report

    def test_each_capability_on_its_own_line(self):
        report = checker.format_report(self._results())
        lines = [l for l in report.splitlines() if any(s in l for s in ("✓", "✗", "⚪"))]
        assert len(lines) == 3

    def test_secrets_scrubbed_at_report_boundary(self):
        # 走真实清洗路径：detail 里嵌了 token（模拟异常文本回显），报告边界必须洗掉
        secret = "supersecrettokenvalue1234567890abcdef"
        results = [checker.ProbeResult("yuque", "语雀知识库", "fail", f"MCP 会话失败: 401 for key {secret}")]
        report = checker.format_report(results, secrets=[secret])
        assert secret not in report
        assert "***" in report

    def test_all_off_gets_visible_notice(self):
        results = [checker.ProbeResult("yuque", "语雀知识库", "off", "未配置（跳过）")]
        report = checker.format_report(results)
        assert "本次未验证任何链路" in report

    def test_all_off_notice_absent_when_something_passed(self):
        report = checker.format_report(self._results())
        assert "本次未验证任何链路" not in report
