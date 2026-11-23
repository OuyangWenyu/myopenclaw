# PPTX 单页截图：方案调研与建议

> 目标：Hermes 在飞书里收到「把第 N 页发我」时，能返回**真实渲染**的单页 PNG，而不是按形状坐标重绘的近似图；同时这套能力要能进入社区版，而不是只在这台机器上手工糊出来。
>
> 调研环境：Hermes 容器（Debian 13 trixie / **aarch64** / Python 3.13 / uid 10000），宿主 Docker VM 约 7.9 GB，`/opt/data` 剩余约 35 GB。
>
> 结论先行：**渲染引擎只选 LibreOffice；打包方式推荐独立 `office-render` 侧车 + Hermes 侧薄 CLI/skill**。镜像层方案可作为「零新增服务」的备选。`/opt/data` 解 deb 前缀、x86_64 AppImage、云转换 API、HTML 重绘都不建议作为主路径。

---

## 1. 先纠正三处会直接踩空的事实

1. **容器是 aarch64，不是 x86_64。**
   Hermes 后来给的 `LibreOffice_26.8.2_Linux_x86-64_deb.tar.gz` 和 LibreItalia 的 `*-x86_64.AppImage` 在这个容器里都跑不起来。TDF 官方**确实有 aarch64 的 deb 包**：
   `https://download.documentfoundation.org/libreoffice/stable/26.8.2/deb/aarch64/LibreOffice_26.8.2_Linux_aarch64_deb.tar.gz`（实测 209 MB，HTTP 200）。
   但更简单的是直接用 Debian 13 的 arm64 包（见第 3 节）。

2. **LibreOffice 官方早就不发 AppImage 了。**
   官方下载站只有 deb / rpm / tar.gz；LibreItalia 的 AppImage 只有 x86_64。AppImage 这条线在 arm64 上直接作废。

3. **「把 deb 解到 `/opt/data` 前缀、不需要 root」这条便携路线，实测走不通。**
   我把 apt 下载的 81 个 deb 全部 `dpkg-deb -x` 到 `/opt/data/tmp/.../lo`（438 MB），`ldd` 用 `LD_LIBRARY_PATH` 能补齐全部依赖，但 `soffice` 启动直接抛：
   `com::sun::star::uno::DeploymentException`（找不到 `unorc`，它去 `usr/lib/aarch64-linux-gnu/unorc` 找，而文件在 `program/unorc`）。
   这不是缺一个环境变量就能稳定解决的；Debian 的 LO 安装布局不是为任意前缀设计的。**不要把它作为社区版方案。**

---

## 2. 需求与约束

| 约束 | 事实 | 对方案的含义 |
|---|---|---|
| 真渲染 | 用户明确不接受重绘近似图 | 只能 LibreOffice / Office / 商业 SDK；排除 HTML 重绘 |
| 运行身份 | Hermes 网关以 uid 10000 跑，无 root、无 apt | 运行期不能装东西；必须镜像预置或独立服务 |
| 架构 | aarch64 | 依赖必须 arm64 可用；Debian apt / TDF aarch64 deb 可用 |
| 持久化 | 容器重建会丢运行时 apt；`/opt/data` 持久 | 运行时 apt 只能算临时验证，不能作为交付 |
| 内存 | Hermes cgroup 4 GB；宿主 Docker VM 7.9 GB，实测只剩约 1.2 GB | 全 64 页导出会把内存打爆；必须单页导出 + 限内存 + 串行 |
| 文件 | 样例 165 MB / 64 页 / 288 个媒体 / 2 个 mp4 | 解析成本高；媒体解码是内存大头 |
| 字体 | 只有 WQY Zen Hei；`微软雅黑` 缺失 | 必须装 Noto CJK 并做 fontconfig 别名，否则中英文混排宽度不稳 |
| 投递 | 飞书 `MEDIA:<绝对路径>`；`.png` 走 `send_image_file` → `im/v1/images` | 输出必须是 hermes 可读的绝对路径；PNG 约 0.5–0.8 MB，远小于飞书图片上限 |

---

## 3. 本机实测（这部分是方案判断的依据）

### 3.1 安装

在运行中的 Hermes 容器里用 root 执行（仅用于验证，**不是最终交付形态**）：

```bash
apt-get install -y --no-install-recommends libreoffice-impress fonts-noto-cjk
```

- 下载 151 MB；安装后 `/usr/lib/libreoffice` 200 MB + `/usr/share/libreoffice` 12 MB + Noto CJK 90 MB。
- 版本：LibreOffice **25.2.3.2**（Debian 13）。
- 不需要 X / FUSE；`--headless` 正常。

### 3.2 全量导出会 OOM

```bash
soffice --headless --norestore --convert-to pdf --outdir /tmp/out deck.pptx
```

- 18 秒时进程被 SIGKILL（`RC=137`），PDF 为 0 字节。
- 容器 cgroup 峰值约 **2.0 GB**（上限 4 GB，没到 cgroup 上限）；宿主 Docker VM 当时只剩约 1.2 GB。
- 判断：**宿主/VM 级内存压力**，不是 Hermes 的 4 GB cgroup 先到顶。换成侧车也不能凭空变出内存，但侧车可以把渲染进程单独限住、失败只死渲染容器。

### 3.3 单页导出：9–11 秒，满足 < 20 s

用 PDF 导出过滤器的 `PageRange` 只导出第 N 页：

```bash
soffice --headless --norestore \
  --convert-to 'pdf:impress_pdf_Export:{"PageRange":{"type":"string","value":"6"}}' \
  --outdir /tmp/out deck.pptx
```

- 第 5 / 6 / 8 页均成功；单页 PDF 约 0.5 MB。
- 端到端 **9–11 秒**（含 soffice 启动）；重复请求同一页仍约 11 秒（没有复用常驻进程）。
- PyMuPDF `page.get_pixmap(dpi=150)` → **2001 × 1125** PNG，0.5–0.8 MB。
- 中文正常，不是方框；渐变、图片、表格、组合形状都由 LO 真实渲染。

### 3.4 版式保真度

- 第 5 页 AlexNet 文字溢出、第 6 页标题压到下方卡片：检查 XML 后确认**源文件本身就是这样**——第 6 页标题是两个 `<a:p>` 段落 + `<a:spAutoFit/>`，第 5 页 AlexNet 是固定框、无 autofit。LO 是**如实还原**，不是渲染 bug。这恰好是截图工具的价值：让用户看到溢出。
- 字体是主要变量：`fc-match "微软雅黑"` 当前返回 **DejaVu Sans**，中文靠逐字回退。必须加 fontconfig 别名（`微软雅黑`/`等线` → Noto Sans CJK SC，`宋体` → Noto Serif CJK SC），否则西文宽度和换行位置会和 PowerPoint 有差异。

### 3.5 飞书投递链路

- `gateway/run_turn.py` 按扩展名分流：`.png` → `adapter.send_image_file` → 飞书 `im/v1/images` 上传拿 `image_key`，发的是**图片消息**，不是文件附件。
- 路径必须是绝对路径，且 gateway（uid 10000）可读。
- 实测 PNG 0.5–0.8 MB，远小于飞书图片上限。

### 3.6 skill 的装载方式

- 仓库里的 `./skills/<name>` 挂到 `/opt/hermes-skills/<name>:ro`，再由 `scripts/start.sh` 的 `ensure_skill_symlink` 链到 `~/.hermes/skills/<name>`（或某个 profile 的 skills 目录）。
- 新增 skill 需要三处：`skills/<name>/`、compose 的只读挂载、`start.sh` 的 symlink。
- 这意味着 skill 本身不依赖镜像重建；但**渲染器**要么在镜像里、要么在独立服务里。

---

## 4. 业界怎么做

| 路线 | 代表 | 原理 | 适配度 |
|---|---|---|---|
| **LibreOffice headless** | Nextcloud/ownCloud、多数机器人、`pptx-thumbnailer` | `soffice --convert-to pdf/png` | **高**；开源界事实标准，真渲染、离线、arm64 可用 |
| **转换侧车** | **Gotenberg**（`gotenberg/gotenberg:9-libreoffice`，多架构含 arm64，LO 路线只出 PDF，最低 512 Mi） | 独立容器 + HTTP，LO 常驻 | **高**；隔离好，但 Gotenberg 不做 PNG、也不支持只导第 N 页 |
| **常驻 LO 服务** | **unoserver** | 启动一个 LO listener，后续请求复用；官方称 CPU 降 50–75%、吞吐 2–4× | 高；适合“首次慢、之后快”，但多一层 Python/UNO 依赖 |
| **商业 SDK** | Aspose.Slides、Spire.Presentation | 自研渲染 | 保真高，但**有水印/收费**，任务明确禁止 |
| **纯渲染器** | `pptx-raster`（自测 SSIM 0.86 vs PowerPoint；Aspose 0.878）、Pagus（SVG/DOM） | 解析 OOXML 自己画 | 中；连商业库都不是像素级，复杂版式仍有差距 |
| **云 API** | CloudConvert / ConvertAPI | 上传转换 | 低；费用、隐私、离线、社区版依赖外部服务 |
| **Office COM / Graph** | Windows PowerPoint、SharePoint | 调 Office | 不适用；容器是 Linux，且本地文件不在 SharePoint |

参考：
- Gotenberg 安装/架构：<https://gotenberg.dev/docs/getting-started/installation>
- Gotenberg LibreOffice 路线：<https://gotenberg.dev/docs/libreoffice/convert>
- unoserver：<https://github.com/unoconv/unoserver>
- pptx-thumbnailer：<https://www.npmjs.com/package/pptx-thumbnailer>
- pptx-raster 的 SSIM 对比：<https://pypi.org/project/pptx-raster/1.0.1/>
- Pagus（纯 SVG/DOM 渲染）：<https://github.com/pagus-kit/Pagus>

**结论**：引擎层面没有第二个同时满足「真渲染 + 离线 + 无许可费 + arm64」的选项；真正的设计空间在**打包位置和进程模型**。

---

## 5. 方案对比

评分：✅ 好 / ⚠️ 可接受 / ❌ 差。

| 方案 | 真渲染 | 单页延迟 | 内存隔离 | 镜像/磁盘 | 可复现（社区） | 改动面 | 复用其他 agent |
|---|---|---|---|---|---|---|---|
| **A. 烘进 Hermes 镜像** | ✅ | ✅ 9–11 s | ❌ 与 gateway 共享 4 GB | ⚠️ +约 300–400 MB | ✅ 一次 build 全有 | ⚠️ 重建 4 个 Hermes 容器 | ❌ 仅 Hermes |
| **B. 独立 `office-render` 侧车**（推荐） | ✅ | ✅ 9–11 s（可再加常驻进程） | ✅ cgroup 限内存、可重启 | ✅ Hermes 镜像不变；侧车约 400 MB | ✅ 可选 profile，一条 compose 命令 | ✅ 新增独立服务，不动 Hermes | ✅ Claude Code / OpenClaw 也能调 |
| **C. Gotenberg 现成侧车** | ✅ | ⚠️ 只出 PDF、无页码 → 全量转换，165 MB 会 OOM | ✅ | ✅ 现成镜像 | ✅ | ✅ | ✅ |
| **D. `/opt/data` 解 deb 前缀** | ❌ 实测 `DeploymentException` | — | — | ⚠️ 438 MB 重复 | ❌ 手工、不可复现 | ✅ 不改镜像 | ❌ |
| **E. 云转换 API** | ✅ | ⚠️ 上传 165 MB | — | ✅ | ⚠️ 依赖外部 | ✅ | ⚠️ |
| **F. HTML 重绘 + 浏览器截图** | ❌ 近似 | ✅ | — | ✅ | ✅ | ⚠️ 工作量大 | ✅ |

**推荐 B，理由：**

1. Hermes 无 root，运行期装不了；要么镜像预置，要么独立服务。独立服务不把 300 MB+ 和渲染内存压进 agent 容器。
2. 实测全量导出会打爆内存。侧车可以单独 `mem_limit: 3g`、失败只重启渲染容器，不影响 4 个 Hermes profile 的飞书连接。
3. 社区版：一个可选 compose profile，多架构（Debian apt 同时覆盖 amd64/arm64），其他 agent 也能复用；Hermes 镜像保持通用。
4. 升级/排障独立：LO 版本、字体、PyMuPDF 都在侧车里换，不用重建 agent。
5. CLI 契约不变：`pptpage` 只是把后端从“本地 soffice”换成“HTTP 调侧车”；将来要退回镜像层，skill 不用改。

**A 什么时候更合适**：只服务这一台机器、追求“零新增服务”、且能接受重建 4 个 Hermes 容器。此时把同一 CLI 烘进镜像即可，契约完全一样。

**C 为什么不做主方案**：Gotenberg 的 LO 路线只出 PDF，没有 `PageRange`，165 MB / 64 页会整本转换——正是我们实测 OOM 的路径；而且 165 MB 走 multipart 上传不如共享卷传路径。它更适合通用 doc→PDF，不适合“单页预览”。

**D 为什么不做**：已经实测失败；便携前缀对 LO 不是受支持布局。

---

## 6. 推荐架构（B）

```
飞书  ──  Hermes gateway (uid 10000)
              │  skill: pptx-slide-preview
              │  $ pptpage <pptx> <page> --dpi 150
              ▼
        /opt/data/bin/pptpage        （Python stdlib，无第三方依赖）
              │  POST http://office-render:8000/render
              ▼
        office-render 侧车（隔离容器，mem_limit 3g，单并发）
          ├─ LibreOffice 25.2（PageRange 单页 PDF）
          ├─ PyMuPDF（PDF 第 1 页 → PNG，dpi=150）
          └─ 写 /opt/data/tmp/pptpage/<sha12>-p<N>-<dpi>.png
              │  返回 {"path": "...", "width": 2001, "height": 1125, "ms": 10500}
              ▼
        pptpage 打印绝对路径
              ▼
        Hermes 回复：MEDIA:/opt/data/tmp/pptpage/....png
              ▼
        飞书 im/v1/images → image_key → 图片消息
```

### 6.1 组件

```
services/office-render/
  Dockerfile              # debian:13-slim + libreoffice-impress + fonts-noto-cjk
  app.py                  # FastAPI/Flask，/render、/health，单并发锁
  requirements.txt        # pymupdf（--no-deps 安装）
  fonts/99-cjk-alias.conf # 微软雅黑/等线/宋体 → Noto
skills/pptx-slide-preview/
  SKILL.md
  scripts/pptpage         # 调侧车的 CLI，stdlib only
docker-compose.office.yml # 可选 profile "office"，挂 ~/.hermes:/opt/data
```

### 6.2 CLI 契约（满足验收）

```bash
pptpage <pptx> <page> [-o out.png] [--dpi 150]
```

- 默认输出目录 `/opt/data/tmp/pptpage/`；stdout 只打印**绝对路径**，方便 Hermes 直接拼 `MEDIA:`。
- 页码 1-based；越界返回明确错误（并给出总页数）。
- 退出码：0 成功 / 2 参数错 / 3 渲染失败 / 4 页码越界 / 5 侧车不可达。

### 6.3 关键设计

- **只导第 N 页**：`PageRange` 是避免 OOM 的关键；禁止“先整本转 PDF 再取页”。
- **缓存**：`sha256(pptx) + mtime + size + page + dpi` 决定输出名；同一文件同一页重复请求直接命中。编辑后 hash 变化，自动失效。
- **单并发**：侧车全局锁（`asyncio.Lock` 或 `flock`），避免两个 profile 同时渲染把宿主打爆；请求排队 + 120 s 超时。
- **限内存**：侧车 `mem_limit: 3g`、`pids_limit`、`read_only` + `tmpfs:/tmp`；LO 被杀只影响侧车。
- **字体**：镜像装 `fonts-noto-cjk`（OFL，可分发），加 fontconfig 别名；不把微软雅黑打进镜像。需要像素级对齐的用户可挂载自己的授权字体目录。
- **路径**：侧车与 Hermes 用同一个 `/opt/data` 挂载（或至少 `/opt/data/tmp`），只传路径不传 165 MB；侧车只允许读配置的根目录、只写输出目录。
- **权限**：输出目录预创建并 chown 10000（Hermes）；侧车以 10000 运行；生成文件 0644。
- **常驻/热启动**：P1 先每次起 soffice（已证明 9–11 s < 20 s）；P2 可换 `unoserver` 或自定义 UNO worker，把“同一文件切页”降到 1–2 s。
- **安全**：侧车不 publish 宿主端口，只在内网；`cap_drop: ALL`、`no-new-privileges`；不访问外网；pptx 是不可信输入，LO 解析放隔离容器。

### 6.4 会话体验（比“能渲染”更容易被忽略）

用户说的是「把第 6 页发我」，不会每次重复完整路径。skill 要把这层意图接住：

- **当前 deck 解析**：优先用用户本轮明确给的路径；否则读 `~/.hermes/pptpage.json` 里记录的“最近使用的 deck”；再否则在 `/opt/data/tmp/xdsh/` 下找最近修改的 `.pptx`。多个候选时不要猜，列出让用户选。
- **等文件写完**：Hermes 刚用 python-pptx 改完文件时，等 mtime 稳定（例如 0.5 s 内不再变化）再渲染，避免截到上一版。
- **页码语义**：1-based；错误信息带总页数（「第 70 页不存在，共 64 页」）。
- **回执**：图片配一句「第 6 页 / 共 64 页（150 dpi）」；不要只发图不说话。
- **不要二次压缩**：直接把 LO 渲染的 PNG 交给飞书；不要转 JPEG 或缩小，避免用户误判清晰度。
- **动画/视频**：截图是静态首帧，和 PowerPoint 编辑视图一致；不要承诺播放态。
- **产物清理**：默认保留 7 天或最近 200 张，避免 `/opt/data/tmp` 越堆越多。

### 6.5 分阶段

**P0（本周，验证价值）**
1. 起 `office-render` 侧车（自定义镜像，Debian + LO + Noto + PyMuPDF）。
2. 写 `pptpage` CLI（stdlib）+ `pptx-slide-preview` skill，装到 `~/.hermes/skills/`。
3. 用样例第 5、6 页 + 一页带表格/图片（第 8 页）验收；确认飞书收到的是图片消息。
4. 记录 p95 延迟、峰值内存、失败模式。

**P1（社区化）**
1. 侧车进 `docker-compose` 的可选 profile；`start.sh` 增加可选开关。
2. 加 fixture（小 pptx + 大 deck smoke）、CI 构建、镜像多架构。
3. 缓存、页码越界、超时、并发、错误码测试。
4. 文档 + skill 里的故障排查。

**P2（体验）**
1. `unoserver` / 常驻 UNO worker，切页 1–2 s。
2. 同一侧车暴露 MCP tool（`render_slide`），Claude Code / OpenClaw 直接调。
3. 可选授权字体挂载 + 与 PowerPoint 导出的基准图做 SSIM 回归。

---

## 7. 社区版要考虑的

- **可选而非默认**：不是每个用户都要 PPT 渲染；用 compose profile，避免给所有部署加 400 MB 和攻击面。
- **多架构**：Debian apt 的 LO 同时有 amd64/arm64；CI 两个架构都 build 一次。
- **许可**：LibreOffice MPL 2.0、Noto CJK OFL，可随镜像分发；微软雅黑不可分发，只能“用户自备挂载”。Aspose/Spire 明确不用。
- **可观测**：`/health` 返回 soffice 版本；日志带 `sha/page/dpi/ms/peak`；失败返回结构化 JSON。
- **可测试**：仓库加一个 2–3 页的 fixture，CI 里 build 侧车、渲染、断言尺寸/非空/延迟；大 deck 只做手动 smoke。
- **一致性**：CLI 契约和 skill 与后端解耦；将来侧车换 Gotenberg/云 API，skill 不变。

---

## 8. 风险与待拍板

| 风险 | 说明 | 缓解 |
|---|---|---|
| 宿主内存 | 8 GB VM，当前只剩约 1.2 GB；全量导出已 OOM | 强制 PageRange；侧车 3 GB 限内存 + 单并发；建议把 Docker Desktop 内存调到 12 GB 留余量 |
| 字体差异 | 微软雅黑缺失，换行/宽度和 PowerPoint 有差异 | Noto 别名；需要像素级时挂载授权字体；用宿主 PowerPoint 导出做基准图对比 |
| 延迟 | 每次 9–11 s；编辑后缓存失效 | 已满足 < 20 s；P2 常驻进程/页窗口缓存 |
| 165 MB + 视频 | 解析慢、内存高 | 单页导出；120 s 超时；失败重试一次；不让它跑在 gateway 容器里 |
| 权限/路径 | 侧车写、Hermes 读；virtiofs 权限 | 预创建输出目录并 chown 10000；侧车以 10000 运行；0644 |
| 安全 | 不可信 pptx 进 LO | 独立容器、非 root、只读挂载、无外网、cap drop |

**需要你拍板：**

1. **打包**：侧车（推荐，社区友好）还是镜像层（单机最简，但重建 4 个 Hermes 容器）？
2. **字体**：只带 Noto（可分发），还是允许挂载本机微软雅黑（更接近 PowerPoint，但不进社区镜像）？
3. **速度**：先接受 9–11 s/页，还是一开始就上常驻 LO（多约 200 MB 内存，切页 1–2 s）？
4. **社区默认**：侧车默认启用，还是 optional profile（推荐）？

### 8.1 怎么验证“和 PowerPoint 一致”（可选）

这台 Mac 上装了 Microsoft PowerPoint，可作为**一次性基准图生成器**，但不要作为运行方案（GUI、许可证、非 Linux、社区不可复制）：

- 在 PowerPoint 里把第 5/6/8 页导出为 PNG，和 LO 渲染图做 SSIM / 像素差；
- 若差异主要来自字体，再决定是否挂载微软雅黑；
- 把基准图放进仓库测试夹具，后续升级 LO / 改字体时回归。

这一步能把“看起来差不多”变成可量化的保真度。

---

## 9. 附：实测产物

容器内路径（宿主对应 `~/.hermes/tmp/pptimg-research/`）：

- 第 5 页：`/opt/data/tmp/pptimg-research/page5.png`（2001×1125，728 KB）
- 第 6 页：`/opt/data/tmp/pptimg-research/page6.png`（2001×1125，681 KB）
- 第 8 页（表格）：`/opt/data/tmp/pptimg-research/page8.png`（2001×1125，524 KB）

这三张是 LibreOffice 真实渲染，不是坐标重绘。

---

## 10. 最终落地（aisecretary v0.2.3）

调研结论在实现阶段被实测修正了一次：**不必预先按页切分整本 deck，而是按「这次请求几页」动态切**。

### 10.1 关键数据（真实 157.9MB / 64 页 deck）

| 输入 | 峰值内存 | 耗时 |
|---|---|---|
| 整本 deck + PageRange 导单页（v0.2.2 之前） | 1.3–2.0 GB | 9–11 s |
| 只含目标页的切片 | **236 MB** | 0.6–2.0 s |
| 4 页连续批量（一个切片、一次 soffice） | ~330 MB | ~2.8–4.1 s |
| 8 页连续批量 | ~318 MB（LO） | ~5 s |

按页数继续放大：16 页 353MB / 32 页 528MB / 64 页回到 1.3–2GB。所以 `PPT_BATCH_MAX=8`：
再大时间收益消失、内存继续涨。

### 10.2 为什么「单页最好」不完全对

- 单页请求 → 切 1 页最优（无额外加载）；
- K 页**同一次**请求 → 切 K 页最优（省掉 K−1 次 soffice 启动，约 0.45s/次）；
- 预先切 64 个文件的唯一后果是多占约 175MB 磁盘和 4.5s 构建，而没有人一次看 64 页 → 不做。

### 10.3 两个坑

1. **`slidenum` 页码域**：切片后页码会显示「1」。必须替换成字面页码（第 9 页仍显示 9）。
2. **切片完整性**：只带目标页 + 媒体还不够，layout/master/theme 缺一个 LO 就报
   `source file could not be loaded`；同时要清掉指向已删除 part 的悬空 relationship。

### 10.4 保真度验证

第 5/6/8/13 页的切片渲染 PNG 与整本 deck 渲染的 PNG **md5 逐字节相同**。
（早期误判过「差 2–14% 像素」，其实是拿 MuPDF 渲染结果和 poppler 比 —— 同一渲染器下完全一致。）

### 10.5 部署形态

最终没有新增 `office-render` 侧车，渲染逻辑并入 aisecretary（`server/app/ppt/slice.py` +
`render.py`），Hermes 只连 aisecretary 一个 MCP 服务；容器内存上限从 3G 降到 1G 即可
（实测峰值 504MB）。
