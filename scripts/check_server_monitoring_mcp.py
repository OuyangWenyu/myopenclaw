#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==2.2.0"]
# ///
"""
check_server_monitoring_mcp.py — server-monitoring MCP 只读冒烟检查器

回答「天一 → server-monitoring 这条链路现在通不通」，不经过模型、不改任何状态：
token 形状 → TLS 信任链（公网证书 / 内网自签 CA 双模式）→ 证书有效期 → /health/live →
Bearer 鉴权（正/反两向）→ MCP initialize → 12 个工具名（9 运行 + 3 交付）→ 抽查调用。

用法:
  uv run scripts/check_server_monitoring_mcp.py                  # 默认读仓库 .env.tianyi-bot + 仓库内 CA
  uv run scripts/check_server_monitoring_mcp.py --json           # JSON（适合 cron/监控）
  uv run scripts/check_server_monitoring_mcp.py --env-file <路径>
  uv run scripts/check_server_monitoring_mcp.py --url <url> --token <token> --ca <pem> --server <名字>

优先级: 命令行参数 > --env-file > 进程环境变量。

退出码（同 check_paper_queue.py）:
  0 — 全部通过
  1 — 有检查项失败（连通性 / 鉴权 / 工具缺失）
  2 — 脚本自身错误（URL/token 未配置、CA 文件不存在、参数错误）

⚠️ token 只用于发请求，**从不打印**（只回显长度）；--json 输出同样不含 token。
依赖 pin `mcp==2.2.0`：与服务端验收时所用客户端一致 —— 协议协商细节随版本漂移，
检查器的意义就在于「验收时能通的，今天还通」。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env.tianyi-bot"
DEFAULT_CA = REPO_ROOT / "docker" / "tianyi-bot" / "ca" / "server-monitoring-ca.pem"

# 客户端策略依赖的工具面 = 9 个核心运行工具 + 3 个交付/CI 工具（2026-10-13 起纳入：
# 服务端当天正式发布 Jenkins 交付层，统一入口要"同时取得交付与运行事实"）。
# **缺任何一个 = 失败**（AGENTS.md 纪律引用了它们：运行类管"现在怎么样"，
# 交付类管"CI/构建怎么样"）。服务端新增、客户端尚未纳入策略的工具**不算失败**，
# 在 detail 里列出提示 —— 是否纳入是决策项，不是故障。
CORE_TOOLS = frozenset({
    "list_servers",
    "get_server_status",
    "list_containers",
    "get_container",
    "get_container_provenance",
    "get_prometheus_status",
    "get_project_runtime_status",
    "get_gfs_status",
    "get_runtime_failure_evidence",
})
DELIVERY_TOOLS = frozenset({
    "list_projects",
    "get_project_overview",
    "get_project_build",
})
REQUIRED_TOOLS = CORE_TOOLS | DELIVERY_TOOLS


def evaluate_tools(names: set[str]) -> tuple[bool, str]:
    """判定服务端工具面对客户端策略的满足度，返回 (ok, detail)。

    缺任一必需工具（不分组）→ False，detail 指明缺了什么；
    多出未知工具 → True，仅提示（供"是否纳入客户端策略"的决策参考）。
    """
    missing_core = sorted(CORE_TOOLS - names)
    missing_delivery = sorted(DELIVERY_TOOLS - names)
    extra = sorted(names - REQUIRED_TOOLS)
    if missing_core or missing_delivery:
        parts = []
        if missing_core:
            parts.append(f"缺核心运行工具 {missing_core}")
        if missing_delivery:
            parts.append(f"缺交付工具 {missing_delivery}")
        return False, "；".join(parts) + ("（服务端升级丢工具，或客户端策略未跟上？）" if not extra else f"；服务端另有：{extra}")
    note = "12/12 工具齐全（9 运行 + 3 交付）"
    if extra:
        note += f"；服务端另有 {len(extra)} 个未纳入客户端策略的工具（仅提示）：{extra}"
    return True, note


TOKEN_MIN_LEN = 32          # 服务端要求 Bearer token ≥32 个可打印 ASCII 字符
HTTP_TIMEOUT = 15.0         # 健康端点探测超时（秒）
CERT_WARN_DAYS = 30         # 证书剩余天数低于此值在输出里提醒（不算失败）


class ScriptError(Exception):
    """参数/配置层面的错误 —— 退出码 2。"""


# =============================================================
# 1. 配置解析
# =============================================================


def read_env_file(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def resolve_config(args: argparse.Namespace) -> tuple[str, str, Path | None]:
    """返回 (mcp_url, token, ca_path)。缺关键项抛 ScriptError。

    ca_path 是可选的：
    - 默认使用随仓库提交的内网 CA（存在时），叠加在系统信任库之上；
    - 域名入口是公网证书，不需要内网 CA —— 默认 CA 文件不存在时返回 None，
      走系统信任库（2026-10-30 起支持 <MCP_DOMAIN> 入口）；
    - 显式 `--ca` 指定的文件不存在 = 配置错误（不静默降级）。
    """
    env_file = Path(args.env_file).expanduser() if args.env_file else DEFAULT_ENV_FILE
    env = read_env_file(env_file) if env_file.is_file() else {}

    url = (
        args.url
        or env.get("TIANYI_BOT_SERVER_MONITORING_MCP_URL")
        or os.environ.get("TIANYI_BOT_SERVER_MONITORING_MCP_URL", "")
    ).strip()

    token = args.token or ""
    if not token and args.token_file:
        token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
    token = (
        token
        or env.get("TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN")
        or os.environ.get("MCP_SERVER_MONITORING_TOKEN", "")
    ).strip()

    if args.ca:
        ca_path: Path | None = Path(args.ca).expanduser()
        if not ca_path.is_file():
            raise ScriptError(f"CA 证书不存在: {ca_path}（--ca 指定；仓库内应随 docker/tianyi-bot/ca/ 提供）")
    else:
        ca_path = DEFAULT_CA if DEFAULT_CA.is_file() else None

    if not url:
        raise ScriptError(
            f"未配置 server-monitoring MCP URL（--url / {env_file} 的 "
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL / 进程环境变量）—— 未启用？"
        )
    if not token:
        raise ScriptError(
            f"未配置 token（--token / --token-file / {env_file} 的 "
            "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN / 进程环境变量）"
        )
    return url, token, ca_path


def build_ssl_context(ca_path: Path | None) -> ssl.SSLContext:
    """系统信任库 + 可选额外 CA。

    ⚠️ `ssl.create_default_context(cafile=...)` 只加载给定文件、**不叠加**系统
    信任库（与 Node 的 NODE_EXTRA_CA_CERTS 语义相反）。服务器现在有两个合法入口：
    校园域名（公网证书，系统信任库可验）和内网 IP（自签 CA，需额外加载仓库 CA），
    同一个上下文必须同时信任两者 —— 所以分两步加载。
    """
    context = ssl.create_default_context()
    if ca_path is not None and ca_path.is_file():
        context.load_verify_locations(cafile=str(ca_path))
    return context


def evaluate_tls_trust(system_trusted: bool, extra_trusted: bool,
                       *, certificate_failure: bool = True) -> tuple[bool, str]:
    """把「端点证书属于哪类信任链」翻译成检查结论（纯函数，供单测）。

    2026-10-30 前的检查器把「不带内网 CA 也能连上」判成失败（自签反证）；
    域名入口是公网证书，那个反证会误报。新语义：
    - 系统信任库能验通 → 公网证书，通过；
    - 系统不认、加载仓库 CA 后能验通 → 内网自签证书，通过；
    - 两者都不通：若至少一次是证书校验错误 → 证书问题；否则是网络/服务不可达
      （超时被报成「证书校验失败」会把人引向错误方向）。
    """
    if system_trusted:
        return True, "公网证书：系统信任库直接认可（无需内网 CA）"
    if extra_trusted:
        return True, "内网自签证书：系统信任库不认可，加载仓库 CA 后通过"
    if not certificate_failure:
        return False, "TLS 连接未建立：网络/服务不可达（不是证书问题）"
    return False, "证书校验失败：系统信任库与仓库 CA 均不认可"


def _is_certificate_error(exc: BaseException) -> bool:
    """异常树里是否有证书校验错误（URLError 会把它藏在 .reason 里）。"""
    for leaf in _leaf_exceptions(exc):
        if isinstance(leaf, ssl.SSLCertVerificationError):
            return True
        reason = getattr(leaf, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
        if "certificate verify" in str(leaf).lower():
            return True
    return False


# =============================================================
# 2. 结果收集
# =============================================================


def record(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail})


def http_get(url: str, context: ssl.SSLContext, headers: dict[str, str] | None = None) -> int:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, context=context, timeout=HTTP_TIMEOUT) as resp:
        return resp.status


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """展开异常树（mcp SDK 的会话失败包成 ExceptionGroup / TaskGroup 形态）。

    用鸭子类型探测 `.exceptions`（ExceptionGroup 是 3.11+，本脚本声明 >=3.10，
    不能直接依赖该类存在）。
    """
    children = getattr(exc, "exceptions", None)
    if children:
        leaves: list[BaseException] = []
        for child in children:
            leaves.extend(_leaf_exceptions(child))
        return leaves
    return [exc]


def describe_exception(exc: Exception) -> str:
    """人类可读的异常描述；包了组就展示叶子异常 —— 不拆包时最常见的
    连接被拒/TLS 失败只回显 'unhandled errors in a TaskGroup'，没法诊断。"""
    leaves = _leaf_exceptions(exc)
    if len(leaves) == 1 and leaves[0] is exc:
        return f"{type(exc).__name__}: {exc}"
    inner = "; ".join(f"{type(e).__name__}: {e}" for e in leaves)
    return f"{type(exc).__name__}({len(leaves)} 个子异常): {inner}"


def hint_for(exc: Exception) -> str:
    """把常见失败翻译成排查方向（对应 docs/server-monitoring-mcp.md 的对照表）。

    先展开异常树再逐叶匹配：组里任意一个叶子命中就给提示。
    """
    hints: list[str] = []
    for leaf in _leaf_exceptions(exc):
        text = f"{type(leaf).__name__}: {leaf}"
        low = text.lower()
        hint = ""
        if "certificate_verify" in low or "sslcertverification" in low:
            hint = " → TLS 校验失败：证书链不被系统信任库 / 仓库 CA 认可（域名入口用公网证书；内网直连需 --ca）"
        elif "401" in text or "unauthorized" in low:
            hint = " → 鉴权失败：token 不对，或 Authorization header 没送达"
        elif "refused" in low or "connecterror" in low or "all connection attempts" in low:
            hint = " → 连接被拒：服务未启动 / 端口不对 / 网络不通"
        elif "timed out" in low or "timeout" in low:
            hint = " → 超时：网络路径不通或服务卡住"
        elif "421" in text:
            hint = " → Host 白名单拒绝：服务端 MCP_ALLOWED_HOSTS 未包含该 Host（域名入口需由维护者显式加入；客户端无法用 headers 绕过）"
        elif "redirect" in low or "moved" in low:
            hint = " → 重定向：URL 可能少了 /mcp 路径"
        if hint and hint not in hints:
            hints.append(hint)
    return "".join(hints)


# =============================================================
# 3. 各项检查
# =============================================================


def check_token_shape(token: str, checks: list[dict]) -> None:
    printable = all(0x21 <= ord(c) <= 0x7E for c in token)
    ok = len(token) >= TOKEN_MIN_LEN and printable
    if ok:
        record(checks, "token_shape", True, f"长度 {len(token)}，全部为可打印 ASCII")
    else:
        record(checks, "token_shape", False,
               f"长度 {len(token)}（需 ≥{TOKEN_MIN_LEN}），可打印 ASCII={printable}")


def check_health_live(base: str, context: ssl.SSLContext, checks: list[dict]) -> None:
    try:
        status = http_get(f"{base}/health/live", context)
        record(checks, "health_live", status == 200, f"GET /health/live → {status}")
    except Exception as exc:  # noqa: BLE001 - 任意失败都是检查失败，不是脚本错误
        record(checks, "health_live", False, f"GET /health/live 失败: {type(exc).__name__}{hint_for(exc)}")


def check_tls_trust(base: str, ca_path: Path | None, checks: list[dict]) -> None:
    """判定端点证书属于哪类信任链，并验证当前上下文能验通。

    两种合法端点（2026-10-30 起）：
    - 校园域名入口 `<MCP_DOMAIN>:8765`：BunkerWeb 终结 TLS，公网
      TrustAsia 证书 —— 系统信任库直接认可，不再需要内网 CA；
    - 内网直连 `10.48.0.81:8765`：内网自签 CA —— 系统信任库不认可，需加载仓库 CA。

    旧检查器的「不带内网 CA 必须失败」反证只在自签端点成立，域名入口会误报；
    这里改为「系统信任库不行就再试仓库 CA」的双模式判定。
    """
    system_ok = False
    extra_ok = False
    system_detail = ""
    extra_detail = ""
    cert_failure = False

    try:
        status = http_get(f"{base}/health/live", ssl.create_default_context())
        system_ok = status == 200
        system_detail = f"系统信任库 → HTTP {status}"
    except Exception as exc:  # noqa: BLE001 - 两类失败都要尝试，不在这里判死
        cert_failure = cert_failure or _is_certificate_error(exc)
        system_detail = f"系统信任库失败: {type(exc).__name__}{hint_for(exc)}"

    if not system_ok and ca_path is not None:
        try:
            status = http_get(f"{base}/health/live", build_ssl_context(ca_path))
            extra_ok = status == 200
            extra_detail = f"仓库 CA → HTTP {status}"
        except Exception as exc:  # noqa: BLE001
            cert_failure = cert_failure or _is_certificate_error(exc)
            extra_detail = f"仓库 CA 失败: {type(exc).__name__}{hint_for(exc)}"

    ok, verdict = evaluate_tls_trust(system_ok, extra_ok, certificate_failure=cert_failure)
    detail_parts = [part for part in (system_detail, extra_detail) if part]
    record(checks, "tls_trust", ok, f"{verdict}（{'；'.join(detail_parts)}）")


def check_cert_expiry(base: str, ca_path: Path | None, checks: list[dict]) -> None:
    parsed = urlparse(base)
    host = parsed.hostname or ""
    port = parsed.port or 443
    try:
        ctx = build_ssl_context(ca_path)
        with socket.create_connection((host, port), timeout=HTTP_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        not_after = parsedate_to_datetime(cert["notAfter"])
        now = datetime.now(timezone.utc)
        days = (not_after - now).days
        if days < 0:
            record(checks, "cert_expiry", False, f"证书已过期（notAfter {cert['notAfter']}）")
        else:
            note = f"⚠️ 剩余不足 {CERT_WARN_DAYS} 天，该换证了" if days < CERT_WARN_DAYS else ""
            record(checks, "cert_expiry", True, f"有效期至 {cert['notAfter']}（剩 {days} 天）{note}")
    except Exception as exc:  # noqa: BLE001
        record(checks, "cert_expiry", False, f"读取证书失败: {type(exc).__name__}{hint_for(exc)}")


def check_auth(base: str, context: ssl.SSLContext, token: str, checks: list[dict]) -> None:
    # 反证：错误 token 必须 401（"服务端真的在验鉴权"的证据）
    bogus = "x" * TOKEN_MIN_LEN
    try:
        status = http_get(f"{base}/health/ready", context, {"Authorization": f"Bearer {bogus}"})
        record(checks, "auth_enforced", False, f"错误 token 也得到 {status} —— 鉴权形同虚设？")
    except urllib.error.HTTPError as exc:
        record(checks, "auth_enforced", exc.code == 401,
               f"错误 token → {exc.code}" + ("" if exc.code == 401 else "（预期 401）"))
    except Exception as exc:  # noqa: BLE001
        record(checks, "auth_enforced", False, f"{type(exc).__name__}{hint_for(exc)}")

    # 正向：正确 token 得到 200
    try:
        status = http_get(f"{base}/health/ready", context, {"Authorization": f"Bearer {token}"})
        record(checks, "auth_accepted", status == 200, f"正确 token → /health/ready {status}")
    except Exception as exc:  # noqa: BLE001
        record(checks, "auth_accepted", False, f"正确 token 请求失败: {type(exc).__name__}{hint_for(exc)}")


def extract_payload(result) -> object:
    """取工具返回的结构化数据；没有就退回解析文本块。"""
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    texts = [getattr(block, "text", "") for block in result.content]
    joined = "\n".join(t for t in texts if t)
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return joined


def summarize(payload: object, limit: int = 300) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


async def check_mcp(url: str, token: str, ca_path: Path | None, server_name: str,
                    checks: list[dict], use_tls: bool) -> None:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        verify=build_ssl_context(ca_path) if use_tls else True,
        timeout=httpx2.Timeout(30.0, read=120.0),
    )
    try:
        async with client:
            async with streamable_http_client(url, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    ok, detail = evaluate_tools(names)
                    record(checks, "tools_list", ok, detail)

                    res = await session.call_tool("list_servers", {})
                    payload = extract_payload(res)
                    detail = summarize(payload)
                    if res.is_error:
                        record(checks, "list_servers", False, f"工具报错: {detail}")
                    else:
                        registered = server_name in json.dumps(payload, ensure_ascii=False, default=str)
                        record(checks, "list_servers", registered,
                               detail + ("" if registered else f"（⚠️ 未出现 --server {server_name}，是否未注册？）"))

                    res = await session.call_tool("get_server_status", {"server": server_name})
                    payload = extract_payload(res)
                    detail = summarize(payload)
                    if res.is_error:
                        record(checks, f"get_server_status({server_name})", False, f"工具报错: {detail}")
                    else:
                        record(checks, f"get_server_status({server_name})", bool(detail),
                               detail or "返回为空")
    except Exception as exc:  # noqa: BLE001 - 会话级失败 = 三项检查全失败
        detail = f"{describe_exception(exc)}{hint_for(exc)}"
        for name in ("tools_list", "list_servers", f"get_server_status({server_name})"):
            if not any(c["name"] == name for c in checks):
                record(checks, name, False, f"MCP 会话失败: {detail}")


# =============================================================
# 4. 输出
# =============================================================


def emit_human(checks: list[dict], meta: dict) -> None:
    print("=== server-monitoring MCP 冒烟检查 ===")
    ca_label = meta["ca"] or "系统信任库（公网证书）"
    print(f"  端点: {meta['url']}（token 长度 {meta['token_length']}，CA: {ca_label}）")
    print()
    for check in checks:
        mark = "✅" if check["ok"] else "❌"
        print(f"  {mark} {check['name']:<28} {check['detail']}")
    failed = sum(1 for c in checks if not c["ok"])
    print()
    print(f"结果: {len(checks) - failed} 通过, {failed} 失败")


def emit_json(checks: list[dict], meta: dict) -> None:
    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({
        "status": "findings" if failed else "ok",
        "url": meta["url"],
        "server": meta["server"],
        "token_length": meta["token_length"],
        "ca": meta["ca"],
        "checks": checks,
    }, ensure_ascii=False, indent=2))


# =============================================================
# 5. main
# =============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="server-monitoring MCP 只读冒烟检查器（不打印 token）")
    parser.add_argument("--env-file", help=f"env 文件（默认 {DEFAULT_ENV_FILE}）")
    parser.add_argument("--url", help="覆盖 MCP URL（默认读 env 文件）")
    parser.add_argument("--token", help="覆盖 token（默认读 env 文件；⚠️ 会进 shell 历史，优先用 --token-file）")
    parser.add_argument("--token-file", help="从文件读取 token（首尾空白会被去掉）")
    parser.add_argument("--ca", help=f"额外 CA 证书（默认 {DEFAULT_CA}；公网证书端点可不填）")
    parser.add_argument("--server", default="weather", help="抽查状态的服务器名（默认 weather）")
    parser.add_argument("--json", action="store_true", help="JSON 输出（适合 cron/监控）")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        url, token, ca_path = resolve_config(args)
    except ScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    use_tls = url.startswith("https://")
    context = build_ssl_context(ca_path) if use_tls else None
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    checks: list[dict] = []
    check_token_shape(token, checks)
    if use_tls:
        check_health_live(base, context, checks)
        check_tls_trust(base, ca_path, checks)
        check_cert_expiry(base, ca_path, checks)
        check_auth(base, context, token, checks)
    else:
        record(checks, "tls_checks", True, "非 HTTPS 端点，跳过 TLS/CA 相关检查")

    try:
        asyncio.run(check_mcp(url, token, ca_path, args.server, checks, use_tls))
    except KeyboardInterrupt:
        print("❌ 被中断", file=sys.stderr)
        return 2

    meta = {
        "url": url,
        "server": args.server,
        "token_length": len(token),
        "ca": str(ca_path) if ca_path is not None else None,
    }
    (emit_json if args.json else emit_human)(checks, meta)
    return 1 if any(not c["ok"] for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
