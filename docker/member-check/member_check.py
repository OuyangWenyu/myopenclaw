#!/usr/bin/env python3
"""
member_check.py — 团队能力接入自检（成员 / 验收 / 迁移验证三用）

回答「我（的 agent）→ 团队服务 这几条链路现在通不通」，不经过模型、不改任何状态：
对每个配置了的能力做一次 MCP 握手（initialize + tools/list），区分
「服务活着但 token 错」（401/403）与「连不通」；未配置的能力标 ⚪ 跳过。

三个能力（URL 空 = 未启用 = ⚪ 跳过，不算失败）:
  - server-monitoring  服务器监控    SERVER_MONITORING_MCP_URL / SERVER_MONITORING_MCP_TOKEN
  - yuque              语雀知识库    YUQUE_MCP_URL             / YUQUE_MCP_TOKEN
  - repo-scanner       仓库动态      REPO_SCANNER_MCP_URL      / REPO_SCANNER_MCP_TOKEN

自签 https 端点：默认仅 server-monitoring 加载内网 CA（容器内 /ca/，宿主机自动回退
仓库路径 docker/tianyi-bot/ca/）。其它能力指向自签 https 时用 <能力>_MCP_CA 指定
（如 YUQUE_MCP_CA=/ca/server-monitoring-ca.pem）。

用法（容器内，推荐）:
  ./scripts/member-check.sh                      # build（缓存）→ docker run --env-file .env.member
用法（宿主机，有 uv 时）:
  uv run --with mcp==2.2.0 docker/member-check/member_check.py --env-file .env.member

退出码（同 check_server_monitoring_mcp.py / check_paper_queue.py）:
  0 — 无失败（⚪ 跳过允许存在）
  1 — 有探测失败（连通性 / 鉴权 / 协议）
  2 — 配置或脚本错误（URL 配了但 token 缺失、token 含非法字符、CA 缺失、参数错误）

安全契约：token 只用于发请求；报告里 **token 值、URL 的 userinfo/query 均被剥除/清洗**
（含异常文本与服务端回显），由 `scrub` / `display_url` 两层保证并有测试钉死。

依赖 pin `mcp==2.2.0`：与 check_server_monitoring_mcp.py 同一客户端版本 ——
「验收时能通的，今天还通」。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

# 容器内 CA 挂载点（scripts/member-check.sh 挂 docker/tianyi-bot/ca/ 到此）
DEFAULT_CA = Path("/ca/server-monitoring-ca.pem")

# 能力规格表：显示顺序固定。字段 = (name, label, url_var, token_var, ca_var)
# ca_var：显式指定自签 https 端点的 CA；server-monitoring 未显式指定时用内网默认 CA。
_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("server-monitoring", "服务器监控", "SERVER_MONITORING_MCP_URL", "SERVER_MONITORING_MCP_TOKEN", "SERVER_MONITORING_MCP_CA"),
    ("yuque", "语雀知识库", "YUQUE_MCP_URL", "YUQUE_MCP_TOKEN", "YUQUE_MCP_CA"),
    ("repo-scanner", "仓库动态", "REPO_SCANNER_MCP_URL", "REPO_SCANNER_MCP_TOKEN", "REPO_SCANNER_MCP_CA"),
)


@dataclass(frozen=True)
class Capability:
    name: str
    label: str
    url: str
    token: str
    transport: str  # "sse" | "streamable-http"
    ca_path: str  # "" = 不用自签 CA


@dataclass(frozen=True)
class ProbeResult:
    name: str
    label: str
    status: str  # "pass" | "fail" | "off"
    detail: str


# =============================================================
# 1. 凭据/URL 卫生（纯函数；「token 不进输出」的安全契约在此兑现）
# =============================================================


def display_url(url: str) -> str:
    """报告展示用 URL：剥掉 userinfo 与 query/fragment（可能携带凭据），保留 scheme/host/port/path。"""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return url
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname or ''}{port}{parsed.path}"
    except ValueError:
        # 非法端口等 —— 最小化剥离（去 userinfo / query / fragment）后返回，别原样回吐
        return re.sub(r"//[^/@]*@", "//", url).split("?", 1)[0].split("#", 1)[0]


def scrub(text: str, secrets: Sequence[str]) -> str:
    """把 text 中出现的任何 secret 替换为 ***（token 或 URL 片段进入输出的最后一道闸）。"""
    out = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    return out


def url_secrets(url: str) -> tuple[str, ...]:
    """URL 中需要清洗的部分：**仅当原始 URL 带 userinfo/query**（与展示形态不同）时返回
    （原串 + username/password/query 分段）——干净的 URL 返回空元组。

    为什么不能无脑把 cap.url 全塞进清洗集：`display_url()` 剥掉凭据后的展示地址
    （如 https://10.48.0.81:8765/mcp）是**有价值的诊断信息**，边界清洗会把与原始串
    相同的干净 URL 也打成 `[***]`（2026-10-14 真机复验发现）。
    """
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password or parsed.query:
            return (url, parsed.username or "", parsed.password or "", parsed.query or "")
    except ValueError:  # 理论不可达（此处未访问 .port），保守返回空
        pass
    return ()


# =============================================================
# 2. 配置解析与判定（纯函数）
# =============================================================


def infer_transport(url: str) -> str:
    """按 URL **路径** 判定传输：/sse 结尾 = SSE（yuque 服务端形态）；其余按 streamable-http。

    只看 path（不含 query）——`…/sse?x=1` 也必须判成 SSE。
    """
    try:
        path = urlparse(url).path
    except ValueError:
        path = url
    return "sse" if path.rstrip("/").endswith("/sse") else "streamable-http"


def parse_capabilities(env: Mapping[str, str], default_ca: str = str(DEFAULT_CA)) -> list[Capability]:
    """从环境映射解析三个能力；URL 空 = 未启用（仍保留条目，报告里显示 ⚪）。

    CA 规则：显式 <能力>_MCP_CA 优先；server-monitoring 走 https 且未显式指定时
    回落 default_ca（内网自签）；其余能力默认不加载 CA（对公网 https 端点
    替换信任库会反向弄坏校验，故不无脑生效）。
    """
    caps: list[Capability] = []
    for name, label, url_var, token_var, ca_var in _SPECS:
        url = (env.get(url_var) or "").strip()
        token = (env.get(token_var) or "").strip()
        ca_path = (env.get(ca_var) or "").strip()
        if not ca_path and name == "server-monitoring" and url.startswith("https://"):
            ca_path = default_ca
        caps.append(Capability(name, label, url, token, infer_transport(url), ca_path))
    return caps


def token_shape_problem(token: str) -> str | None:
    """token 形状不可能合法（引号/空白/控制字符）→ 返回问题描述；合法 → None。

    成员最常见的翻车：写 `TOKEN="xxx"`（引号原样进 token，docker --env-file 与
    read_env_file 都不剥引号）或复制带截断。这类问题在本地拦下，别让它到远端
    表现为「鉴权失败」把人引去找管理员。
    """
    bad = sorted({c for c in token if c in "\"'` \t\r\n" or ord(c) < 0x21})
    if bad:
        shown = " ".join(repr(c) for c in bad)
        return f"token 含不可能合法的字符（{shown}）——常见于引号包裹或复制截断，请重填"
    return None


def token_length_warning(token: str) -> str | None:
    """长度可疑（服务端一般 ≥32）→ 警告文本（不算错误，仅提示核对）。"""
    if token and len(token) < 32:
        return f"token 长度仅 {len(token)}（服务端一般 ≥32）——若鉴权失败先核对是否复制不完整"
    return None


def config_errors(capabilities: list[Capability], ca_exists: Callable[[str], bool]) -> list[str]:
    """配置层面的错误（→ 退出码 2）。返回空列表 = 可继续探测。

    ca_exists：CA 路径 → 是否存在（注入以便纯函数测试）。
    """
    errors: list[str] = []
    for cap in capabilities:
        if cap.url and not cap.token:
            token_var = next(t for n, _, _, t, _ in _SPECS if n == cap.name)
            url_var = next(u for n, _, u, _, _ in _SPECS if n == cap.name)
            errors.append(
                f"{cap.label}：配置了 {url_var} 但没有 {token_var}"
                "（token 向管理员获取；只填 token 不填 URL = 未启用，不是错误）"
            )
            continue
        if cap.token:
            problem = token_shape_problem(cap.token)
            if problem:
                errors.append(f"{cap.label}：{problem}")
        if cap.ca_path and not ca_exists(cap.ca_path):
            errors.append(
                f"{cap.label}：配置了 CA（{cap.ca_path}）但文件不存在"
                "（容器内路径 /ca/… 由 wrapper 挂载；<能力>_MCP_CA 可改）"
            )
    return errors


def resolve_default_ca() -> str:
    """默认 CA：容器内 /ca/ 优先；宿主机 uv 直跑时回退仓库内路径（无需 --ca）。"""
    if DEFAULT_CA.is_file():
        return str(DEFAULT_CA)
    repo_ca = Path(__file__).resolve().parents[2] / "docker" / "tianyi-bot" / "ca" / "server-monitoring-ca.pem"
    if repo_ca.is_file():
        return str(repo_ca)
    return str(DEFAULT_CA)  # 报错信息里展示容器挂载点（wrapper 会挂它）


# =============================================================
# 3. 异常 → 排查提示（纯函数，移植 check_server_monitoring_mcp.py 的手法）
# =============================================================


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """展开异常树（mcp SDK 的会话失败包成 ExceptionGroup / TaskGroup 形态）。"""
    children = getattr(exc, "exceptions", None)
    if children:
        leaves: list[BaseException] = []
        for child in children:
            leaves.extend(_leaf_exceptions(child))
        return leaves
    return [exc]


def hint_for(exc: BaseException) -> str:
    """把常见失败翻译成排查方向；先展开异常树再逐叶匹配，命中即收。"""
    hints: list[str] = []
    for leaf in _leaf_exceptions(exc):
        text = f"{type(leaf).__name__}: {leaf}"
        low = text.lower()
        hint = ""
        if "certificate_verify" in low or "sslcertverification" in low or "certificate verify failed" in low:
            hint = " → TLS 校验失败：CA 不对 / 未挂载 CA（自签端点用 <能力>_MCP_CA 指定）"
        elif re.search(r"\b401\b", text) or "unauthorized" in low or re.search(r"\b403\b", text) or "forbidden" in low:
            hint = " → 鉴权失败：token 不对或已轮换（向管理员确认）"
        elif "refused" in low or "connecterror" in low or "all connection attempts" in low:
            hint = " → 连不通：服务未启动 / 地址端口不对 / 网络不可达"
        elif "timed out" in low or "timeout" in low:
            hint = " → 超时：网络路径不通或服务卡住"
        elif re.search(r"\b421\b", text):
            hint = " → Host 白名单拒绝：URL 须为裸 IP:port（勿走别名/代理）"
        elif "redirect" in low or "moved" in low:
            hint = " → 重定向：URL 可能少了 /mcp（或 /sse）路径"
        elif "server returned an error response" in low:
            # streamable 传输层对非 2xx 的合成文案（不带状态码）——真正的状态码
            # 由 _http_status_probe 旁路探测补齐；旁路也失败时给这条兜底。
            hint = " → 服务端以 HTTP 错误拒绝，但本传输层未带状态码（token / URL 路径 / 网络？可 `curl -i` 复核）"
        if hint and hint not in hints:
            hints.append(hint)
    return "".join(hints)


def status_hint(status: int | None) -> str:
    """HTTP 旁路探测状态码 → 提示（streamable 传输层丢失状态码时的补位）。"""
    if status is None:
        return ""
    if status in (401, 403):
        return f" → HTTP {status}：鉴权/白名单拒绝（token 不对或已轮换，向管理员确认）"
    if status == 404:
        return " → HTTP 404：路径不对（端点是否少了 /mcp 或 /sse 后缀？）"
    if status == 421:
        return " → HTTP 421：Host 白名单拒绝（URL 须为裸 IP:port，勿走别名/代理）"
    if status in (400, 405, 406) or 200 <= status < 400:
        return f" → HTTP {status}：鉴权已通过、端点存在（可能是协议/瞬态问题，稍后重试）"
    return f" → HTTP {status}：服务端拒绝了请求"


# =============================================================
# 4. 结果聚合与输出（纯函数）
# =============================================================


def summarize_exit_code(results: list[ProbeResult]) -> int:
    """0 = 无失败（⚪ 跳过允许）；1 = 有失败。未知状态按失败处理（fail-closed，防打字错静默变绿）。"""
    return 1 if any(r.status not in ("pass", "off") for r in results) else 0


_MARKS = {"pass": "✓", "fail": "✗", "off": "⚪"}


def format_report(results: list[ProbeResult], secrets: Sequence[str] = ()) -> str:
    """渲染报告。secrets：调用方传入全部 token —— 报告边界上的最后一道清洗闸。"""
    lines = ["=== 团队能力接入自检 ===", ""]
    for r in results:
        lines.append(f"  {_MARKS.get(r.status, '?')} {r.label:<6} {scrub(r.detail, secrets)}")
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    off = sum(1 for r in results if r.status == "off")
    lines.append("")
    if passed == 0 and failed == 0 and off > 0:
        lines.append("⚠️  本次未验证任何链路（全部未配置）——请确认 .env.member 变量名与示例一致（勿写 export / 引号）。")
    lines.append(f"结果: {passed} 通过, {failed} 失败, {off} 跳过")
    if failed:
        lines.append("排查方向见上行提示；仍需帮助：飞书找天一或管理员。")
    return "\n".join(lines)


# =============================================================
# 5. 探测（触网部分；mcp/httpx2 延迟导入，纯函数测试无需安装）
# =============================================================


async def _http_status_probe(cap: Capability) -> int | None:
    """旁路 HTTP 探测：只取状态码用于诊断（streamable 传输层不携带它）。

    鉴权在方法判定之前（401/403 优先于一切）；POST 与真实协议一致。
    尽力而为：任何失败返回 None，不改变主结论、不打印任何请求内容。
    """
    try:
        import httpx2

        verify: object = cap.ca_path if cap.ca_path else True
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {cap.token}"},
            verify=verify,
            timeout=15.0,
        ) as client:
            resp = await client.post(cap.url, json={})
            return resp.status_code
    except Exception:  # noqa: BLE001 — 旁路探测失败不改变主结论
        return None


async def _probe_one(cap: Capability) -> ProbeResult:
    try:
        import httpx2
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamable_http_client

        auth_headers = {"Authorization": f"Bearer {cap.token}"}
        verify: object = cap.ca_path if cap.ca_path else True

        if cap.transport == "sse":
            def _factory(**kwargs):
                return httpx2.AsyncClient(verify=verify, follow_redirects=True, **kwargs)

            connect = sse_client(
                cap.url, headers=auth_headers, timeout=30.0, sse_read_timeout=60.0,
                **({"httpx_client_factory": _factory} if cap.ca_path else {}),
            )
        else:
            client = httpx2.AsyncClient(
                headers=auth_headers,
                verify=verify,
                timeout=httpx2.Timeout(30.0, read=120.0),
            )

            @contextlib.asynccontextmanager
            async def _streamable():
                async with client:
                    async with streamable_http_client(cap.url, http_client=client) as (read, write):
                        yield read, write

            connect = _streamable()

        async with connect as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                if not names:
                    return ProbeResult(cap.name, cap.label, "fail",
                                       f"[{display_url(cap.url)}] MCP 已连通但工具列表为空（服务端配置异常？）")
                preview = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
                return ProbeResult(cap.name, cap.label, "pass",
                                   f"{len(names)} 个工具可用（{preview}）")
    except Exception as exc:  # noqa: BLE001 - 任意失败 = 该项 ✗，不是脚本错误
        leaves = _leaf_exceptions(exc)
        if len(leaves) == 1 and leaves[0] is exc:
            detail = f"{type(exc).__name__}: {exc}"
        else:
            detail = f"{type(exc).__name__}({len(leaves)} 个子异常): " + "; ".join(
                f"{type(e).__name__}: {e}" for e in leaves)
        # 异常/服务端文本可能回显凭据 —— 源头清洗。httpx2 的 HTTPStatusError 会把
        # **未脱敏的原始 URL**（userinfo/query 全带）整条写进消息；带凭据时连同分段
        # 一并清洗（归一化差异下分段仍命中），干净 URL 不洗（别误伤展示地址）。
        detail = scrub(detail, (cap.token, *url_secrets(cap.url)))
        hint = hint_for(exc)
        if cap.transport == "streamable-http" and (not hint or "服务端以 HTTP 错误拒绝" in hint):
            # 传输层把状态码吞成合成文案（不含 401）时，旁路 HTTP 探测**补位（首选）**；
            # 旁路失败才保留 hint_for 的兜底文案 —— 注意次序：兜底文案不能短路旁路探测。
            probe_hint = status_hint(await _http_status_probe(cap))
            if probe_hint:
                hint = probe_hint
        return ProbeResult(cap.name, cap.label, "fail",
                           f"[{display_url(cap.url)}] MCP 会话失败: {detail}{hint}")


async def _probe_all(caps: list[Capability]) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for cap in caps:
        if not cap.url:
            results.append(ProbeResult(cap.name, cap.label, "off", "未配置（跳过）"))
            continue
        results.append(await _probe_one(cap))
    return results


# =============================================================
# 6. main
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="团队能力接入自检（不打印 token）")
    parser.add_argument("--env-file", help="env 文件（宿主机运行时用；容器内由 docker --env-file 注入）")
    parser.add_argument("--ca", help="覆盖内网默认 CA（默认容器内 /ca/，宿主机自动回退仓库 docker/tianyi-bot/ca/）")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    env: dict[str, str] = dict(os.environ)
    if args.env_file:
        env_file = Path(args.env_file).expanduser()
        if not env_file.is_file():
            print(f"❌ env 文件不存在: {env_file}", file=sys.stderr)
            return 2
        env.update(read_env_file(env_file))

    default_ca = args.ca or env.get("MEMBER_CHECK_CA") or resolve_default_ca()
    caps = parse_capabilities(env, default_ca)
    errors = config_errors(caps, ca_exists=lambda p: Path(p).is_file())
    if errors:
        for err in errors:
            print(f"❌ {err}", file=sys.stderr)
        return 2

    for cap in caps:
        if cap.url and (warning := token_length_warning(cap.token)):
            print(f"⚠️  {cap.label}：{warning}")

    import asyncio
    try:
        results = asyncio.run(_probe_all(caps))
    except KeyboardInterrupt:
        print("❌ 被中断", file=sys.stderr)
        return 2

    # 报告边界清洗集：token + 各端点 URL 中**带凭据的部分**（同上——异常文本可能回显
    # 原始 URL；干净 URL 不洗，保留诊断所需的展示地址）
    report_secrets: list[str] = []
    for cap in caps:
        report_secrets += [cap.token, *url_secrets(cap.url)]
    print(format_report(results, secrets=report_secrets))
    return summarize_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
