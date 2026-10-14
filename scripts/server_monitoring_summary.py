#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==2.2.0"]
# ///
"""server_monitoring_summary.py — 服务器监控「总结信息」获取器

一次收集 5 次固定调用 + 每服务器/每项目各 1 次（当前注册表：2 服务器 + 11 项目 = 18 次），
模板直出人读摘要；--json 输出结构化结果，供下游自取。**本工具定位**：验收证据 / 任一台
连着 UniVPN 的电脑上的随手查 / 未来部署参考——**消费归属是天一（OpenClaw）**，本项目不接
Hermes；天一上服务器后的每日推送 = 天一自己的 OpenClaw cron automation。
**不经过 LLM**：数字与状态全部来自工具返回
（服务端设计原则：事实由程序确定，模型只负责组织语言——本脚本连组织都省了）。

采集面（"现有工具能提供什么就取什么"）：
  list_servers → get_server_status×每台 → get_prometheus_status → get_gfs_status
  → get_runtime_failure_evidence(rainfall-oa-data) → list_projects
  → get_project_overview×每项目

单工具失败不中止：记入"取数缺口"，摘要照出；全部失败则明确报"无法获取"，
绝不拿空数据冒充"一切正常"（UniVPN 断线时你看到的必须是失败，不是假摘要）。

用法:
  uv run scripts/server_monitoring_summary.py                # 人读摘要（默认读仓库 .env.tianyi-bot + 仓库内 CA）
  uv run scripts/server_monitoring_summary.py --json         # 结构化输出（供 cron/下游消费）
  uv run scripts/server_monitoring_summary.py --env-file <路径>

退出码（与 check_server_monitoring_mcp.py 同族）:
  0 — 全部工具成功
  1 — 有工具失败（摘要仍输出，缺口如实标注）
  2 — 配置/脚本错误（URL/token/CA 未配置、参数错误、被中断）

复用检查器的凭据解析与异常诊断（importlib 按路径加载，不做模块搬迁 ——
凭据语义只允许有一个来源）。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GFS_PROJECT = "rainfall-oa-data"  # 目前唯一有业务指标的项目（服务端 registry）


def _load_checker():
    """按路径加载姊妹检查器，复用其凭据解析/异常诊断（唯一来源）。"""
    path = REPO_ROOT / "scripts" / "check_server_monitoring_mcp.py"
    spec = importlib.util.spec_from_file_location("check_server_monitoring_mcp_shared", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_checker = _load_checker()

# 本脚本会调用的工具面。守卫（tests/test_server_monitoring_summary.py）钉住它 ⊆
# 检查器的 REQUIRED_TOOLS —— 不得用未纳入客户端策略的工具偷偷取数。
TOOLS_USED = frozenset({
    "list_servers",
    "get_server_status",
    "get_prometheus_status",
    "get_gfs_status",
    "get_runtime_failure_evidence",
    "list_projects",
    "get_project_overview",
})


# =============================================================
# 1. 采集（薄层，不做业务判断）
# =============================================================


async def collect(url: str, token: str, ca_path: Path, use_tls: bool) -> tuple[dict, dict[str, str]]:
    """按采集面顺序调工具；返回 (collected, failed)。

    collected 的键：list_servers / get_server_status:<id> / get_prometheus_status /
    get_gfs_status / get_runtime_failure_evidence / list_projects / get_project_overview:<id>。
    """
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    collected: dict = {}
    failed: dict[str, str] = {}

    client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        verify=str(ca_path) if use_tls else True,
        timeout=httpx2.Timeout(30.0, read=120.0),
    )
    async with client:
        async with streamable_http_client(url, http_client=client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(key: str, tool: str, args: dict) -> object | None:
                    try:
                        result = await session.call_tool(tool, args)
                        if result.is_error:
                            texts = [getattr(block, "text", "") for block in (result.content or [])]
                            failed[key] = "tool error: " + _checker.summarize("\n".join(t for t in texts if t), 160)
                            return None
                        payload, error = classify_payload(_checker.extract_payload(result))
                        if error:
                            failed[key] = error
                            return None
                        collected[key] = payload
                        return payload
                    except Exception as exc:  # noqa: BLE001 - 单工具失败记缺口，不中止
                        failed[key] = f"{_checker.describe_exception(exc)}{_checker.hint_for(exc)}"
                        return None

                servers = await call("list_servers", "list_servers", {})
                for sid in iter_server_ids(servers):
                    await call(f"get_server_status:{sid}", "get_server_status", {"server": sid})

                await call("get_prometheus_status", "get_prometheus_status", {})
                await call("get_gfs_status", "get_gfs_status", {})
                await call("get_runtime_failure_evidence", "get_runtime_failure_evidence",
                           {"project": GFS_PROJECT})

                projects = await call("list_projects", "list_projects", {})
                for pid in iter_project_ids(projects):
                    await call(f"get_project_overview:{pid}", "get_project_overview", {"project": pid})

    return collected, failed


def classify_payload(payload: object) -> tuple[dict | None, str | None]:
    """工具 payload 必须是有结构的 dict；纯文本回退记入缺口，不许静默丢弃。"""
    if isinstance(payload, dict):
        return payload, None
    return None, f"非结构化返回：{_checker.summarize(payload, 120)}"


# =============================================================
# 2. 摘要构建（纯函数，fixture 驱动单测）
# =============================================================


def _dict(value: object) -> dict:
    """宽容取嵌套 dict：字段出现意外类型时给空 dict（摘要降级为缺失），
    不让整份摘要因一个错类型字段崩掉。"""
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    """宽容取嵌套 list（与 _dict 同一语义——数字/字符串都不许崩整份摘要）。"""
    return value if isinstance(value, list) else []


def iter_server_ids(payload: object) -> list[str]:
    """从 list_servers payload 提取服务器 id（容忍非 dict/缺字段的列表项）。"""
    servers = _dict(payload).get("servers")
    servers = servers if isinstance(servers, list) else []
    return [s["id"] for s in servers if isinstance(s, dict) and s.get("id")]


def iter_project_ids(payload: object) -> list[str]:
    """从 list_projects payload 提取项目 id（同上宽容语义）。"""
    projects = _dict(payload).get("projects")
    projects = projects if isinstance(projects, list) else []
    return [p["id"] for p in projects if isinstance(p, dict) and p.get("id")]


def _alert_brief(alert: dict) -> dict:
    # 归一化形状（服务端 host adapter 的 active_alerts / failure evidence value，
    # 2026-10-14 真实抓样）：{"name", "severity", "state", "value", "summary"}
    if alert.get("name"):
        return {
            "name": str(alert.get("name")),
            "severity": alert.get("severity") or "",
            "state": alert.get("state") or "firing",
            "summary": alert.get("summary") or "",
            "value": alert.get("value"),
        }
    # 兼容旧版 Prometheus 原始形状（labels/annotations）
    labels = _dict(alert.get("labels"))
    annotations = _dict(alert.get("annotations"))
    return {
        "name": labels.get("alertname") or "alert",
        "severity": labels.get("severity") or "",
        "state": alert.get("state") or "firing",
        "summary": annotations.get("summary") or alert.get("summary") or "",
        "value": alert.get("value"),
    }


def _server_brief(payload: dict) -> dict:
    monitoring = _dict(payload.get("monitoring"))
    load = _dict(payload.get("load"))
    disks = [d for d in _list(payload.get("disk"))
             if isinstance(d, dict) and isinstance(d.get("used_percent"), (int, float))]
    max_disk = max(disks, key=lambda d: d["used_percent"]) if disks else None
    # 真实字段是 active_alerts（2026-10-14 抓样）；alerts 为旧名兼容
    alerts_raw = _list(monitoring.get("active_alerts")) or _list(monitoring.get("alerts"))
    return {
        "id": _dict(payload.get("server")).get("id", "?"),
        "status": payload.get("status", "UNKNOWN"),
        "cpu_percent": _dict(payload.get("cpu")).get("used_percent"),
        "memory_percent": _dict(payload.get("memory")).get("used_percent"),
        "load_1m": load.get("1m"),
        "max_disk": ({"mountpoint": max_disk.get("mountpoint"),
                      "used_percent": max_disk.get("used_percent")} if max_disk else None),
        "alerts": [_alert_brief(a) for a in alerts_raw if isinstance(a, dict)],
        "observed_at": payload.get("observed_at"),
    }


def _project_brief(pid: str, payload: dict) -> dict:
    runtime = _dict(payload.get("runtime"))
    delivery = _dict(payload.get("delivery"))
    ci_block = _dict(delivery.get("ci"))
    brief = {
        "id": payload.get("project") or pid,
        "environment": payload.get("environment"),
        "runtime_status": runtime.get("status", "UNKNOWN"),
        "ci": None,
        # 2026-10-14 起新形状：delivery 顶层无 state、直接给 ci 子树——两版兼容
        "delivery_state": delivery.get("state") or ci_block.get("state"),
        "business_acceptance": _dict(payload.get("business_acceptance")).get("state"),
        "coverage_gaps": _list(_dict(payload.get("coverage")).get("gaps")),
        "observed_at": payload.get("observed_at"),
    }
    latest = _dict(ci_block.get("latest_build"))
    if ci_block.get("state") == "AVAILABLE" and latest:
        tests = _dict(latest.get("tests"))
        passed, total = tests.get("passed"), tests.get("total")
        brief["ci"] = {
            "number": latest.get("number"),
            "status": _dict(latest.get("status")).get("value"),
            "branch": _dict(latest.get("branch")).get("value"),
            "finished_at": _dict(latest.get("finished_at")).get("value"),
            "tests": f"{passed}/{total}" if total is not None else None,
        }
    return brief


def _gfs_brief(payload: dict) -> dict:
    primary = _dict(payload.get("primary"))
    discovered = _dict(primary.get("latest_discovered_cycle"))
    success = _dict(primary.get("latest_successful_cycle"))
    kinds = sorted({str(item.get("kind")) for item in _list(payload.get("failure_evidence"))
                    if isinstance(item, dict) and item.get("kind")})
    stage_failures = sorted({str(s.get("stage")) for s in _list(payload.get("stage_results"))
                             if isinstance(s, dict) and s.get("value") == -1})
    return {
        "status": payload.get("status", "UNKNOWN"),
        "primary_status": primary.get("status"),
        "last_outcome": primary.get("last_outcome"),
        "job_running": primary.get("job_running"),
        "reasons": _list(primary.get("reasons")),
        "database_status": _dict(payload.get("database")).get("status"),
        "visualization_status": _dict(payload.get("visualization")).get("status"),
        "latest_successful_cycle": success.get("value"),
        "latest_successful_age_seconds": success.get("age_seconds"),
        "latest_discovered_age_seconds": discovered.get("age_seconds"),
        "stage_failures": stage_failures,
        "failure_kinds": kinds,
    }


_ANOMALY_STATUSES = ("DEGRADED", "UNHEALTHY")


def build_summary(collected: dict, failed: dict[str, str], generated_at: str) -> dict:
    """把工具 payload 汇总成结构化摘要（纯函数，不做任何推断）。"""
    servers = [_server_brief(p) for k, p in sorted(collected.items())
               if k.startswith("get_server_status:") and isinstance(p, dict)]

    prom_raw = _dict(collected.get("get_prometheus_status"))
    targets = _dict(prom_raw.get("targets"))
    prometheus = {
        "reachable": prom_raw.get("reachable"),
        "status": prom_raw.get("status"),
        "total": targets.get("total"),
        "up": targets.get("up"),
        "down": targets.get("down"),
        "unknown": targets.get("unknown"),
    }

    projects = [_project_brief(k.split(":", 1)[1], p) for k, p in sorted(collected.items())
                if k.startswith("get_project_overview:") and isinstance(p, dict)]

    gfs_raw = collected.get("get_gfs_status")
    gfs = _gfs_brief(gfs_raw) if isinstance(gfs_raw, dict) else None

    evidence_raw = collected.get("get_runtime_failure_evidence")
    failure_evidence = None
    if isinstance(evidence_raw, dict):
        items = []
        for item in _list(evidence_raw.get("evidence")):
            if not isinstance(item, dict):
                continue
            if item.get("kind") in ("host_alert", "prometheus_alert"):
                # 真实形状：告警在嵌套 value 里（归一化形状）；兼容直接在 item 上
                brief = _alert_brief(_dict(item.get("value")) or item)
                items.append({"kind": item.get("kind"), "name": brief["name"],
                              "severity": brief["severity"], "state": brief["state"],
                              "server": item.get("server")})
            else:
                items.append({"kind": item.get("kind"), "component": item.get("component"),
                              "summary": _checker.summarize(item, 160)})
        failure_evidence = {"complete": evidence_raw.get("complete"), "count": len(items), "items": items}

    alerts_firing = sum(len(s["alerts"]) for s in servers)
    unknown_projects = sum(1 for p in projects if p["runtime_status"] == "UNKNOWN")

    has_data = bool(collected)
    anomaly = (
        any(s["status"] in _ANOMALY_STATUSES for s in servers)
        or alerts_firing > 0
        or (prometheus["down"] or 0) > 0
        or any(p["runtime_status"] in _ANOMALY_STATUSES for p in projects)
        or (gfs is not None and gfs["status"] in _ANOMALY_STATUSES)
    )

    if not has_data:
        headline = "❌ 未取得任何数据（全部取数失败，见取数缺口）"
    else:
        parts = []
        if servers:
            parts.append(f"{len(servers)} 台服务器（" + "、".join(f"{s['id']} {s['status']}" for s in servers) + "）")
        if alerts_firing:
            parts.append(f"{alerts_firing} 条告警触发中")
        if prometheus["total"] is not None:
            parts.append(f"Prometheus {prometheus['up']}/{prometheus['total']} UP")
        if projects:
            parts.append(f"{len(projects)} 个项目（" + "、".join(f"{p['id']} {p['runtime_status']}" for p in projects) + "）")
        if unknown_projects:
            parts.append(f"{unknown_projects} 个项目状态 UNKNOWN（证据缺失，不等于正常）")
        if failed:
            parts.append(f"{len(failed)} 项取数失败")
        # 有缺口时绝不出 ✅ —— "缺了口还标全绿"是假绿的一种
        headline = ("⚠️ " if (anomaly or failed) else "✅ ") + "；".join(parts)

    return {
        "generated_at": generated_at,
        "overall": {
            "headline": headline,
            "alerts_firing": alerts_firing,
            "unknown_projects": unknown_projects,
            "servers_total": len(servers),
            "projects_total": len(projects),
        },
        "servers": servers,
        "prometheus": prometheus,
        "gfs": gfs,
        "failure_evidence": failure_evidence,
        "projects": projects,
        "completeness": {
            "ok": len(collected),
            "failed": [{"key": k, "error": v} for k, v in sorted(failed.items())],
        },
        "note": "数字与状态全部来自 MCP 工具返回；UNKNOWN 表示证据缺失/未知，不等于正常。",
    }


# =============================================================
# 3. 输出
# =============================================================


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"  # 人读不下长浮点（12.690935672331634%）；JSON 保原值
    return str(value)


def render_human(summary: dict) -> str:
    lines: list[str] = [f"=== 服务器监控摘要（生成于 {summary.get('generated_at', '')}）==="]
    completeness = summary.get("completeness") or {}
    failed = completeness.get("failed") or []

    if completeness.get("ok", 0) == 0 and failed:
        lines.append("")
        lines.append("❌ 无法获取监控数据（全部取数失败）。可能原因：UniVPN 未连接 / 网络不通 / 服务端不可达。")
        lines.append("── 取数缺口 ──")
        for item in failed:
            lines.append(f"  ❌ {item['key']}: {item['error']}")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"总览：{summary['overall']['headline']}")

    lines.append("── 服务器 ──")
    for s in summary["servers"]:
        line = f"  {s['id']}: {s['status']} | CPU {_fmt(s['cpu_percent'])}% 内存 {_fmt(s['memory_percent'])}%"
        if s.get("max_disk"):
            line += f" | 磁盘最高 {s['max_disk']['mountpoint']} {_fmt(s['max_disk']['used_percent'])}%"
        if s.get("load_1m") is not None:
            line += f" | 负载 {_fmt(s['load_1m'])}"
        if s.get("observed_at"):
            line += f" | 观测 {s['observed_at']}"
        lines.append(line)
        # 同名告警聚合成一行（真实场景：同一规则 5 个盘各一条）；JSON 里保留全量明细
        grouped: dict[tuple[str, str, str], list[dict]] = {}
        for a in s["alerts"]:
            grouped.setdefault((a["name"], a["severity"], a["state"]), []).append(a)
        for (name, severity, state), group in grouped.items():
            if len(group) == 1:
                lines.append(f"    ⚠️ {name}({state}): {group[0]['summary']}")
            else:
                lines.append(f"    ⚠️ {name}×{len(group)}（{severity}，{state}）：{group[0]['summary']}")
    seen_servers = {str(s["id"]) for s in summary["servers"]}
    for item in failed:
        if item["key"].startswith("get_server_status:"):
            sid = item["key"].split(":", 1)[1]
            if sid not in seen_servers:
                lines.append(f"  {sid}: ❌ 取数失败（见取数缺口）")
    if not summary["servers"] and not any(f["key"].startswith("get_server_status:") for f in failed):
        lines.append("  （未取到任何服务器状态）")

    p = summary["prometheus"]
    if p.get("total") is not None:
        lines.append("── Prometheus ──")
        lines.append(f"  {p['up']}/{p['total']} UP（down {_fmt(p['down'])}、unknown {_fmt(p['unknown'])}）")

    gfs = summary.get("gfs")
    if gfs:
        lines.append(f"── GFS（{GFS_PROJECT}）──")
        lines.append(f"  状态 {gfs['status']} | 主 worker {_fmt(gfs['primary_status'])}"
                     f" | 最近结局 {_fmt(gfs['last_outcome'])} | 运行中 {'是' if gfs['job_running'] else '否'}")
        detail = f"  数据库 {_fmt(gfs['database_status'])} | 可视化 {_fmt(gfs['visualization_status'])}"
        if gfs["stage_failures"]:
            detail += f" | stage 失败 {gfs['stage_failures']}"
        if gfs["reasons"]:
            detail += f" | 原因 {gfs['reasons']}"
        if gfs["failure_kinds"]:
            detail += f" | 失败证据 {gfs['failure_kinds']}"
        lines.append(detail)
        if gfs["latest_successful_age_seconds"] is not None or gfs["latest_discovered_age_seconds"] is not None:
            lines.append(f"  最近成功 cycle 距今 {_fmt(gfs['latest_successful_age_seconds'])}s"
                         f" | 最新发现 cycle 距今 {_fmt(gfs['latest_discovered_age_seconds'])}s")

    if summary["projects"]:
        lines.append("── 项目 ──")
        for proj in summary["projects"]:
            seg = f"  {proj['id']}: 运行时 {proj['runtime_status']}"
            if proj["ci"]:
                ci = proj["ci"]
                seg += (f" · CI #{_fmt(ci['number'])} {_fmt(ci['status'])}"
                        f"（{_fmt(ci['tests'])} 测试，{_fmt(ci['finished_at'])}）")
            else:
                seg += f" · CI 未接入（{_fmt(proj['delivery_state'])}）"
            if proj["coverage_gaps"]:
                gaps = proj["coverage_gaps"]
                if len(gaps) <= 3:
                    seg += f" · 缺口 {'、'.join(map(str, gaps))}"
                else:
                    seg += f" · 缺口 {len(gaps)} 项（{'、'.join(map(str, gaps[:3]))} 等）"
            if proj.get("observed_at"):
                seg += f" · 观测 {proj['observed_at']}"
            lines.append(seg)

    ev = summary.get("failure_evidence")
    if ev:
        lines.append("── 失败证据 ──")
        lines.append(f"  完整={ev['complete']}，{ev['count']} 条")
        ev_grouped: dict[tuple, list[dict]] = {}
        for item in ev["items"]:
            label = item.get("name") or item.get("summary") or item.get("component", "")
            ev_grouped.setdefault((item.get("kind"), label, item.get("server")), []).append(item)
        for (kind, label, server), group in list(ev_grouped.items())[:8]:
            suffix = f" ×{len(group)}" if len(group) > 1 else ""
            where = f"（{server}）" if server else ""
            lines.append(f"    · {kind}: {label}{suffix}{where}")

    lines.append("── 取数完整性 ──")
    if failed:
        lines.append(f"  {completeness['ok']} 项成功、{len(failed)} 项失败（取数缺口）：")
        for item in failed:
            lines.append(f"  ❌ {item['key']}: {item['error']}")
    else:
        lines.append(f"  {completeness['ok']}/{completeness['ok']} 全部成功")

    lines.append("── 数据口径 ──")
    lines.append(f"  {summary['note']}")
    return "\n".join(lines)


def summary_exit_code(failed_count: int) -> int:
    return 0 if failed_count == 0 else 1


# =============================================================
# 4. main
# =============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="server-monitoring MCP 总结获取（不打印 token）")
    parser.add_argument("--env-file", help="env 文件（默认仓库根 .env.tianyi-bot）")
    parser.add_argument("--url", help="覆盖 MCP URL（默认读 env 文件）")
    parser.add_argument("--token", help="覆盖 token（⚠️ 会进 shell 历史，优先用 --token-file）")
    parser.add_argument("--token-file", help="从文件读取 token")
    parser.add_argument("--ca", help="内网 CA 证书（默认仓库 docker/tianyi-bot/ca/）")
    parser.add_argument("--json", action="store_true", help="结构化 JSON 输出（供 cron/下游消费）")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        url, token, ca_path = _checker.resolve_config(args)
    except _checker.ScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    use_tls = url.startswith("https://")
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        collected, failed = asyncio.run(collect(url, token, ca_path, use_tls))
    except KeyboardInterrupt:
        print("❌ 被中断", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 会话级失败：仍输出可诊断的结果
        collected, failed = {}, {
            "__session__": f"{_checker.describe_exception(exc)}{_checker.hint_for(exc)}"
        }

    summary = build_summary(collected, failed, generated_at)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_human(summary))
    return summary_exit_code(len(failed))


if __name__ == "__main__":
    sys.exit(main())
