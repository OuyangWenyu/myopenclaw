# Plan: Hermes agent 清单化 — 四个服务定义收成「模板 + 每 agent 一个文件」

**来源**: 2026-11-12 服务解耦讨论（技术演进路径「阶段 1 声明化」的第一步）
**复杂度**: Medium（新增 1 个渲染器 + 5 个清单文件 + 1 个测试文件；`docker-compose.yml` 第 2–345 行改为渲染产物）
**分支**: `refactor/hermes-agent-manifest`（当前在 `main`）

## 摘要

`docker-compose.yml` 里四个 hermes 服务（`hermes` / `hermes-coder` / `hermes-finance` / `hermes-daoyuan`）是四段手写的、各约 100 行的定义，没有任何复用。本计划把它们改成：

- `agents/hermes/base.yaml`：公共字段、公共环境变量、挂载目录、被多个 agent 共用的变量组
- `agents/hermes/<agent>.yaml`：每个 agent 只写自己的差异
- `scripts/render_agents.py`：把清单渲染成**完全展开**的 compose 段，写回 `docker-compose.yml` 的标记区间

**硬约束：运行时零变化。** 渲染产物的解析结果与现状完全相同，`up -d` 不重建任何容器。

## 为什么是「渲染成展开文本」而不是 compose 的 `extends` / YAML 锚点

19 个测试文件读 `docker-compose.yml`，其中至少 10 个直接检查 hermes 服务的环境变量和挂载，读法有两种，都**不经过 compose 解析**：

- PyYAML 读原文件，把 `environment` 当字符串列表（`test_hermes_kanban_wiring` / `test_onedrive_isolation` / `test_coder_feishu_app` / `test_feishu_markdown_card` / `test_model_ids` / `test_hermes_memory_wiring` / `test_hermes_server_monitoring_wiring`）
- 按文本切服务块（`test_hermes_paper_queue_wiring::_service_block`、`test_outlook_email_config::_service_blocks`、`test_dailyinfo_mcp_wiring`）

用 `extends` 之后，这些守卫只能看到每个服务自己的差异部分。正向断言会直接变红；更糟的是**负向断言会空转变绿**（例如「非默认容器不得有 `/opt/onedrive`」「Outlook 变量只在 `hermes`」）。渲染成展开文本则全部守卫原样有效、一个都不用改。

## 已实测的事实（2026-11-12）

| 事实 | 依据 |
|---|---|
| 四个服务完全相同的环境变量键值对 = **23 个** | `docker compose config --no-interpolate` 逐对求交 |
| 公共挂载 = 5 条（`.hermes`、gh、opencode、lark、Zotero） | 同上 |
| **挂载顺序变了，服务的 config hash 就变**（会触发重建） | 临时副本里交换 `hermes-finance` 两条挂载：hash `c531ec2f…` → `e58a2bdc…` |
| 环境变量顺序、注释不影响 hash | 临时副本里调换 `hermes-daoyuan` 两条 env：hash 不变 |
| `docker compose --dry-run up -d <四个服务>` 当前输出四行 `Running` | 可作为「不重建」的直接判据 |
| CI 的 pytest 已带 PyYAML | `.github/workflows/release-checks.yml:48` |

差异分组（清单字段的来源）：

| 变量组 | 用到的 agent | 条数 |
|---|---|---|
| Zotero（`ZOT_DATA_DIR`、`ZOTERO_*`、`GDRIVE_PAPERS_LOCAL_PATH`） | default、coder、finance | 5 |
| server-monitoring（URL + token） | default、coder | 2 |
| `GITCODE_TOKEN` | default、coder | 1 |
| `XIAOMI_API_KEY` | default、coder | 1 |
| 关闭看板派发（`HERMES_KANBAN_DISPATCH_IN_GATEWAY=0`） | default、finance、daoyuan | 1 |
| 共享 rclone 配置（`RCLONE_CONFIG=/opt/data/rclone/rclone.conf`） | coder、finance、daoyuan | 1 |
| 只属于单个 agent 的 | default 12 / coder 8 / finance 5 / daoyuan 9 | — |

## 宁缺毋滥原则

- **不改** `scripts/start.sh`、13 个接线脚本、任何 `.env*`、任何 Dockerfile、任何现有守卫测试
- **不改** `hermes-dashboard`、`claude-code` 及其余 12 个服务（标记区间之外一行不动）
- **不重建、不重启**任何容器；不改变量名、端口、`container_name`
- **不清理**已知冗余（如无消费方的 `XIAOMI_API_KEY`）—— 清理会改变解析结果，另起一步
- **不渲染** Hermes 的 `config.yaml`，不做能力注册表 —— 那是阶段 1 的后续步骤
- **不处理**三个无鉴权端口 —— 与本步无关，单独处理
- 渲染器是**开发期工具**，产物提交入库；`start.sh` 不调用它，运行路径不新增依赖

## Files to Change

| File | Action | Why |
|---|---|---|
| `agents/hermes/base.yaml` | CREATE | 公共服务字段、23 个公共变量、挂载目录、6 个共用变量组 |
| `agents/hermes/default.yaml` | CREATE | 爱玛士：镜像构建、`extra_hosts`、Outlook、OneDrive、`docker.sock` |
| `agents/hermes/coder.yaml` | CREATE | 爱码士：Discord、论文流水线、gitcode 挂载；看板派发在此 |
| `agents/hermes/finance.yaml` | CREATE | finance：群开放策略 |
| `agents/hermes/daoyuan.yaml` | CREATE | 道元：dailyinfo、paper-queue、飞书白名单 |
| `scripts/render_agents.py` | CREATE | 清单 → 展开的 compose 段；`--check` 检查漂移 |
| `tests/test_render_agents.py` | CREATE | 合并规则单测 + 漂移守卫 |
| `docker-compose.yml` | UPDATE | 第 2–345 行换成「标记 + 渲染产物」 |
| `docs/hermes-topology.md` | UPDATE | 增「加一个 agent 怎么做」，指向清单 |
| `CLAUDE.md` | UPDATE | 记录清单是 hermes 服务定义的唯一来源 |

## 清单结构

```yaml
# agents/hermes/base.yaml
service:                 # identical for every agent
  image: myopenclaw/hermes:latest
  restart: unless-stopped
  shm_size: "1g"
  memory: 4G
  cpus: "2.0"
  network: myopenclaw-net
env:                     # the 23 shared pairs
  GH_TOKEN: ${GH_TOKEN:-}
  # ...
mounts:                  # catalog: id -> spec (a list value is an ordered group)
  data: ${HOME}/.hermes:/opt/data
  onedrive: ${HOME}/.hermes-onedrive:/opt/onedrive:rw
  team-skills: [ ... 7 mounts in today's order ... ]
  email-mask: [ ... 2 named volumes ... ]
bundles:                 # env groups used by two or more agents
  zotero: { ... }
  server-monitoring: { ... }
```

```yaml
# agents/hermes/coder.yaml
service: hermes-coder
profile: coder                      # -> HERMES_PROFILE + `--profile coder`
command_extra: --replace
port: ${HERMES_CODER_PORT:-8643}
feishu_env_prefix: CODER_FEISHU     # -> FEISHU_APP_ID=${CODER_FEISHU_APP_ID:-}
bundles: [shared-rclone, zotero, server-monitoring, gitcode, xiaomi]
env:                                # only what no other agent has
  DISCORD_BOT_TOKEN: ${DISCORD_BOT_TOKEN:-}
  # ...
mounts: [data, gh, opencode, lark, gitcode, zotero-data, team-skills, myagentdata, email-mask]
```

规则：

- **挂载按 agent 显式列出、顺序即渲染顺序**（因为顺序影响 hash）；变量顺序无所谓
- 同一个变量键在 `base` / 变量组 / agent 自己的 `env` 里出现两次 → **报错**，不做静默覆盖
- 引用了不存在的变量组或挂载 id → 报错
- 现有服务块里的关键注释（看板派发的原因、coder 飞书应用「绝不许改回」的警告）**搬到对应清单文件**，紧挨它解释的那一项
- 渲染产物的 `environment` 保持 `- KEY=value` 列表形式（现有守卫按这个形状读）

## Tasks

### Task 0: 记录基线（不含密钥，放仓库外的临时目录）

```bash
B=$(mktemp -d)
docker compose config --no-interpolate --format json > "$B/config.json"
docker compose config --hash '*' | sort > "$B/hashes.txt"
uv run --with pytest --with pyyaml --with pylatexenc pytest tests/ -q > "$B/pytest.txt" 2>&1 || true
```

`--no-interpolate` 保留 `${VAR}` 字面量，文件里没有真实密钥。不要用不带该参数的 `config` 落盘。

### Task 1: 写五个清单文件

按上面「差异分组」表逐项填写，来源是现有 `docker-compose.yml` 第 3–345 行，不凭记忆。

### Task 2: 写渲染器 `scripts/render_agents.py`

- 纯函数 `render(base, agents) -> str`，CLI 只做读写
- 默认：重写 `docker-compose.yml` 中 `# >>> GENERATED: hermes agents` 与 `# <<< GENERATED` 之间的内容（标记行 2 格缩进，与现有服务间注释同级，不干扰按文本切块的守卫）
- `--check`：产物与文件现状不同则退出 1；脚本自身错误退出 2（沿用本仓 0/1/2 约定）
- 运行方式：`uv run --with pyyaml scripts/render_agents.py`；用 black 格式化

### Task 3: 写测试 `tests/test_render_agents.py`（pytest）

- 合并规则：重复键报错、未知变量组/挂载 id 报错、挂载组按原顺序展开
- 漂移守卫：`render()` 的结果 == `docker-compose.yml` 标记区间的现状
- 清单一致性：服务名、端口变量、飞书凭据前缀在 agent 之间互不重复（同一飞书应用被两个 agent 共用是 2026-10-12 事故的成因）

### Task 4: 替换 `docker-compose.yml` 第 2–345 行

运行渲染器写入。`hermes-dashboard`（第 347 行起）及之后保持手写。

### Task 5: 验收（全部通过才提交）

```bash
docker compose config --no-interpolate --format json | diff "$B/config.json" -   # must be empty
docker compose config --hash '*' | sort | diff "$B/hashes.txt" -                 # must be empty
docker compose --dry-run up -d hermes hermes-coder hermes-finance hermes-daoyuan # four lines of "Running"
uv run --with pytest --with pyyaml --with pylatexenc pytest tests/ -q            # same pass/fail set as baseline, plus new tests
uv run --with pyyaml scripts/render_agents.py --check                            # exit 0
```

任何一项 diff 非空：**停下来改清单或渲染器，不要去改基线，也不要 `up -d`**。

### Task 6: 文档

- `docs/hermes-topology.md`：加一节，说明服务定义来自 `agents/hermes/`，以及改完要重新渲染
- `CLAUDE.md`：在 Architecture 的 hermes 条目补一句，并写明「不要手改标记区间」

## 风险

| 风险 | 应对 |
|---|---|
| 渲染细节（引号、`:rw` 后缀、`deploy` 结构）导致 hash 变化 | Task 5 的两条 diff 是硬门槛 |
| 有人习惯性地手改生成区间 | 区间头部注明来源；漂移守卫在 CI 里变红 |
| 关键注释在搬迁中丢失 | Task 1 逐条对照原文件搬到清单；评审时对照 diff |
| 某个按文本切块的守卫依赖块内注释 | 基线与改后的 pytest 结果集逐项比对 |

## 回滚

单个提交 `git revert`。因为解析结果前后相同，回滚同样不触发任何容器变化。

## 做完之后的状态（以及还没解决的）

- 加一个 hermes agent 的 compose 部分 = 新增一个清单文件 + 重新渲染
- **仍然要手工做的**：`.env` 里的凭据变量、`start.sh` 里按 profile 名写的初始化与 cron 注册 —— 这是阶段 1 的下一步（能力注册表 + `start.sh` 改为遍历清单）
- 能力矩阵（`docs/hermes-topology.md`）可以直接从清单读出，不再靠人工对照
