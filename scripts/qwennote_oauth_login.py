#!/usr/bin/env python3
"""QwenNote（千问办公 AI听记）MCP 的一次性 OAuth 授权助手。

在 hermes 容器内运行（依赖 mcp SDK 与 tools.mcp_oauth.HermesTokenStorage）：

    docker compose cp scripts/qwennote_oauth_login.py hermes:/tmp/
    docker compose exec hermes /opt/hermes/.venv/bin/python3 /tmp/qwennote_oauth_login.py

流程：打印授权 URL → 浏览器完成登录授权 → 浏览器跳转
``http://127.0.0.1:<port>/callback?code=...`` 并报「无法连接」（预期行为：
回调监听在容器内部，宿主浏览器够不到）→ 把地址栏里的完整 URL 粘贴回终端 →
脚本用 PKCE 换取 token，并以 Hermes 标准格式落盘（HERMES_HOME/mcp-tokens/qwennote.*），
此后 ``hermes mcp test qwennote`` 与网关运行时直接可用。

为什么不用 ``hermes mcp login``：无头容器内该命令的探测会并发启动两条授权流、
抢同一个回调端口，第二条必然 EADDRINUSE 并把进程带崩（2026-10 实测，Hermes 2026.9.x
镜像）。本脚本只跑单条流、纯粘贴换码，不监听任何端口。

首次运行（无缓存 DCR 注册）时，脚本会自行向注册端点登记一个公共客户端
（``token_endpoint_auth_method=none``，redirect_uri 为随机的 127.0.0.1 空闲端口）。
token 与客户端注册信息只写入本机 HERMES_HOME，不进入代码仓库。

模块级只依赖标准库，便于在宿主上直接 import 做单元测试；
Hermes/mcp SDK 的导入都推迟到函数内部（它们只在容器内存在）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import signal
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse

DEFAULT_SERVER = "qwennote"
DEFAULT_ISSUER = "https://minutes.qwennote.cn"
DEFAULT_RESOURCE = "https://minutes.qwennote.cn/mcp"
DEFAULT_SCOPE = "qwennote:read qwennote:write"
DEFAULT_WAIT_SECONDS = 1500  # 粘贴回调 URL 的等待窗口（25 分钟）


class CallbackError(ValueError):
    """粘贴的回调 URL 不合法（缺 code / state 不匹配 / iss 不匹配等）。"""


def make_pkce() -> tuple[str, str]:
    """生成 PKCE (verifier, S256 challenge)。"""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    resource: str,
    scope: str,
) -> str:
    """按 MCP OAuth（含 RFC 8707 resource 参数）拼授权 URL。"""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": resource,
        "scope": scope,
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


def parse_callback(pasted: str, expected_state: str, expected_issuer: str | None = None) -> str:
    """从粘贴的完整回调 URL（或裸 ``?code=..&state=..`` 片段）中提取并校验 code。

    校验：code 存在、state 与本次流程一致、iss 若存在则必须等于期望签发方（RFC 9207）。
    """
    value = pasted.strip()
    if not value:
        raise CallbackError("输入为空")
    query = urlparse(value).query if "?" in value else value
    qs = parse_qs(query)
    code = (qs.get("code") or [""])[0]
    state = (qs.get("state") or [""])[0]
    if not code:
        raise CallbackError("未找到 ?code= —— 请粘贴浏览器地址栏里的完整 URL（不是错误页文字）")
    if state != expected_state:
        raise CallbackError("state 不匹配 —— 该 URL 属于另一次授权流程，请用本次打印的链接重新授权")
    if expected_issuer:
        iss = (qs.get("iss") or [""])[0]
        if iss and iss != expected_issuer:
            raise CallbackError(f"iss 不匹配（{iss}）—— 拒绝向非预期签发方兑换")
    return code


def pick_free_port() -> int:
    """选一个本机空闲端口（仅用于 DCR 注册的 redirect_uri，不真正监听）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_metadata(issuer: str) -> dict:
    """读取授权服务器元数据（RFC 8414）。"""
    return _http_json(issuer.rstrip("/") + "/.well-known/oauth-authorization-server")


def register_client(*, registration_endpoint: str, redirect_uri: str, scope: str) -> dict:
    """RFC 7591 动态客户端注册（公共客户端，auth method = none）。"""
    payload = json.dumps({
        "client_name": "Hermes Agent",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": scope,
    }).encode("utf-8")
    return _http_json(
        registration_endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


def exchange_code(*, token_endpoint: str, code: str, redirect_uri: str, client_id: str,
                  code_verifier: str, resource: str) -> dict:
    """授权码换 token（PKCE，公共客户端 client_id 走请求体）。"""
    body = urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }).encode("ascii")
    try:
        return _http_json(
            token_endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise SystemExit(f"token 端点返回 HTTP {exc.code}: {detail}")


def _die(msg: str, code: int = 1):
    print("ERROR: " + msg, file=sys.stderr, flush=True)
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="QwenNote 听记 MCP 的一次性 OAuth 授权")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Hermes MCP server 名（默认 qwennote）")
    parser.add_argument("--issuer", default=DEFAULT_ISSUER, help="授权服务器 issuer")
    parser.add_argument("--resource", default=DEFAULT_RESOURCE, help="MCP resource（RFC 8707）")
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--timeout", type=int, default=DEFAULT_WAIT_SECONDS,
                        help="等待粘贴回调 URL 的秒数（默认 1500）")
    args = parser.parse_args(argv)

    # 仅容器内有：Hermes 存储与 mcp SDK
    from tools.mcp_oauth import HermesTokenStorage
    import asyncio

    storage = HermesTokenStorage(args.server)

    print(f"读取授权服务器元数据: {args.issuer}", flush=True)
    metadata = fetch_metadata(args.issuer)
    authz = metadata["authorization_endpoint"]
    token_endpoint = metadata["token_endpoint"]

    client_info = asyncio.run(storage.get_client_info())
    if client_info is None:
        redirect_uri = f"http://127.0.0.1:{pick_free_port()}/callback"
        print(f"无缓存客户端注册，执行 DCR（redirect_uri={redirect_uri}）……", flush=True)
        reg = register_client(
            registration_endpoint=metadata["registration_endpoint"],
            redirect_uri=redirect_uri,
            scope=args.scope,
        )
        from mcp.shared.auth import OAuthClientInformationFull
        client_info = OAuthClientInformationFull.model_validate(reg)
        asyncio.run(storage.set_client_info(client_info))
    else:
        redirect_uri = str(client_info.redirect_uris[0])
    client_id = client_info.client_id

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(32)
    url = build_authorize_url(
        authorization_endpoint=authz,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=challenge,
        resource=args.resource,
        scope=args.scope,
    )
    print("\nAUTH_URL: " + url, flush=True)
    print("在浏览器打开上述链接并完成授权；随后浏览器会跳转到 http://127.0.0.1:…/callback\n"
          "并显示「无法连接」（预期）—— 把地址栏里的完整 URL 粘贴到下面并回车：\n", flush=True)

    def _alarm(signum, frame):
        _die(f"等待粘贴超时（{args.timeout}s）—— 请重新运行本脚本获取新链接", 9)
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)

    line = sys.stdin.readline()
    if not line:
        _die("stdin 已关闭，未收到回调 URL", 3)
    try:
        code = parse_callback(line, expected_state=state, expected_issuer=args.issuer)
    except CallbackError as exc:
        _die(str(exc), 4)

    token_json = exchange_code(
        token_endpoint=token_endpoint,
        code=code,
        redirect_uri=redirect_uri,
        client_id=client_id,
        code_verifier=verifier,
        resource=args.resource,
    )
    if not token_json.get("access_token"):
        _die("token 响应中没有 access_token: " + json.dumps(token_json)[:200], 6)

    from mcp.shared.auth import OAuthToken
    try:
        token = OAuthToken.model_validate(token_json)
    except Exception:
        allowed = {k: token_json.get(k) for k in
                   ("access_token", "token_type", "expires_in", "scope", "refresh_token")}
        token = OAuthToken.model_validate(allowed)

    # 持久化 AS 元数据，供运行时的 refresh 与 issuer 校验使用（避免重新发现）。
    try:
        from mcp.shared.auth import OAuthMetadata
        try:
            meta_obj = OAuthMetadata.model_validate(metadata)
        except Exception:
            meta_obj = metadata
        try:
            storage.save_oauth_metadata(meta_obj)
        except TypeError:
            storage.save_oauth_metadata(metadata)
    except Exception as exc:
        print(f"WARN: 元数据落盘失败（不影响本次授权）: {exc}", file=sys.stderr, flush=True)

    storage.bind_issuer(args.issuer)
    asyncio.run(storage.set_tokens(token))
    signal.alarm(0)
    print(
        "TOKENS SAVED:",
        "access=yes" if token.access_token else "access=no",
        "refresh=%s" % ("yes" if token.refresh_token else "no"),
        "expires_in=%s" % token.expires_in,
        flush=True,
    )
    print(f"has_cached_tokens: {storage.has_cached_tokens()}", flush=True)
    print(f"\n完成。验证: hermes mcp test {args.server}", flush=True)


if __name__ == "__main__":
    main()
