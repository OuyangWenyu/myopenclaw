# 模型源切换（modelctl）

一套把「用哪个供应商、哪个模型、谁用哪个」收进一个注册表、再用一个命令渲染到所有
消费方的机制。换源不再靠手改 12 个文件，也不再依赖「记得把那一处也改了」。

## 为什么需要它

换源前的真实形状：

| 消费方 | 配置在哪 | 怎么写模型 |
|---|---|---|
| Hermes ×4（爱玛士/爱码士/finance/道元） | `~/.hermes[/profiles/*]/config.yaml` | 裸 id + `provider:` + `base_url` |
| Claude Code | `~/.claude/settings.json`、`~/.cc-connect/config.toml`、compose env | **Anthropic 协议**，id 可能带 `[1M]` 后缀 |
| 虾酱（OpenClaw 主网关） | `~/.openclaw/openclaw.json` | `provider/model` 前缀 + 上下文/输出/模态元数据 |
| 知汛 / 天一 bot | `.env.zhixun-bot` / `.env.tianyi-bot` → 启动时渲染 | `provider/model` + 元数据 |
| TDAI 记忆 | `.env` 的 `TDAI_LLM_*` | OpenAI 兼容 |
| 语音 TTS/STT | `docker/hermes/voice/ds_voice.py` | DeepSeek 专用接口（不可换） |

三套写法、两种协议、两个上下文单位。漏一处的表现几乎都不是报错，而是**某个 agent
继续跑在旧模型上**，或者上下文从 1M 被元数据截成 128K。

## 三层模型

```
provider（端点 + 钥匙变量名 + 各生态的 provider 名）
   ↑
model（真实 id + 上下文 + 最大输出 + 模态）
   ↑
role（primary / coding / aux，各带一个跨厂商 fallback）
   ↑
binding（哪个消费方用哪个 role；也可以直接钉某个 model）
```

日常切换只动最后两层。

## 文件

| 文件 | 作用 |
|---|---|
| `config/models.toml`（仓库根） | 唯一事实来源。**不含密钥**，只写 `key_env`（变量名） |
| `scripts/modelctl.py`（仓库根） | 唯一写者：`status` / `apply` / `check` / `probe` |
| `.env` | **部署专属值**：模型密钥 + 业务空间端点（`ALIYUN_MAAS_BASE_URL`）；另有一段 `# BEGIN MODELCTL` 托管块（Claude Code 的端点与钥匙变量名） |
| `tests/test_model_registry.py` | 渲染形状与跨文件约定的守卫 |

模型注册表刻意用 TOML：宿主 `python3` 没有 PyYAML（本仓 `ensure_*.py` 都避开了它），
而 `tomllib` 是 3.11+ 标准库 —— 这个脚本因此零依赖、能直接在宿主跑。

### 哪些值放哪里

| 类别 | 例子 | 放哪 | 为什么 |
|---|---|---|---|
| 结构 / 意图 | 有几个 provider、模型能力、谁绑谁、协议路径 | `config/models.toml` | 需要进 git、可 review、可回滚 |
| 密钥 | `DASHSCOPE_API_KEY` | `.env` | 凭据不入库 |
| 部署专属端点 | `ALIYUN_MAAS_BASE_URL`（业务空间域名，含租户 ID） | `.env` | 这是我们这份采购的专属值：换业务空间/地域只改 `.env`，代码与文档里只留 `${ALIYUN_MAAS_BASE_URL}/compatible-mode/v1` 这种**路径形状** |

注册表里的端点用 `${VAR}` 占位；变量缺失时 `probe`/`apply` 会明确报「端点未配置」并
拒绝写入 —— 不会渲染出 `/compatible-mode/v1` 这种「看起来像 URL」的死值。

## 命令

```bash
python3 scripts/modelctl.py status            # 期望值 vs live，谁在漂移 + 缺哪把 key
python3 scripts/modelctl.py check             # 同 status，供守卫用（有问题 exit 1）
python3 scripts/modelctl.py apply --dry-run   # 打印每份文件的 diff + 重启清单
python3 scripts/modelctl.py apply             # 幂等写入（写前备份到 ~/.cache/modelctl/<ts>/）
python3 scripts/modelctl.py apply --only hermes
python3 scripts/modelctl.py probe             # 真打最小请求，验证 key / 端点 / 模型 id
```

`apply` 在**缺密钥时拒绝执行**（`--allow-missing-keys` 可强制，得到的是跑不起来的
配置）。它只保证「写对了」；能不能调通看 `probe`；端到端行为（多轮工具调用、思考
模式回显）还得真跑一个回合。

## 一次切换的标准流程

```bash
# 0. 把部署专属值写进仓库根 .env：
#    ALIYUN_MAAS_BASE_URL=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com
#    DASHSCOPE_API_KEY=sk-…
# 1. 先验证：端点 + key + 模型 id 三者配套
python3 scripts/modelctl.py probe
# 2. 看要改什么
python3 scripts/modelctl.py apply --dry-run
# 3. 灰度：先只切一个消费方（bots 影响面最小）
python3 scripts/modelctl.py apply --only bots
./scripts/start-zhixun-bot.sh && ./scripts/start-tianyi-bot.sh
# 4. 全量
python3 scripts/modelctl.py apply
docker compose up -d hermes hermes-coder hermes-finance hermes-daoyuan claude-code
docker compose up -d openclaw-gateway     # ⚠ 见下面的重启矩阵
# 5. 收尾
python3 scripts/modelctl.py check
```

### 重启矩阵（为什么不是「无脑 restart」）

| 消费方 | 生效方式 | 原因 |
|---|---|---|
| Hermes ×4 | `up -d`（recreate） | 新增/变更的是**容器环境变量**，`restart` 不重读 |
| Claude Code | `up -d`（recreate） | 同上；`settings.json` 在启动时读入 |
| 知汛 / 天一 | 重新跑 `start-*.sh` | 配置由 entrypoint 在启动时从环境变量渲染 |
| 虾酱 | `up -d openclaw-gateway` | ⚠ **启动迁移进行中不要重启**（见 `CLAUDE.md`），否则网关看起来卡死 |

⚠️ 改的若是**烘进镜像**的文件（`docker/claude-code/entrypoint.sh` 这类 `COPY` 进去的），
`up -d` 不够 —— 要先 `docker compose build <service>`。实测踩过：换了 entrypoint 却只
recreate，旧 entrypoint 仍按老逻辑把模型 id 改写成 `deepseek-flash[1M]`，配上百炼端点
就是 404，而且看起来像"配置没生效"。

## 各消费方的适配细节（都是踩过的地方）

- **协议不同**：Claude Code 只能走 Anthropic 兼容端点（`…/apps/anthropic`），其余走
  OpenAI 兼容（`…/compatible-mode/v1`）。注册表里一个 provider 同时声明两条，适配器
  按消费方选。把 Claude Code 指到 `compatible-mode/v1` 的表现是 404。
- **认证头**：百炼的 Anthropic 端点 `x-api-key` 与 `Authorization: Bearer` 都接受。
  适配器把**钥匙和端点一起写进** `settings.json` 的 `ANTHROPIC_AUTH_TOKEN`（这是切换前
  就验证过的路径：cc-connect 拉起 claude 时一定读得到），并且只写一个头 —— 残留旧
  token 才是「拿 A 家的钥匙打 B 家的端点」，而 Claude 只会报 401。entrypoint 另外按
  `CLAUDE_MODEL_KEY_ENV` 间接造一份 `ANTHROPIC_API_KEY`，给没走 settings 的场景兜底。
- **模型 id 三套写法**：Hermes 要裸 id + provider；OpenClaw 要 `provider/model`；
  Claude Code 的 `[1M]` 后缀是 Anthropic 的 1M 上下文 beta 标记，**不能带到百炼**
  （注册表里用 `claude_suffix` 表达，DeepSeek 侧保留 `[1M]`，切回时行为不变）。
- **Claude Code 不认识的模型 id 会按 200K 算上下文**（实测 warning：`doesn't recognize
  the model … assumes a context window of 200k tokens`）。注册表的 `claude_max_context`
  渲染成 `CLAUDE_CODE_MAX_CONTEXT_TOKENS`，不设不报错、只是长会话提前压缩。
- **能力元数据**：OpenClaw 的 provider 段要写 `contextWindow` / `maxTokens` / `input`，
  写错是静默截断。这些数字只从注册表来；bot 的元数据在渲染器里按 env 覆写（模板里
  的 128000 只是参数化之前的缺省值，模板本身必须保持合法 JSON）。
- **跨厂商 fallback**：`role.fallback` 指向另一家的模型，主源整体不可用时还有一条
  独立的路。所以 `DEEPSEEK_API_KEY` 不要删。

## 显式钉模型 vs 钉住不切

两种"不跟随默认"的表达，别混：

- **普通 binding + 专用 role**（推荐，可被 `apply`/`check` 覆盖）：
  例如 TDAI 记忆抽取 —— 它留在官方源（顺带当备用通道的活体探针）。车道定义在
  `[roles.official]`，绑定只写 `[bindings."tdai-memory"] role = "official"`，
  与其它消费方**完全同形**。想让它跟主源一起切，把 role 改成 `"primary"` 即可。
  模型 id 必须是 canonical 的那一个：官方把旧的 `chat` 别名静默路由到 flash
  （响应里的 `model` 字段就是 `deepseek-flash`），而 `/models` 只列 flash 与 pro
  —— 别名既不报错也不在目录里，依赖它等于把「哪天路由撤了」当运气。

  `model = "..."` 直接钉在 binding 上也支持，但只作**临时逃生口**（给单个消费方
  试模型/回滚），不写进常规配置 —— 否则注册表里就有两种写法，谁都得先判断
  "这个为什么不一样"。守卫 `test_every_binding_declares_a_role` 钉着这条。

- **`[pinned]`（结构上切不了，只能手工）**：
  - **voice** — TTS/STT 走 DeepSeek 专用接口，不是兼容协议，换源切不过去。
  - **hermes-bind** — 扫码新增的助手容器在创建时固化了自己的 compose（`env_keys` 已
    预留 `DASHSCOPE_API_KEY`），要收编需先用 bind 工具重建容器。

判断标准很简单：**能不能被渲染器写出来并被 `check` 校验**。能，就当 binding；
不能（协议不同、容器形状在创建时冻结），才放 `[pinned]`。放错位置的代价是
没人校验它 —— `deepseek-chat` 那个别名就是这么在 TDAI 里躺了很久的。

## 回滚

注册表是声明式的，回滚就是改回绑定再 apply：

```bash
# config/models.toml
[bindings."hermes.default"]
model = "deepseek-flash"      # 直接钉，绕过 role
```

```bash
python3 scripts/modelctl.py apply && docker compose up -d hermes
```

每个文件在写入前都备份在 `~/.cache/modelctl/<时间戳>/`（mode 600），也可以直接拷回。

## 密钥轮换

1. 改 `.env`（或对应的 `.env.*-bot`）里的值；
2. `python3 scripts/modelctl.py apply`（把新值同步进 bot env、`openclaw.json` 的
   占位符不需要改）；
3. `docker compose up -d` 受影响的容器。

`status` / `apply` 只回显「有/无」与长度，从不打印密钥值；`--dry-run` 的 diff 也会
把 `*_API_KEY` / `*_TOKEN` 一类键的值打码。

## 已知限制

- 两个 bot 的 **fallback 仍是 zhipu/GLM**（模板只支持一条兜底），没有跟随主备切换。
  需要时改 `docker/*-bot/openclaw.json.template` 的 `agents.defaults.model.fallbacks`。
- 扫码新增的 agent（`~/.hermes-agents/`）不参与 `apply`，见上面的 `[pinned]`。
- DNS：`aliyuncs.com` 已加进 `scripts/setup-dns.sh` 的 resolver 清单；换机后跑一次
  `./scripts/setup-dns.sh`，否则容器可能 `ENOTFOUND` 而不是报鉴权错。

## 守卫

```bash
uv run --with pytest --with pyyaml pytest tests/test_model_registry.py -q
uv run --with pytest --with pyyaml pytest tests/test_model_ids.py -q   # 已下线 id 的扫描
```

`test_model_registry.py` 钉的是渲染形状、幂等性、密钥打码，以及三处容易漏的跨文件
约定：compose 是否把 `DASHSCOPE_API_KEY` 注进了四个 Hermes 容器与 OpenClaw、
entrypoint 会不会把 `deepseek-v4.1-flash-1017` 误判成旧 id 改写、bot 渲染器是否按 env
覆写上下文与最大输出。