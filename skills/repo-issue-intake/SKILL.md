---
name: repo-issue-intake
description: |
  把「在别处看到的、可能对我们某个仓库有用」的东西整理成一条 issue，提到我们自己的仓库（GitHub OuyangWenyu/iHeadWater、GitCode dlut-water）。用户发来仓库/链接/工具/文章并问「这个对我们哪个仓库有用」「提个 issue」「记下来给 X 仓库」，或要求给自有仓库建需求/bug issue 时使用。只写白名单内的自有仓库，先查重、再创建，最后把 issue 链接回报到当前会话。
version: 1.0.0
metadata:
  hermes:
    tags: [issue, github, gitcode, intake, triage]
---

# 仓库 Issue 入口（repo-issue-intake）

**你做的事**：判断「这东西对我们哪个仓库有用」→ 写成一条可执行的 issue →
提到该仓库 → 把链接回报给用户。不是转发链接，也不是写一篇综述。

工具已预配好：`gh`（GitHub）与 `gc`（GitCode）在本容器里可直接用，凭据由宿主注入。
**不要**运行 `gh auth login` / `gc auth login`，**不要**读取或打印 token，
**不要**修改 `/opt/gh-config` 与 `/opt/gitcode-config`。

## 六条铁律

1. **只写自有仓库。** 目标必须在 `references/repos.md` / `scripts/repos.json` 的
   39 个仓库内（GitHub `OuyangWenyu/*`、`iHeadWater/*`；GitCode `dlut-water/*`）。
   **第三方仓库一律不建 issue**——哪怕对方是上游、哪怕问题出在对方身上。
   上游的问题写进我们自己的仓库（说明「上游 X 的行为导致我们 Y 受影响」）。
2. **一次请求最多 1 条 issue。** 用户没明确要求批量时，不要连开多条。
3. **先查重。** 同一仓库里已有近似标题的 issue（脚本会算相似度）→ 不新建；
   能补充就建议在原 issue 下评论，确实不同才加 `--force`。
4. **可追溯。** 正文必须有 `## 来源`：链接、仓库名，或「对话：用户口述」。
5. **不泄露。** 正文里不得出现 token、密码、私钥、内网地址、客户数据。
   脚本会做模式扫描并拒绝。
6. **不越权。** 不 close / comment / edit / merge / push，只 `issue create`。

## 流程

1. **接住来源。** 用户给的可能是仓库 URL、文章、一段描述、一个包名。
   需要更多信息时用 `gh repo view <owner/repo> --json description,url`、
   `gc repo view -R <owner/repo> --json`、`web_search`；**不要 clone 大仓库**。
2. **提炼成一句话能力。** 例如「用 STAC API 批量拉遥感影像」而不是「一个很酷的项目」。
3. **选目标仓库。** 查 `references/repos.md` 的定位表；按「这个能力补的是谁的缺口」选，
   不是按「来源属于谁」选。规则：
   - 先看表里每个仓库「适合接的 issue」一栏，选最贴近的一个；
   - 同名仓库在 GitHub 与 GitCode 各一份（myopenclaw / hydroutils / torchhydro）时
     **优先 GitHub**（上游）；只写裸名字时脚本自动选 GitHub，写 `dlut-water/...`
     时脚本会打印 `MIRROR=` 提醒；
   - 两个仓库都说得通时选**主责仓库**，并在正文「备选」里写明另一个，不要开两条；
   - 都不贴近 → **不要硬凑**。回复用户：列 2–3 个候选仓库和理由，请其定夺。
4. **起草。** 按 `references/issue-template.md` 写正文到临时文件
   （`/tmp/intake-<slug>.md` 或 `~/tmp/…`，正文较长时用文件，避免 shell 引号问题）。
   标题只写「做什么」，≤ 120 字，不带 emoji、不写「建议」前缀。
5. **过脚本。** 先 `--dry-run` 看目标、查重结果、字节数；确认无误再去掉 `--dry-run`。
6. **回报。** 把 `ISSUE_URL=` 那行给的链接原样发给用户，附一句「提到了
   `<owner/repo>#<n>`，理由：<一句话>」。**只报你验证过的链接**
   （脚本会回读 issue 确认存在）。

## 命令

脚本在 skill 目录下（容器内绝对路径）：

```bash
SKILL=/opt/hermes-skills/repo-issue-intake
python3 $SKILL/scripts/file_issue.py --list-targets          # 看白名单（39 条）

# GitHub
python3 $SKILL/scripts/file_issue.py \
  --repo iHeadWater/aisecretary \
  --title "接入图表设计能力：结构化出图走 PNG/SVG" \
  --body-file /tmp/intake-aisecretary.md --dry-run

# GitCode（只有 dlut-water 独有的仓库需要显式平台；镜像仓库默认 GitHub）
python3 $SKILL/scripts/file_issue.py \
  --repo dlut-water/zhixun-core --platform gitcode \
  --title "为预报结果增加不确定性区间" \
  --body-file /tmp/intake-zhixun-core.md
```

退出码：`0` 成功（dry-run 通过也算）/ `1` 被护栏拒绝（不在白名单、重复、缺来源、
疑似凭据）/ `2` 执行错误（命令缺失、鉴权失败、API 失败）。
**看到 1 或 2 都不要原样重试**：按输出里的原因修，或如实告诉用户哪里不通。

## 正文模板

```markdown
## 来源
- 链接：<URL，或「对话：用户口述，无外部链接」>
- 平台/仓库：<owner/repo>
- 看到的时间：<YYYY-MM-DD>

## 是什么
（2–4 句，只讲事实）

## 为什么对我们有用
- 对应本仓：<owner/repo> 的 <模块/痛点>
- 可复用的部分：
- 预期收益：

## 建议做法
1. …
2. …

## 风险 / 成本
- …

## 验收标准
- [ ] …

---
> 由道元整理（repo-issue-intake），供人工判断是否排期；未复制第三方代码。
```

## 查重与重复

脚本会拉该仓库最近 60 条 open+closed issue，按标题相似度（≥0.72）比较：

- 命中 → 默认拒绝，并打印候选链接。**先看链接**：如果就是同一件事，
  告诉用户「已有 #N」，不要 `--force`。
- 只有确认「同主题但不同诉求」才加 `--force`，并在正文里写清与 #N 的差异。

## 失败处理

| 现象 | 处理 |
|---|---|
| `gh` / `gc` 401/403 | 凭据由宿主注入，**不要自己登录**。告诉用户「仓库写权限未就绪」，停止。 |
| 404（仓库不存在/无权限） | 核对 `references/repos.md` 的 slug；不要换到第三方仓库。 |
| 限流（rate limit） | 停止本次写入，把 reset 时间告诉用户，不要重试轰炸。 |
| 查重接口失败 | 脚本会拒绝创建（不盲写）。告诉用户稍后再试。 |
| 创建成功但回读失败 | 先 `gh issue list -R <repo> -L 5` / `gc issue list -R <repo> -L 5` 确认，**不要重复创建**。 |

## 反例（不要这样做）

- ❌ 用户发来 `pallets/flask` 的一个 issue，就给 `pallets/flask` 建 issue。
- ❌ 「这个项目不错」→ 直接建一条没有具体诉求的 issue。
- ❌ 一次对话里给 5 个仓库各建一条「调研一下 X」。
- ❌ 把 token、内网 IP、客户名写进正文。
- ❌ 建完只说「已创建」，不给链接、不说提到了哪个仓库。
- ❌ 用 `gh issue create` 裸命令绕过脚本（白名单/查重/凭据扫描都会被绕过）；
  确需调试时也必须先 `file_issue.py --dry-run`。