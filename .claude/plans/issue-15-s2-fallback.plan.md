# Plan: arXiv 正式DOI升级 — S2 回退 + 测试覆盖

**来源**: Issue #15 缺口分析
**复杂度**: Small（2 个文件，仅补充回退逻辑 + 测试）

## 摘要

`paper-to-zotero.py` 的 `build_item()` 已有 arXiv → 正式 DOI 升级路径，但仅依赖 arXiv API 的 `<arxiv:doi>` 字段（作者手动更新，经常为空）。`fetch_published_doi_s2()` 函数已写好但未接入主流。本计划：将 S2 作为 `<arxiv:doi>` 缺失时的回退，补测试覆盖，保留所有现有行为不变。

## 宁缺毋滥原则

- **不碰** metadata 路由逻辑（L137-305，已验证正确）
- **不碰** pipeline 脚本（`run-paper-pipeline.sh`）
- **不碰** `build_item()` 签名和返回值结构
- **不改** arXiv API / CrossRef API / S2 API 的调用方式
- 只做一件事：在 L131 的 `if published_doi:` 分支后加一个 `elif arxiv_id:` 走 S2

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| 命名 | `paper-to-zotero.py:76-98` | `fetch_published_doi_s2()` 已存在，直接复用 |
| 错误处理 | `paper-to-zotero.py:36-37` | `return None` on any failure，调用方 fallback |
| 日志 | `paper-to-zotero.py:397` | `print("   ℹ️  ...")` to stderr |
| 测试 | `test_paper_to_zotero.py:41-184` | `patch.object(_mod, "fetch_*")` + `_mod.build_item()` |
| 测试 fixture | `test_paper_to_zotero.py:25-35` | `_make_creators()` helper 可复用 |

## Files to Change

| File | Action | Why |
|---|---|---|
| `docker/hermes/scripts/paper-to-zotero.py` | UPDATE L128-135 | 加 S2 回退：arXiv API 无 `<arxiv:doi>` 时调用 `fetch_published_doi_s2()` |
| `docker/hermes/scripts/tests/test_paper_to_zotero.py` | UPDATE | 新增 `TestArxivUpgrade` 类，覆盖 5 个场景 |

## Implementation Flow（TDD + Review Loop）

```
┌──────────┐    ┌──────────┐    ┌──────────────┐
│ 1. RED   │──▶│ 2. GREEN │──▶│ 3. /review   │
│ 写测试    │    │ 最小实现  │    │ 独立 agent   │
└──────────┘    └──────────┘    └──────┬───────┘
                                       │
                              ┌────────▼──────┐
                              │ PASS?          │
                              │ 是 → 结束      │
                              │ 否 → 回到 RED  │
                              └───────────────┘
```

## Tasks

### Task 1: 新增测试 `TestArxivUpgrade`（RED）

- **文件**: `docker/hermes/scripts/tests/test_paper_to_zotero.py`
- **新增测试类**: `TestArxivUpgrade`，5 个场景：

| # | 测试 | 场景 |
|---|------|------|
| 1 | `test_arxiv_doi_upgrades_via_arxiv_api` | arXiv `<arxiv:doi>` 存在 → 升级为正式 journalArticle |
| 2 | `test_arxiv_doi_falls_back_to_s2` | arXiv `<arxiv:doi>` 缺失，S2 返回正式 DOI → 升级 |
| 3 | `test_arxiv_doi_stays_preprint_when_both_fail` | arXiv 无 `<arxiv:doi>`，S2 也返回 None → 保持 preprint |
| 4 | `test_arxiv_source_preserved_after_upgrade` | 升级后 extra 仍保留 `repository: arXiv` 等字段 |
| 5 | `test_non_arxiv_doi_untouched` | 普通 DOI 不受影响，不调用 `fetch_arxiv` 或 S2 |

- **Mock 策略**: `patch.object(_mod, "fetch_crossref")` + `patch.object(_mod, "fetch_arxiv")` + `patch.object(_mod, "fetch_published_doi_s2")`
- **验证**: `pytest` 全部 FAIL（RED）

### Task 2: 实现 S2 回退逻辑（GREEN）

- **文件**: `docker/hermes/scripts/paper-to-zotero.py` L128-135
- **改动**: 在 `if published_doi:` 分支后加 `elif arxiv_id:` 走 S2
- **同时加诊断输出**: 升级成功/失败时 print 到 stderr（ℹ️ 级别，不改变 stdout JSON）
- **验证**: `pytest` 全部 PASS（GREEN）

### Task 3: Review loop

- 调用 `/review` — 独立 agent 审查代码
- 有 CRITICAL/HIGH → 回到 Task 1 修测试或 Task 2 修实现
- PASS → 完成

## 验证命令

```bash
cd docker/hermes/scripts/tests
python3 -m pytest test_paper_to_zotero.py -v
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| S2 rate-limit 影响 paper-fetch | Low | S2 只在 arXiv API 无结果时调用（fallback，非首选）；单次请求 + 短超时 |
| 测试 mock 过多导致假阳性 | Medium | 每个测试只 mock 外部 API，`build_item()` 逻辑本身不 mock |
| 现有测试回归 | Low | 跑全量测试确认，不修改任何现有测试 |

## Acceptance

- [ ] 5 个新测试全部 PASS
- [ ] 15 个现有测试无回归
- [ ] `/review` 独立 agent 无 CRITICAL/HIGH
