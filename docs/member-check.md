# member-check — 团队能力接入自检

对三项团队能力做**机器级**连通 + 鉴权检查：MCP 握手（initialize + tools/list）、区分「服务活着但 token 错」（401）与「连不通」、未配置项跳过（⚪）。不经过模型、不改任何状态、**永不打印 token**。

三类用途：

- 成员接入后自检（"我这边到底通没通"）
- 服务验收时的机器预检（自然语言验收之前的确定性检查）
- 服务迁移后验证端点（改的是 `.env.member`，零代码）

## 快速使用（macOS / Linux）

```bash
cp .env.member.example .env.member   # 填 URL / token（.env.member 已在 .gitignore，永不入库）
./scripts/member-check.sh
```

首次会构建检查器镜像（几十秒，之后有缓存秒过）。Docker Hub 拉不动时会自动回退国内镜像源（`docker.m.daocloud.io`）。

## Windows（只需 Docker，无需 bash）

```powershell
docker build -t myopenclaw/member-check:latest docker/member-check
docker run --rm --env-file .env.member `
  -v ${PWD}/docker/tianyi-bot/ca/server-monitoring-ca.pem:/ca/server-monitoring-ca.pem:ro `
  myopenclaw/member-check:latest
```

Docker Hub 拉不动时加 `--build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim` 重试 build。

## 配置（.env.member）

| 规则 | 行为 |
|---|---|
| URL 留空 | 该项跳过（⚪），**不算失败** |
| 填了 URL 但 token 为空 | 配置错误，退出码 2（不静默带病探测） |
| token 含引号/空白/控制字符 | 配置错误（2）——引号包裹、复制截断是最常见来源 |
| token 长度 <32 | 仅警告（继续探测）——先核对是否复制完整再去找管理员 |
| 自签 https 端点 | 默认仅服务器监控加载内网 CA（wrapper 自动挂载到容器 `/ca/`）；其它能力用 `<能力>_MCP_CA` 指定（如 `YUQUE_MCP_CA=/ca/server-monitoring-ca.pem`）。覆盖默认 CA：`.env.member` 里设 `MEMBER_CHECK_CA` 或 CLI `--ca`（wrapper 会透传，注意是**容器内路径**视角） |

三个能力的变量：`SERVER_MONITORING_MCP_URL/TOKEN`、`YUQUE_MCP_URL/TOKEN`、`REPO_SCANNER_MCP_URL/TOKEN`。

## 输出与退出码

```
  ✓ 服务器监控  12 个工具可用（…）
  ✗ 语雀知识库  [https://…] MCP 会话失败: …401… → 鉴权失败：token 不对或已轮换（向管理员确认）
  ⚪ 仓库动态   未配置（跳过）

结果: 1 通过, 1 失败, 1 跳过
```

对 streamable-http 服务（服务器监控 / 仓库动态），传输层可能丢失状态码——检查器会**旁路补一次
HTTP 探测**，把 401/403（鉴权）、404（路径少了 /mcp？）、421（Host 白名单）等还原进提示。
全部未配置时，汇总区（`结果:` 行上方）会出现「⚠️ 本次未验证任何链路」一行（防"全 ⚪ 误读成全通"）。

退出码：**0** = 无失败（⚪ 允许）/ **1** = 有探测失败 / **2** = 配置或脚本错误。适合脚本/监控直接消费。

## 常见排查

| 现象 | 方向 |
|---|---|
| 鉴权失败（401） | token 不对或已轮换 → 找管理员换新的 |
| 连不通 | 内网服务需 VPN/校园网；端点是否抄全（含 `/mcp` 或 `/sse` 后缀） |
| TLS 校验失败 | 服务器监控的 CA 未挂载（走 wrapper 会自动挂） |
| 工具列表为空 | 服务端重启/升级中，稍后重试 |

## 进阶：对着本机服务自检

让检查器容器加入指定 docker 网络即可测本机/主栈内服务（端点写网络内服务名）：

```bash
MEMBER_CHECK_DOCKER_NETWORK=myopenclaw_myopenclaw-net ./scripts/member-check.sh
```

另可用 `MEMBER_CHECK_ENV_FILE=<路径>` 指定其它配置文件（不动 `.env.member` 做失败路径演练）。

## 实现与守卫

- 检查器：`docker/member-check/member_check.py`（依赖 pin `mcp==2.2.0`，与 `scripts/check_server_monitoring_mcp.py` 同版本——"验收时能通的，今天还通"）
- 单测：`uv run --with pytest pytest tests/test_member_check.py`
- 退出码契约（0/1/2）与 token 不回显均有测试钉死
