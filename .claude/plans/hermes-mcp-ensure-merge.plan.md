# Plan: 步骤 1 收尾 + 三个 Hermes MCP 接线脚本合一

**来源**: 2026-11-12 对 `hermes-agent-manifest.plan.md` 实现结果的核对
**复杂度**: Small × 2（A 只动步骤 1 新增的文件；B 新增 1 个脚本、3 个旧脚本改为转发）
**分支**: 继续用 `refactor/hermes-agent-manifest`（worktree：`.claude/worktrees/hermes-agent-manifest`）
**顺序**: A 提交后再做 B，两个独立提交

---

# A. 步骤 1 收尾

## 核对结论

符合计划的部分：10 个文件都按清单创建/修改；`start.sh`、接线脚本、`.env*`、Dockerfile、现有测试一行未动；`docker-compose.yml` 的差异只有注释、空行和两处行尾注释；四个服务的配置 hash 与线上一致；`--check` 的退出码符合 0/1/2 约定。

与计划不符的四处：

| # | 偏差 | 依据 | 影响 |
|---|---|---|---|
| 1 | 新测试文件**单独运行会失败**（`ModuleNotFoundError: No module named 'scripts'`） | `pytest tests/test_render_agents.py` 实测；全量能过是因为别的测试先把仓库根目录塞进了 `sys.path` | 真缺陷。本仓其余测试用 `importlib.util.spec_from_file_location` 按路径加载（24 处） |
| 2 | 清单里多了 `env_order` 和 `fragments`，每个 agent 要手写约 17 行变量顺序 | 计划写的是「变量顺序无所谓」；实测把四个服务的变量全部按字母重排，hash 不变 | 这套机制没有必要，而且让「加一个 agent 只写差异」落空 |
| 3 | `feishu_env_prefix`、由 `profile` 推出 `HERMES_PROFILE` 没有实现，agent 文件里是手写的 | 对照计划的清单结构 | 同一件事要写两遍；`zotero` 变量组也因此被拆成两个（共 7 组，计划是 6 组） |
| 4 | compose 里原有的 61 行注释，只有各清单文件头部的几行概述留了下来 | `git diff` 统计 | 丢了的包括：GLM 兜底为什么要注入、语雀 key 为什么必须进容器环境、记忆网关地址缺了会怎样、两个守卫测试的指针 |

## Tasks

### A1: 测试改为按路径加载渲染器

`tests/test_render_agents.py` 用 `importlib.util.spec_from_file_location` 加载 `scripts/render_agents.py`，与本仓其余测试一致。

### A2: 清单去掉变量顺序

- 删除 `env_order` 与所有 `fragments`；agent 文件改回计划里的形状：`bundles: [...]` + 自己独有的 `env`
- 渲染顺序固定为：`base.env` → 所选变量组（按列出顺序）→ 推导项 → agent 自己的 `env`
- 推导项：`profile` 推出 `HERMES_PROFILE` 与命令里的 `--profile`；`feishu_env_prefix` 推出 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
- `gdrive` 并回 `zotero`（恢复为 6 个变量组）
- **挂载仍按 agent 显式列出**（挂载顺序影响 hash，这条不变）

### A3: 注释补回清单

对照 `git diff` 里删掉的 61 行，凡是说明原因、事故日期、守卫指针的，都在清单里紧挨对应项补一条英文注释。纯分隔线和重复的不用补。

### A4: 重新渲染并验收

```bash
MAIN="$HOME/code/myopenclaw"
S='hermes,hermes-coder,hermes-finance,hermes-daoyuan'
uv run --with pyyaml scripts/render_agents.py
# worktree 目录名不同会改变项目名与路径解析，必须按主目录解析才能和线上比较
diff <(docker compose -f $MAIN/docker-compose.yml --project-directory $MAIN -p myopenclaw config --hash $S | sort) \
     <(docker compose -f docker-compose.yml       --project-directory $MAIN -p myopenclaw config --hash $S | sort)
docker compose -f docker-compose.yml --project-directory $MAIN -p myopenclaw --dry-run up -d hermes hermes-coder hermes-finance hermes-daoyuan
uv run --with pytest --with pyyaml pytest tests/test_render_agents.py -q          # 单独运行也要过
uv run --with pytest --with pyyaml --with pylatexenc pytest tests/ -q             # 0 skipped
```

预期：compose 里四个服务的变量行**文本顺序会变**，hash 不变，dry-run 四行 `Running`。

### A5: 提交

一个提交，包含步骤 1 的全部文件和两份计划。

---

# B. 三个 Hermes MCP 接线脚本合一

## 摘要

`ensure_hermes_server_monitoring.py`（318 行）、`ensure_hermes_dailyinfo.py`（315 行）、`ensure_hermes_paper_queue.py`（370 行）做的是同一件事：往某个 profile 的 `config.yaml` 写入或移除一个带 Bearer 的 `mcp_servers` 条目。本步把实现收成一份，三个旧文件名保留为转发入口。

## 已实测的事实

| 事实 | 依据 |
|---|---|
| 前两个脚本把名字归一化后**逐行相同** | `diff` 结果 0 行 |
| 三者的差别只有 4 个常量：条目名、`managed_by`、URL 变量名、token 变量名 | 三份 `BLOCK_LINES` 对比 |
| `paper_queue` 是超集：多了版式检查与「兄弟条目丢失」保护，以及 `:no-token` 状态 | 归一化后多出约 55 行 |
| 另外两个脚本**没有**这层保护 | 同上。按 `paper_queue` 里的注释，缺了它时某些缩进/内联写法会产生第二个 `mcp_servers:` 键，把同文件里别的条目静默丢掉 |
| `start.sh` 按前缀消费状态串（`written*` / `removed*` / `failed*` / `conflict*`），`*:no-token` 只对 paper-queue 判断 | `start.sh` 581、938–960、1117、1136 行 |
| 现有覆盖：5 个测试文件共 90 个用例 | `rg -c 'def test_'` |
| 宿主 `python3` 没有 PyYAML | 实测 `import yaml` 失败；所以 `start.sh` 不能直接读清单 |

## 宁缺毋滥原则

- **不改** `scripts/start.sh`：四处调用、脚本文件名、参数、状态串前缀都保持原样
- **不改**现有 5 个测试文件，它们就是行为规格
- **不动** `ensure_hermes_repo_scanner_auth.py`（只补一行 header，形状不同）、两个 OpenClaw 侧脚本、语雀/事务库/Zotero 的注册路径
- **不在清单里加 `mcp:` 字段**，不把 `start.sh` 改成循环 —— 那是再下一步
- 唯一有意的行为变化：server-monitoring 与 dailyinfo 获得 paper-queue 已有的版式保护

## Files to Change

| File | Action | Why |
|---|---|---|
| `scripts/ensure_hermes_mcp.py` | CREATE | 唯一实现，取自 `paper_queue` 版本；条目名与两个变量名由参数给出 |
| `scripts/ensure_hermes_server_monitoring.py` | UPDATE | 改为转发入口：固定三个常量后调用通用实现 |
| `scripts/ensure_hermes_dailyinfo.py` | UPDATE | 同上 |
| `scripts/ensure_hermes_paper_queue.py` | UPDATE | 同上，并打开 `:no-token` 状态 |
| `tests/test_ensure_hermes_mcp.py` | CREATE | 对三个条目名参数化：写入、更新、移除、幂等、版式拒绝 |

## Tasks

### B0: 记录基线

- 跑现有 5 个测试文件，记下通过数
- 把四个调用点对应的线上配置各复制一份到临时目录（爱玛士、爱码士各一份，道元两份），用**主目录**的旧脚本各跑一次，保存输出文件与状态串。临时目录用完即删，过程中不打印配置内容

### B1: 写通用实现

- 从 `ensure_hermes_paper_queue.py` 提取，三个常量变成参数
- `:no-token` 后缀做成显式开关，只有 paper-queue 入口打开；其余两个的状态串与今天逐字相同
- 先确认 `restart_hermes_for_mcp` 对新出现的 `unsupported:*` 状态怎么处理，再决定是否需要在该函数之外兜底

### B2: 三个旧文件改为转发入口

保留现有测试直接调用的函数名（如 `ensure_paper_queue`）和命令行参数。

### B3: 新测试

对三个条目名参数化；重点是此前只有 paper-queue 覆盖的版式拒绝，现在三个都要有。

### B4: 验收

- 现有 5 个测试文件**不修改**，全部通过
- 对 B0 的四份配置副本，用新脚本重跑：输出文件逐字节相同，状态串相同
- 全量测试通过，0 跳过；新旧 Python 文件用 black 格式化

## 回滚

单个提交 `git revert`。脚本只在 `start.sh` 运行时被调用，不影响正在跑的容器。

## 做完之后

三个能力的接线逻辑只剩一份。再下一步才是把它接到清单上：agent 文件里写 `mcp: [server-monitoring]`，由它同时决定容器环境变量和 `config.yaml` 里的注册；渲染器产出一份提交入库的对照表，`start.sh` 的四处调用改成遍历这张表。
