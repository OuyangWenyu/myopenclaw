# 仓库 Issue 入口（道元 · repo-issue-intake）

道元（`hermes-daoyuan`，飞书文献/情报助手）在 2026-11-24 获得 **GitHub / GitCode CLI 写能力**：
当用户在对话里发来一个仓库、链接、工具或想法，判断「这东西对我们哪个仓库有用」后，
直接给我们**自己的仓库**建一条 issue，并把链接回报到当前飞书会话。

它不是通用 GitHub 机器人：**只建 issue、只写自有仓库**，不评论、不关单、不 push。

## 1. 能力与凭据

| 平台 | 命令 | 凭据通道 |
|---|---|---|
| GitHub | `gh`（镜像内 `/usr/bin/gh`，apt 安装） | `GH_CONFIG_DIR=/opt/gh-config` → `hosts.yml` |
| GitCode | `gc`（`gitcode-cli`，npm 全局） | `GITCODE_TOKEN` 环境变量（gc 优先读 env） |

**为什么 GitHub 走 `GH_CONFIG_DIR` 而不是 `GH_TOKEN`**：Hermes 把 `GH_TOKEN` /
`GITHUB_TOKEN` 列为 Tier-1 secret，会从 terminal 子进程环境里剥掉
（`tools/environments/local_env_policy.py` 的 `_STATIC_PROVIDER_ENV_BLOCKLIST`），
`env_passthrough` 也救不回来。gh 只能读 `hosts.yml`。

**为什么不按 coder 的方式链到 profile home**：道元的 terminal `HOME` 是共享的
`/opt/data`（`profiles/daoyuan/home` 不存在），把 `.config/gh` 链到那里会同时给
finance / 爱玛士开 gh 写权限——`entrypoint-wrapper.sh` 之前专门清掉过这条全局链接。
`GH_CONFIG_DIR` 只作用于**这个容器**，不动共享 HOME。`GITCODE_TOKEN` 不在
blocklist 里，所以 gc 直接读 env 就够。

`gh` 与 coder 共用同一份 `/opt/gh-config`（同一个 owner PAT，`repo` scope）；
`gc` 与 coder 共用 `/opt/gitcode-config`（道元现在也挂了该目录，避免
`/opt/data/.gitcode` 悬空）。两者都不新增 token。

> 授权开关在 `agents/hermes/daoyuan.yaml`：`GH_CONFIG_DIR` + `gitcode` bundle/mount。
> 拿掉这三项就收回能力。守卫 `tests/test_repo_issue_intake.py` 钉住「只有道元有
> `GH_CONFIG_DIR`」。

## 2. 白名单：39 个自有仓库

`skills/repo-issue-intake/scripts/repos.json` 是唯一机器可读清单：

- GitHub `OuyangWenyu/*`（6）+ `iHeadWater/*`（9）
- GitCode `dlut-water/*`（24）
- 来源：`~/code/git-contribution-stats/configs/repos.toml`（每日采集清单，声明式）
- 人类可读的定位表：`skills/repo-issue-intake/references/repos.md`

**为什么不直接用 repo-scanner 的 SQLite**：那张表由 watcher 动态写入，未来可能
因 issue 事件收进非自有仓库；拿它当写权限白名单等于让外部数据决定授权范围。
声明式 TOML 才是正确的来源。

**刷新**（加了仓库之后）：

```bash
python3 skills/repo-issue-intake/scripts/refresh_allowlist.py        # 默认读 repos.toml
python3 skills/repo-issue-intake/scripts/refresh_allowlist.py --sqlite ~/.myagentdata/repo-scanner/repos.sqlite
# 再在 references/repos.md 补一行；测试会检查两边一致
```

目前 `dlut-water/aisecretary`、`dlut-water/HydroForecastEval` 只在 watcher 库里、
不在白名单；要可写就先加进 `repos.toml` 再刷新。

## 3. 三条硬护栏（脚本，不靠模型自觉）

`skills/repo-issue-intake/scripts/file_issue.py`：

1. **白名单**：目标不在 `repos.json` → 退出码 1；第三方仓库一律拒绝。
   同名镜像（myopenclaw / hydroutils / torchhydro）写 GitCode 副本时打印
   `MIRROR=` 提醒上游在 GitHub。
2. **正文合规**：必须有 `## 来源` 段；正文 ≤ 60KB；标题 ≤ 120 字；
   扫描 `ghp_` / `github_pat_` / `gitcode_` / `sk-` / Slack token / 私钥，
   以及**当前环境里真实 token 的值**——命中即拒绝（issue 是公开可读的）。
3. **查重**：拉该仓库最近 60 条 open+closed issue，标题归一化后按
   `SequenceMatcher ≥ 0.72` 判重；命中就拒绝并打印候选链接，`--force` 才放行
   （但仍过白名单与凭据检查）。

创建后脚本会**回读** issue（`gh issue view` / `gc issue view`）再打印 `ISSUE_URL=`，
不会出现「以为建好了其实没有」。查重接口失败时脚本拒绝盲写。

## 4. 使用方式

道元在飞书里被要求时（例如「这个项目对我们哪个仓库有用？提个 issue」）：
读来源 → 选仓库 → 按模板起草 → `--dry-run` → 正式创建 → 回报链接。
完整操作说明在 `skills/repo-issue-intake/SKILL.md`。

手动冒烟（不真正创建）：

```bash
docker compose exec -T hermes-daoyuan sh -lc '
  cat > /tmp/body.md <<EOF
## 来源
- 链接：https://example.com/demo
## 是什么
冒烟测试正文。
EOF
python3 /opt/hermes-skills/repo-issue-intake/scripts/file_issue.py \
  --repo iHeadWater/aisecretary --title "冒烟测试：不要创建" \
  --body-file /tmp/body.md --dry-run'
```

## 5. 运维与排障

```bash
# 白名单
docker compose exec -T hermes-daoyuan python3 /opt/hermes-skills/repo-issue-intake/scripts/file_issue.py --list-targets

# gh：模拟 terminal（token 已被 Hermes 剥掉）
docker compose exec -T --user hermes hermes-daoyuan sh -lc 'env -u GH_TOKEN -u GITHUB_TOKEN gh auth status'

# gc
docker compose exec -T hermes-daoyuan gc auth status

# skill 是否被道元加载
docker compose exec -T hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan skills list | grep repo-issue-intake

# 最近创建（人工核对）
docker compose exec -T hermes-daoyuan sh -lc 'gh issue list -R iHeadWater/aisecretary --limit 5'
```

| 现象 | 原因 / 处理 |
|---|---|
| `gh auth status` 报 not logged in | `/opt/gh-config/hosts.yml` 缺失或 token 失效；改 `.env` 的 `GH_TOKEN` 后重启容器（wrapper 会重写 hosts.yml） |
| gh 显示两个 account（GH_TOKEN + hosts.yml） | 交互式 exec 的 shell 里有 `GH_TOKEN`；agent terminal 里不会。属正常 |
| gc 未登录 | `GITCODE_TOKEN` 没注入或已过期；检查 `.env` 后 `docker compose up -d hermes-daoyuan` |
| `ERROR=gh issue list 失败：HTTP 401` | token 过期或被撤；不要 `gh auth login`，改宿主 `.env` |
| 查重把正常新 issue 挡了 | 先看候选链接；确实不同再 `--force`，并在正文写差异 |
| `ERROR=issue 可能已创建，但输出里没有编号` | **不要再跑一次**；先 `gh issue list -L 5` 确认 |

**限额**：GitHub PAT 5000 req/h（共享给 coder / claude-code），GitCode 有独立限额。
查重 1 次 + 创建 1 次 + 回读 1 次 ≈ 3 次调用；不要批量刷。

## 6. 与现有系统的分工

- **repo-scanner-mcp**：读（采集/日报）；**repo-issue-intake**：写（建 issue）。
- **issue-notifier**：issue/PR 分配变动 → 预报系统群；它不建 issue，也不读道元。
- **道元**：判断与起草；**脚本**：授权与写入门槛。
- **dailyinfo**：道元的情报来源；本能力是「按需」触发，不新增定时任务。

## 7. 已知边界

- 只 `issue create`，没有评论/编辑/关单能力（有意）。
- 查重只覆盖最近 60 条；很老的重复 issue 可能漏判。
- 不自动加 label / assignee（label 名因仓库而异，失败会连带创建失败）。
- 正文的「为什么有用」仍是模型判断，不是事实校验；issue 里明确写了「供人工判断」。
- 道元与 coder 共享同一个 GitHub PAT：道元能写的 GitHub 范围 = 该 PAT 的范围，
  白名单只是收窄入口，不是权限隔离。

## 8. 守卫

```bash
uv run --with pytest --with pyyaml pytest tests/test_repo_issue_intake.py -q
```

覆盖：容器接线（只有道元有 `GH_CONFIG_DIR`）、39 条白名单与文档一致性、
镜像优先 GitHub、未知仓库/重复标题/凭据正文的拒绝路径、dry-run 主流程、
skill 文档不含 token。