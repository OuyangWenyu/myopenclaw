"""qwennote（千问办公 AI听记）接入的静态与单元守卫。

Run: uv run --with pytest pytest tests/test_qwennote_integration.py -v

守卫三类易静默损坏的事实：
- 注册脚本是外科文本编辑：保留无关内容、幂等、url 冲突需 --force、绝不写 profile 子目录
  （个人听记仅默认 profile 可用）；
- 授权助手的纯函数（授权 URL 构造 / 回调解析）行为正确——这是无头容器里
  绕开 `hermes mcp login` 并发双流缺陷的关键路径；
- 文档与入口在位，且仓库文件中不出现真实的 DCR client_id。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bootstrap_hermes_qwennote import (  # noqa: E402
    MANAGED_BY,
    SERVER_NAME,
    SERVER_URL,
    ensure_server_entry,
    guard_default_home,
    remove_server_entry,
)
from qwennote_oauth_login import (  # noqa: E402
    CallbackError,
    build_authorize_url,
    parse_callback,
)

ISS = "https://minutes.qwennote.cn"

SAMPLE_CONFIG = """\
model:
  default: deepseek-flash
mcp_servers:
  aisecretary:
    connect_timeout: 60
    enabled: true
    timeout: 120
    url: http://aisecretary:8000/mcp/
  zotero:
    connect_timeout: 60
    enabled: true
    timeout: 120
    url: http://zotero-mcp:8002/mcp
platform_toolsets:
  cli:
    - hermes-cli
"""


class TestEnsureServerEntry:
    def test_inserts_into_existing_section_preserving_neighbors(self):
        new_text, status = ensure_server_entry(SAMPLE_CONFIG)
        assert status == "inserted"
        assert f"  {SERVER_NAME}:" in new_text
        assert f"url: {SERVER_URL}" in new_text
        assert f"managed_by: {MANAGED_BY}" in new_text
        # 既有条目与后续段完整保留
        assert "aisecretary" in new_text and "zotero" in new_text
        assert "platform_toolsets:" in new_text
        # 插入位置在 section 内（在 platform_toolsets 之前）
        assert new_text.index(SERVER_NAME) < new_text.index("platform_toolsets:")

    def test_appends_section_when_missing(self):
        text = "model:\n  default: deepseek-flash\n"
        new_text, status = ensure_server_entry(text)
        assert status == "appended"
        assert "mcp_servers:" in new_text
        assert f"  {SERVER_NAME}:" in new_text

    def test_idempotent_when_entry_exists(self):
        once, _ = ensure_server_entry(SAMPLE_CONFIG)
        twice, status = ensure_server_entry(once)
        assert status == "exists"
        assert twice == once  # 幂等：不再改写

    def test_conflict_on_different_url_requires_force(self):
        text = SAMPLE_CONFIG.replace("platform_toolsets:", """\
  qwennote:
    url: https://example.com/other/mcp
platform_toolsets:""")
        unchanged, status = ensure_server_entry(text)
        assert status == "conflict"
        assert unchanged == text
        forced, status = ensure_server_entry(text, force=True)
        assert status == "replaced"
        assert f"url: {SERVER_URL}" in forced
        assert "example.com" not in forced


class TestRemoveServerEntry:
    def test_removes_entry_keeps_other_servers(self):
        text, _ = ensure_server_entry(SAMPLE_CONFIG)
        new_text, status = remove_server_entry(text)
        assert status == "removed"
        assert SERVER_NAME not in new_text
        assert "aisecretary" in new_text and "zotero" in new_text

    def test_removes_empty_section_header(self):
        text, _ = ensure_server_entry("model:\n  default: x\n")
        assert "mcp_servers:" in text
        new_text, status = remove_server_entry(text)
        assert status == "removed"
        assert "mcp_servers:" not in new_text

    def test_absent_when_missing(self):
        new_text, status = remove_server_entry(SAMPLE_CONFIG)
        assert status == "absent"
        assert new_text == SAMPLE_CONFIG


class TestGuardDefaultHome:
    def test_rejects_profile_path(self, tmp_path):
        with pytest.raises(SystemExit):
            guard_default_home(tmp_path / "profiles" / "coder" / "config.yaml")

    def test_allows_default_path(self, tmp_path):
        guard_default_home(tmp_path / "config.yaml")  # 不应抛出


class TestParseCallback:
    def test_full_url_with_iss(self):
        line = ("http://127.0.0.1:27890/callback?code=abc123&state=st-1"
                "&iss=https%3A%2F%2Fminutes.qwennote.cn")
        assert parse_callback(line, expected_state="st-1", expected_issuer=ISS) == "abc123"

    def test_bare_query_fragment(self):
        assert parse_callback("?code=x9&state=st-1", expected_state="st-1") == "x9"

    def test_missing_code_rejected(self):
        with pytest.raises(CallbackError):
            parse_callback("http://127.0.0.1:1/callback?state=st-1", expected_state="st-1")

    def test_state_mismatch_rejected(self):
        with pytest.raises(CallbackError):
            parse_callback("?code=x&state=other", expected_state="st-1")

    def test_iss_mismatch_rejected(self):
        with pytest.raises(CallbackError):
            parse_callback("?code=x&state=st-1&iss=https%3A%2F%2Fevil.example",
                           expected_state="st-1", expected_issuer=ISS)

    def test_iss_absent_is_tolerated(self):
        assert parse_callback("?code=x&state=st-1", expected_state="st-1",
                              expected_issuer=ISS) == "x"


class TestAuthorizeUrl:
    def test_params_include_resource_pkce_and_scope(self):
        url = build_authorize_url(
            authorization_endpoint=ISS + "/authorize",
            client_id="cid",
            redirect_uri="http://127.0.0.1:27890/callback",
            state="st",
            code_challenge="ch",
            resource=ISS + "/mcp",
            scope="qwennote:read qwennote:write",
        )
        assert url.startswith(ISS + "/authorize?")
        for frag in ("response_type=code", "code_challenge_method=S256",
                     "resource=https%3A%2F%2Fminutes.qwennote.cn%2Fmcp",
                     "scope=qwennote%3Aread+qwennote%3Awrite", "client_id=cid"):
            assert frag in url, f"缺少参数片段: {frag}"


class TestDocsAndPrivacy:
    def test_docs_exist_and_linked(self):
        import re

        doc = REPO_ROOT / "docs" / "qwennote-mcp-hermes.md"
        assert doc.exists()
        text = doc.read_text(encoding="utf-8")
        assert SERVER_URL in text
        # 默认 profile-only 的边界必须在文档中明示（隔离自检命令可含对齐空格）
        assert re.search(r"-p coder\s+mcp list", text), "文档缺少 -p coder 隔离自检命令"
        assert "仅默认 profile" in text or "只有**默认 profile" in text
        index = (REPO_ROOT / "docs" / "index.md").read_text(encoding="utf-8")
        assert "qwennote-mcp-hermes.md" in index

    def test_claude_md_references_bootstrap(self):
        text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        assert "bootstrap_hermes_qwennote.py" in text
        assert "qwennote_oauth_login.py" in text

    def test_no_real_client_id_in_repo_files(self):
        # 真实 DCR 注册的 client_id 形如 qnmcp-<hex>，只应存在于本机 ~/.hermes/mcp-tokens/
        for rel in ("scripts/bootstrap_hermes_qwennote.py", "scripts/qwennote_oauth_login.py",
                    "docs/qwennote-mcp-hermes.md"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "qnmcp-" not in text, f"{rel} 中出现真实 client_id"
