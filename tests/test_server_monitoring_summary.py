"""摘要脚本纯函数单测（fixture 驱动，不触网）。

fixture 形状对齐服务端权威来源（2026-10-14 从 server-monitoring 仓核对）：
- HostStatus: src/monitoring_core/models.py + adapters/host.py（monitoring.reachable/target/alerts）
- Prometheus 状态: tests/core/test_mcp_server.py（targets.total/up/down/unknown）
- GFS: adapters/rainfall.py（status/primary/visualization/database/failure_evidence[kind]）
- overview/list_projects: 2026-10-13 真实 HTTPS 抓样

钉住的语义（与服务端/AGENTS.md 证据纪律一致）：
- UNKNOWN ≠ 正常：headline 不得把 UNKNOWN 吞进"全部正常"；
- 取数失败必须如实出现在"取数缺口"，绝不假摘要；
- 数字全部来自工具返回，本脚本不做推断。

Run: uv run --with pytest --with pyyaml pytest tests/test_server_monitoring_summary.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


summary_mod = _load("server_monitoring_summary_under_test", "scripts/server_monitoring_summary.py")
checker_mod = _load("check_server_monitoring_mcp_for_summary", "scripts/check_server_monitoring_mcp.py")

GEN_AT = "2026-10-14T08:00:00+08:00"


# ── 真实形状 fixture ──────────────────────────────────────────────

def _host(server_id: str, status: str, *, alerts: list | None = None,
          cpu: float = 11.6, mem: float = 41.9, reachable: bool = True) -> dict:
    """HostStatus 形状按 2026-10-14 真实抓样：monitoring.active_alerts（归一化形状）。"""
    return {
        "server": {"id": server_id, "role": "storage", "metadata": {"environment": "production"}},
        "observed_at": "2026-10-14T00:00:05.000Z",
        "cpu": {"used_percent": cpu},
        "memory": {"available_bytes": 156728700928.0, "total_bytes": 269878263808.0, "used_percent": mem},
        "load": {"1m": 7.01, "5m": 8.35, "15m": 8.94, "threshold_policy": "none"},
        "disk": [{"mountpoint": "/", "device": "/dev/mapper/ubuntu--vg-ubuntu--lv", "used_percent": 21.1,
                  "freshness": "CURRENT", "age_seconds": 0.0},
                 {"mountpoint": "/mnt/disk1", "device": "/dev/sde", "used_percent": 94.7,
                  "freshness": "CURRENT", "age_seconds": 0.0}],
        "monitoring": {"reachable": reachable, "target_health": "up",
                       "freshness": "CURRENT", "active_alerts": alerts if alerts is not None else []},
        "status": status,
        "evidence": [],
        "errors": [],
    }


# 归一化告警形状（2026-10-14 真实抓样；旧版 Prometheus labels/annotations 形状见 LEGACY_ALERT）
DISK_ALERT = {
    "name": "DiskAlmostFull",
    "severity": "warning",
    "state": "firing",
    "value": "94.7",
    "summary": "Disk almost full on pm-minio-01 (10.48.0.86:9100)",
}

LEGACY_ALERT = {
    "labels": {"alertname": "HighCPU", "server": "pm-minio-01", "severity": "warning"},
    "state": "firing",
    "value": "95",
    "annotations": {"summary": "Existing host rule is firing"},
}

PROM_OK = {"reachable": True, "status": "HEALTHY",
           "targets": {"total": 7, "up": 7, "down": 0, "unknown": 0}}

GFS_OK = {
    "project": "rainfall-oa-data", "status": "HEALTHY",
    "primary": {"status": "HEALTHY", "job_running": False, "last_outcome": "SAFE_NOOP",
                "reasons": [],
                "latest_discovered_cycle": {"value": 1759516800.0, "age_seconds": 3600.0},
                "latest_successful_cycle": {"value": 1759516800.0, "age_seconds": 3600.0}},
    "visualization": {"status": "HEALTHY"},
    "database": {"status": "HEALTHY"},
    "stage_results": [{"stage": "input", "value": 0.0, "note": None},
                      {"stage": "grib", "value": 0.0, "note": None}],
    "failure_evidence": [],
}

EVIDENCE_PARTIAL = {
    "project": "rainfall-oa-data", "environment": "production", "component": None,
    "complete": True, "evidence_count": 2, "truncated": False, "errors": [],
    "evidence": [
        {"kind": "host_alert", "component": "host", "server": "pm-minio-01",
         "value": DISK_ALERT, "source": "prometheus:/api/v1/alerts", "observed_at": "2026-10-14T00:00:12.000Z"},
        {"kind": "docker_backend", "component": "docker", "detail": "Docker metadata backend unavailable"},
    ],
}

OVERVIEW_RAINFALL = {
    "schema_version": 1, "project": "rainfall-oa-data", "environment": "production",
    "observed_at": "2026-10-14T00:00:10.000Z",
    "runtime": {"project": "rainfall-oa-data", "status": "UNHEALTHY", "servers": [], "containers": [],
                "monitoring": {}, "application": {}, "evidence": [], "observed_at": "2026-10-14T00:00:10.000Z"},
    "delivery": {"state": "UNAVAILABLE", "error": {"code": "DELIVERY_NOT_CONFIGURED"}},
    "business_acceptance": {"state": "UNKNOWN", "detail": "…"},
    "coverage": {"state": "incomplete", "gaps": ["DELIVERY_UNAVAILABLE", "BUSINESS_ACCEPTANCE_NOT_CONNECTED"]},
}

# 2026-10-14 真实形状：delivery 顶层**没有** state，直接给 ci 子树
OVERVIEW_ZHIXUN = {
    "schema_version": 1, "project": "zhixun-core", "environment": "production",
    "observed_at": "2026-10-14T00:00:11.000Z",
    "runtime": {"project": "zhixun-core", "status": "UNKNOWN", "servers": [], "containers": [],
                "monitoring": {}, "application": {}, "evidence": [], "observed_at": "2026-10-14T00:00:11.000Z"},
    "delivery": {
        "project": "zhixun-core", "environment": "production", "observed_at": "2026-10-14T00:00:11.000Z",
        "ci": {"state": "AVAILABLE",
               "latest_build": {"number": 38, "status": {"value": "SUCCESS"},
                                "branch": {"value": "main"},
                                "finished_at": {"value": "2026-10-12T08:38:49Z"},
                                "tests": {"state": "AVAILABLE", "total": 477, "passed": 477, "failed": 0}}}},
    "business_acceptance": {"state": "UNKNOWN", "detail": "…"},
    "coverage": {"state": "incomplete", "gaps": ["BUSINESS_ACCEPTANCE_NOT_CONNECTED"]},
}

LIST_SERVERS = {"servers": [
    {"id": "pm-minio-01", "role": "storage", "metadata": {"environment": "production"},
     "monitoring_backend": {"type": "prometheus", "identity": "pm-minio-01"},
     "docker_backend": {"type": "gateway", "identity": "pm-minio-01", "endpoint_configured": True}},
    {"id": "weather", "role": "app", "metadata": {"environment": "production"},
     "monitoring_backend": {"type": "prometheus", "identity": "weather"},
     "docker_backend": {"type": "engine_mtls", "identity": "weather", "endpoint_configured": False}},
]}

LIST_PROJECTS = {"projects": [
    {"id": "rainfall-oa-data", "environments": ["production"], "delivery_project": None},
    {"id": "zhixun-core", "environments": ["production", "staging"], "delivery_project": "zhixun-core"},
], "source": "server-monitoring:config/projects.json", "observed_at": "2026-10-14T00:00:09.000Z"}


def _collected(**overrides) -> dict:
    base = {
        "list_servers": LIST_SERVERS,
        "get_server_status:pm-minio-01": _host("pm-minio-01", "DEGRADED", alerts=[DISK_ALERT], cpu=11.6, mem=41.9),
        "get_server_status:weather": _host("weather", "HEALTHY", cpu=0.8, mem=17.2),
        "get_prometheus_status": PROM_OK,
        "get_gfs_status": GFS_OK,
        "get_runtime_failure_evidence": EVIDENCE_PARTIAL,
        "list_projects": LIST_PROJECTS,
        "get_project_overview:rainfall-oa-data": OVERVIEW_RAINFALL,
        "get_project_overview:zhixun-core": OVERVIEW_ZHIXUN,
    }
    base.update(overrides)
    return base


class TestToolSelection:
    def test_summary_tools_are_adopted_client_tools(self):
        """摘要只用客户端策略已采纳的工具 —— 不能用未纳入纪律的工具偷偷取数。"""
        assert set(summary_mod.TOOLS_USED) <= set(checker_mod.REQUIRED_TOOLS)
        assert set(summary_mod.TOOLS_USED) == {
            "list_servers", "get_server_status", "get_prometheus_status", "get_gfs_status",
            "get_runtime_failure_evidence", "list_projects", "get_project_overview",
        }


class TestBuildSummary:
    def test_servers_parsed_with_metrics_and_alerts(self):
        s = summary_mod.build_summary(_collected(), {}, GEN_AT)
        by_id = {x["id"]: x for x in s["servers"]}
        assert by_id["pm-minio-01"]["status"] == "DEGRADED"
        assert by_id["pm-minio-01"]["cpu_percent"] == 11.6
        alert = by_id["pm-minio-01"]["alerts"][0]
        assert alert["name"] == "DiskAlmostFull"  # active_alerts 归一化形状
        assert alert["state"] == "firing"
        assert alert["severity"] == "warning"
        assert "almost full" in alert["summary"]
        assert alert["value"] == "94.7"  # 数值留给下游（JSON 契约可加字段）
        assert by_id["weather"]["alerts"] == []

    def test_legacy_prometheus_alert_shape_still_parsed(self):
        """兼容旧版 labels/annotations 形状（服务端曾用过；向后兼容有守卫）。"""
        collected = _collected()
        collected["get_server_status:pm-minio-01"] = _host("pm-minio-01", "DEGRADED", alerts=[LEGACY_ALERT])
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        by_id = {x["id"]: x for x in s["servers"]}
        assert by_id["pm-minio-01"]["alerts"][0]["name"] == "HighCPU"
        # 未达告警阈值的盘高水位也要可见（AGENTS.md 声明该工具覆盖 disk）
        assert by_id["pm-minio-01"]["max_disk"] == {"mountpoint": "/mnt/disk1", "used_percent": 94.7}
        assert by_id["pm-minio-01"]["load_1m"] == 7.01

    def test_headline_never_says_clear_when_any_fetch_failed(self):
        """有取数缺口时不得出现 ✅ —— 缺口本身就是需要看的异常面。"""
        collected = _collected()
        collected["get_server_status:pm-minio-01"] = _host("pm-minio-01", "HEALTHY")
        ov = dict(OVERVIEW_RAINFALL)
        ov["runtime"] = dict(ov["runtime"], status="HEALTHY")
        collected["get_project_overview:rainfall-oa-data"] = ov
        s = summary_mod.build_summary(collected, {"get_prometheus_status": "TimeoutError: "}, GEN_AT)
        assert "✅" not in s["overall"]["headline"]
        assert "⚠️" in s["overall"]["headline"]
        assert "取数失败" in s["overall"]["headline"]

    def test_overall_headline_flags_anomalies_not_unknowns(self):
        s = summary_mod.build_summary(_collected(), {}, GEN_AT)
        o = s["overall"]
        assert o["alerts_firing"] == 1
        assert "DiskAlmostFull" in o["headline"] or "告警" in o["headline"]
        assert "⚠️" in o["headline"]
        # UNKNOWN（zhixun-core 运行时）单列，不得被吞进"正常"
        assert o["unknown_projects"] == 1
        assert "UNKNOWN" in o["headline"]

    def test_all_healthy_and_unknown_projects_never_say_all_clear(self):
        """全绿 + 有 UNKNOWN：headline 只允许"未见异常"级措辞，同时点名 UNKNOWN。"""
        collected = _collected()
        collected["get_server_status:pm-minio-01"] = _host("pm-minio-01", "HEALTHY")
        collected["get_prometheus_status"] = PROM_OK
        collected["get_gfs_status"] = GFS_OK
        # rainfall overview 改为健康
        ov = dict(OVERVIEW_RAINFALL)
        ov["runtime"] = dict(ov["runtime"], status="HEALTHY")
        collected["get_project_overview:rainfall-oa-data"] = ov
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        assert "⚠️" not in s["overall"]["headline"]
        assert "UNKNOWN" in s["overall"]["headline"]  # zhixun-core runtime UNKNOWN 必须点名

    def test_prometheus_down_listed(self):
        collected = _collected()
        collected["get_prometheus_status"] = {"reachable": True, "status": "DEGRADED",
                                              "targets": {"total": 7, "up": 6, "down": 1, "unknown": 0}}
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        assert s["prometheus"]["down"] == 1
        assert "⚠️" in s["overall"]["headline"]

    def test_gfs_failure_evidence_kinds_surfaced(self):
        collected = _collected()
        gfs = dict(GFS_OK)
        gfs["status"] = "UNHEALTHY"
        gfs["failure_evidence"] = [{"kind": "application_outcome_failure", "component": "gfs-primary"}]
        collected["get_gfs_status"] = gfs
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        assert s["gfs"]["status"] == "UNHEALTHY"
        assert "application_outcome_failure" in str(s["gfs"]["failure_kinds"])

    def test_gfs_stage_failures_and_cycle_ages_surfaced(self):
        """『GFS 处理到哪一步』要能答：stage 级失败与 discovered/success 两个 cycle 的年龄。"""
        collected = _collected()
        gfs = dict(GFS_OK)
        gfs["stage_results"] = [{"stage": "input", "value": 0.0},
                                {"stage": "grib", "value": -1, "note": "stage failed"}]
        gfs["primary"] = dict(gfs["primary"],
                              latest_discovered_cycle={"value": 1759524000.0, "age_seconds": 3600.0},
                              latest_successful_cycle={"value": 1759516800.0, "age_seconds": 10800.0})
        collected["get_gfs_status"] = gfs
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        assert s["gfs"]["stage_failures"] == ["grib"]
        assert s["gfs"]["latest_successful_age_seconds"] == 10800.0
        assert s["gfs"]["latest_discovered_age_seconds"] == 3600.0
        text = summary_mod.render_human(s)
        assert "grib" in text  # stage 失败进人读
        assert "最近成功 cycle 距今 10800.0s" in text
        assert "最新发现 cycle 距今 3600.0s" in text

    def test_gfs_unknown_reasons_surfaced(self):
        """UNKNOWN 要能说清为什么（reasons），不然用户只知道"不知道"。"""
        collected = _collected()
        gfs = dict(GFS_OK)
        gfs["status"] = "UNKNOWN"
        gfs["primary"] = dict(gfs["primary"], status="UNKNOWN",
                              reasons=["PRIMARY_METRICS_TARGET_DOWN"])
        collected["get_gfs_status"] = gfs
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        assert s["gfs"]["reasons"] == ["PRIMARY_METRICS_TARGET_DOWN"]
        assert "PRIMARY_METRICS_TARGET_DOWN" in summary_mod.render_human(s)

    def test_ci_summary_from_overview(self):
        s = summary_mod.build_summary(_collected(), {}, GEN_AT)
        zx = {x["id"]: x for x in s["projects"]}["zhixun-core"]
        assert zx["runtime_status"] == "UNKNOWN"
        assert zx["ci"]["number"] == 38
        assert zx["ci"]["tests"] == "477/477"
        assert zx["ci"]["status"] == "SUCCESS"
        # 新形状 delivery 无顶层 state —— 该回退行必须被钉住（2026-10-14 终验发现
        # 删掉 `or ci.state` 后全套测试仍绿，只有真实抓样能抓到）
        assert zx["delivery_state"] == "AVAILABLE"

    def test_host_alert_evidence_items_named(self):
        s = summary_mod.build_summary(_collected(), {}, GEN_AT)
        ev = s["failure_evidence"]
        assert ev["complete"] is True
        assert ev["items"][0]["kind"] == "host_alert"
        assert ev["items"][0]["name"] == "DiskAlmostFull"
        assert ev["items"][0]["server"] == "pm-minio-01"
        text = summary_mod.render_human(s)
        assert "host_alert" in text and "DiskAlmostFull" in text

    def test_completeness_reports_failures(self):
        collected = _collected()
        del collected["get_gfs_status"]
        failed = {"get_gfs_status": "ConnectTimeout: "}
        s = summary_mod.build_summary(collected, failed, GEN_AT)
        assert s["completeness"]["ok"] == len(collected)
        assert s["completeness"]["failed"] == [{"key": "get_gfs_status", "error": "ConnectTimeout: "}]

    def test_generated_at_passthrough(self):
        s = summary_mod.build_summary(_collected(), {}, GEN_AT)
        assert s["generated_at"] == GEN_AT


class TestClassifyPayload:
    def test_dict_payload_accepted(self):
        payload, error = summary_mod.classify_payload({"a": 1})
        assert payload == {"a": 1}
        assert error is None

    def test_non_dict_payload_becomes_gap_not_silent_drop(self):
        """工具回退成纯文本时：记缺口，不许静默丢弃（假摘要同类风险）。"""
        payload, error = summary_mod.classify_payload("plain text result")
        assert payload is None
        assert "非结构化返回" in error
        assert "plain text" in error


class TestMalformedPayloadTolerance:
    """字段类型意外时不崩整份摘要（降级为缺失/空列表），也不产生幽灵条目。"""

    def test_iter_ids_tolerates_malformed_lists(self):
        assert summary_mod.iter_server_ids({"servers": [{"id": "a"}, "junk", {"no_id": 1}]}) == ["a"]
        assert summary_mod.iter_server_ids({"servers": "not-a-list"}) == []
        assert summary_mod.iter_server_ids({"servers": 42}) == []
        assert summary_mod.iter_server_ids("plain text") == []
        assert summary_mod.iter_project_ids({"projects": [{"id": "p1"}, None]}) == ["p1"]
        assert summary_mod.iter_project_ids({"projects": 42}) == []

    def test_non_list_collections_degrade_gracefully(self):
        """集合字段出现数字/字符串（不可迭代）时不许崩整份摘要。

        上一轮复核实锤：`"disk": "z"`（字符串可迭代）会恰好掩盖这个洞，
        必须用真正的非可迭代类型（42/7）钉住 _list 防护。
        """
        weird_host = {"server": {"id": "s"}, "status": "HEALTHY", "disk": 42,
                      "monitoring": {"alerts": 7}}
        brief = summary_mod._server_brief(weird_host)
        assert brief["max_disk"] is None
        assert brief["alerts"] == []

        weird_gfs = {"status": "UNKNOWN", "primary": {"reasons": "oops"},
                     "failure_evidence": 42, "stage_results": 7}
        gfs = summary_mod._gfs_brief(weird_gfs)
        assert gfs["reasons"] == []
        assert gfs["failure_kinds"] == []
        assert gfs["stage_failures"] == []

        collected = _collected()
        collected["get_runtime_failure_evidence"] = {"project": "rainfall-oa-data",
                                                     "complete": None, "evidence": 42}
        ov = dict(OVERVIEW_RAINFALL)
        ov["coverage"] = {"state": "incomplete", "gaps": 42}
        collected["get_project_overview:rainfall-oa-data"] = ov
        s = summary_mod.build_summary(collected, {}, GEN_AT)  # 不崩即通过
        assert s["failure_evidence"]["count"] == 0
        assert {p["id"]: p for p in s["projects"]}["rainfall-oa-data"]["coverage_gaps"] == []

    def test_wrong_typed_nested_fields_degrade_gracefully(self):
        weird = {"server": "not-a-dict", "cpu": ["oops"], "memory": "x",
                 "monitoring": "y", "load": 3, "disk": "z", "status": "HEALTHY"}
        brief = summary_mod._server_brief(weird)
        assert brief["id"] == "?"
        assert brief["cpu_percent"] is None
        assert brief["max_disk"] is None
        assert brief["alerts"] == []


class TestRenderHuman:
    def test_sections_present_and_discipline_note(self):
        s = summary_mod.build_summary(_collected(), {}, GEN_AT)
        text = summary_mod.render_human(s)
        for token in ("服务器监控摘要", "总览", "服务器", "Prometheus", "项目", "取数完整性", "UNKNOWN"):
            assert token in text, f"人读摘要缺少: {token}"
        assert "DiskAlmostFull" in text
        assert "477/477" in text
        assert "/mnt/disk1 94.7%" in text  # 盘高水位可见
        assert "负载 7.0" in text  # 浮点 1 位小数
        assert "观测" in text  # observed_at 随行标注

    def test_partial_failure_shown_as_gap_not_faked(self):
        collected = _collected()
        del collected["get_prometheus_status"]
        s = summary_mod.build_summary(collected, {"get_prometheus_status": "TimeoutError: "}, GEN_AT)
        text = summary_mod.render_human(s)
        assert "取数缺口" in text
        assert "get_prometheus_status" in text

    def test_alerts_aggregated_by_name_in_human_output(self):
        """同一条告警 5 个实例（真实：5 个盘各触发一条）→ 人读聚合一行，JSON 保明细。"""
        alerts = [dict(DISK_ALERT) for _ in range(5)]
        collected = _collected()
        collected["get_server_status:pm-minio-01"] = _host("pm-minio-01", "DEGRADED", alerts=alerts)
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        text = summary_mod.render_human(s)
        assert "DiskAlmostFull×5" in text
        assert "warning" in text
        assert len({x["id"]: x for x in s["servers"]}["pm-minio-01"]["alerts"]) == 5  # JSON 不聚合

    def test_failure_evidence_rows_aggregated_in_human_output(self):
        """失败证据 5 条同源告警（真实场景）→ 人读一行 ×5；JSON 保留 5 条明细。"""
        collected = _collected()
        collected["get_runtime_failure_evidence"] = {
            "project": "rainfall-oa-data", "complete": True,
            "evidence": [{"kind": "host_alert", "component": "host", "server": "pm-minio-01",
                          "value": dict(DISK_ALERT)} for _ in range(5)],
        }
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        assert s["failure_evidence"]["count"] == 5
        text = summary_mod.render_human(s)
        assert "host_alert: DiskAlmostFull ×5（pm-minio-01）" in text

    def test_many_coverage_gaps_folded_in_human_output(self):
        """缺口多时人读折叠（真实：多个项目各带 5 个模板缺口），JSON 保全量。"""
        ov = dict(OVERVIEW_ZHIXUN)
        ov["coverage"] = {"state": "incomplete", "gaps": ["A", "B", "C", "D", "E"]}
        collected = _collected()
        collected["get_project_overview:zhixun-core"] = ov
        s = summary_mod.build_summary(collected, {}, GEN_AT)
        text = summary_mod.render_human(s)
        assert "缺口 5 项" in text
        assert "['A', 'B', 'C', 'D', 'E']" not in text
        assert {p["id"]: p for p in s["projects"]}["zhixun-core"]["coverage_gaps"] == ["A", "B", "C", "D", "E"]

    def test_failed_server_keeps_placeholder_line(self):
        """单台服务器取数失败：该机不许从"服务器"段整体消失（只留缺口行）。

        断言必须**行级精确**：宽断言（"weather" / "取数失败" 全文出现即可）会被
        缺口清单与 headline 里的"N 项取数失败"满足 —— 删掉占位行代码仍全绿，
        是假守卫（2026-10-14 独立复核用变异法实锤）。
        """
        collected = _collected()
        del collected["get_server_status:weather"]
        s = summary_mod.build_summary(collected, {"get_server_status:weather": "TimeoutError: "}, GEN_AT)
        text = summary_mod.render_human(s)
        assert "  weather: ❌ 取数失败（见取数缺口）" in text.splitlines()

    def test_total_failure_never_fakes_summary(self):
        """全失败 = 明确报"无法获取"，绝不以空数据冒充"一切正常"。"""
        s = summary_mod.build_summary({}, {"list_servers": "ConnectTimeout: "}, GEN_AT)
        text = summary_mod.render_human(s)
        assert "无法获取" in text
        assert "总览" not in text  # 不得出现任何正常摘要骨架
        assert "✅" not in text

    def test_session_level_failure_renders_truthfully(self):
        """会话级失败（__session__ 键）走同一条"无法获取"路径并列出诊断。"""
        s = summary_mod.build_summary({}, {"__session__": "ConnectTimeout:  → 超时：网络路径不通"}, GEN_AT)
        text = summary_mod.render_human(s)
        assert "无法获取" in text
        assert "__session__" in text
        assert "超时" in text


class TestExitCodes:
    def test_exit_code_mapping(self):
        assert summary_mod.summary_exit_code(0) == 0
        assert summary_mod.summary_exit_code(2) == 1
