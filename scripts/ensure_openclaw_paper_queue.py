#!/usr/bin/env python3
"""
ensure_openclaw_paper_queue.py — 幂等地把论文清单接入虾酱的 openclaw.json

由 scripts/start.sh 调用。做四件事，全部**只在需要时才写回**（内容是"手术"而非覆盖：

  1. mcp.servers.paper-queue     —— 注册 stdio MCP server
  2. plugins.load.paths          —— 指向身份注入插件目录
  3. plugins.allow               —— **仅当白名单已存在时**追加；不存在就绝不创建
  4. plugins.entries             —— 启用插件（含 hooks.allowConversationAccess）
  5. skills.entries.paper-fetch  —— 置 false（机器强制「只记不下」，不靠提示词）

退出码: 0 成功（无论是否改动）; 2 配置不可读/不可写

用法:
  python3 scripts/ensure_openclaw_paper_queue.py [配置路径]
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

PLUGIN_ID = "paper-queue-actor"
PLUGIN_DIR = "/home/node/.openclaw/extensions/paper-queue-actor"   # 容器内路径
MCP_NAME = "paper-queue"
SECRET_ENV = "PAPER_QUEUE_ACTOR_SECRET"
DISABLED_SKILLS = {"paper-fetch": {"enabled": False}}

# 归属签名密钥：插件与服务端共享。存在的意义不是"保密"（配置文件里的东西模型读得到），
# 而是**模型算不出 HMAC** —— 于是"插件没加载/被摘掉"这种失败会变成响亮的拒绝写入，
# 而不是静默地改用模型自己填的 actor_id（伪造出来的行和真实的行长得一模一样）。
#
# 生成一次后复用；轮换 = 删掉 config.secret 再跑一次本脚本。
def _secret(data: dict) -> str:
    existing = (data.get("plugins", {}).get("entries", {})
                .get(PLUGIN_ID, {}).get("config", {}).get("secret"))
    if isinstance(existing, str) and existing:
        return existing
    return secrets.token_hex(32)


def apply(config_path: Path) -> bool:
    """就地修改配置。返回是否发生了改动。"""
    data = json.loads(config_path.read_text())
    changed = False
    secret = _secret(data)

    # ── 1. MCP server（保留运维手工加的其它键，只覆盖我们负责的那两个）──
    servers = data.setdefault("mcp", {}).setdefault("servers", {})
    server = servers.get(MCP_NAME)
    if not isinstance(server, dict):
        server = {}
    want_server = {
        "command": "python3",
        "args": ["/home/node/.openclaw/skills/paper-queue/mcp_server.py"],
    }
    for key, value in want_server.items():
        if server.get(key) != value:
            server[key] = value
            changed = True
    # env 里只写我们这一项，**保留运维手工加的其它环境变量**（整个对象替换会把它们
    # 无声丢掉，而那种丢失的表现是"改了路径但服务端还在读老库"）。
    env = server.get("env")
    if not isinstance(env, dict):
        env = {}
    if env.get(SECRET_ENV) != secret:
        env[SECRET_ENV] = secret
        changed = True
    if server.get("env") is not env:
        server["env"] = env
        changed = True
    if servers.get(MCP_NAME) is not server:
        servers[MCP_NAME] = server
        changed = True

    # ── 2/3/4. 插件 ──────────────────────────────────────────
    plugins = data.setdefault("plugins", {})

    paths = plugins.setdefault("load", {}).setdefault("paths", [])
    if PLUGIN_DIR not in paths:
        paths.append(PLUGIN_DIR)
        changed = True

    # ⚠️ plugins.allow 是"存在即白名单"语义：一旦出现这个键，**没列进去的插件全部不加载**。
    # 所以只有它本来就有（线上确实有 6 条）才追加；配置里没有就别创建 —— 凭空造一个
    # 只含自己的白名单会把别的插件全挡在门外。
    allow = plugins.get("allow")
    if isinstance(allow, list) and PLUGIN_ID not in allow:
        allow.append(PLUGIN_ID)
        changed = True

    entries = plugins.setdefault("entries", {})
    want_entry = {
        "enabled": True,
        "hooks": {"allowConversationAccess": True},
        "config": {"secret": secret},
    }
    if entries.get(PLUGIN_ID) != want_entry:
        entries[PLUGIN_ID] = want_entry
        changed = True

    # ── 5. 禁用 paper-fetch（"只记不下"由机器强制，而不是靠提示词）──
    skills = data.setdefault("skills", {}).setdefault("entries", {})
    for name, value in DISABLED_SKILLS.items():
        if skills.get(name) != value:
            skills[name] = value
            changed = True

    if changed:
        config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return changed


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".openclaw" / "openclaw.json"
    if not path.exists():
        print("skip: 配置不存在", file=sys.stderr)
        return 2
    try:
        changed = apply(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: 无法处理 {path}: {exc}", file=sys.stderr)
        return 2
    print("updated" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
