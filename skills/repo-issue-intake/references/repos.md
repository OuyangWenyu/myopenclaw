# 自有仓库白名单与定位

**只有这张表里的 39 个仓库可以建 issue。** 来源：`~/code/git-contribution-stats/configs/repos.toml`
（每日采集清单），由 `scripts/refresh_allowlist.py` 同步到 `scripts/repos.json`。
新增仓库：先在 `repos.toml` 加一行 → 跑刷新脚本 → 在这张表补一行（测试会检查两边一致）。

**镜像规则**：`myopenclaw`、`hydroutils`、`torchhydro` 在 GitHub 与 GitCode 各有一份
（同名、不同 owner）。默认写 **GitHub**（上游）；只写裸仓库名时脚本自动选 GitHub。
要写 GitCode 副本，写全 `dlut-water/...` 并说明理由，脚本会打印 `MIRROR=` 提醒。

> 不在白名单但 repo-scanner 也见过的：`dlut-water/aisecretary`、`dlut-water/HydroForecastEval`
> （只在 watcher 的库里，不在每日采集清单）。需要可写时：先加进
> `git-contribution-stats/configs/repos.toml` → 跑刷新脚本 → 在这张表补一行。

## GitHub · OuyangWenyu

| 仓库 | 定位 | 适合接的 issue |
|---|---|---|
| `OuyangWenyu/aqualord` | 水文元数据（站点/流域数据的元信息） | 元数据字段、数据来源、格式标准 |
| `OuyangWenyu/hydrodataset` | 水文建模数据集的下载与读取 | 新数据源、读取器、下载/缓存健壮性 |
| `OuyangWenyu/hydromodel` | 新安江等概念性水文模型 | 模型算法、参数率定、新模型接入 |
| `OuyangWenyu/hydroutils` | 水文建模常用工具函数 | 通用工具、坐标/时间处理、性能 |
| `OuyangWenyu/myopenclaw` | 个人 agent 操作系统（本仓） | agent 编排、MCP、飞书/钉钉接入、部署运维 |
| `OuyangWenyu/torchhydro` | 深度学习水文建模（数据集 + 模型） | 训练/数据管线、新模型、GPU 性能 |

## GitHub · iHeadWater

| 仓库 | 定位 | 适合接的 issue |
|---|---|---|
| `iHeadWater/aisecretary` | AI 秘书：事务记账 + Hermes skill 自动配置 | 记账、图表、日程、技能接入 |
| `iHeadWater/dailyinfo` | 每日科技情报的采集与调度 | RSS 源、摘要质量、调度/推送 |
| `iHeadWater/dailyinfo-web` | 每日科技情报的 Web 展示 | 前端、检索、展示 |
| `iHeadWater/digital_twin_basin` | 数字孪生流域课程 | 课程材料、可视化 demo |
| `iHeadWater/HydroAgent` | Agentic Loop + Tool Use 智能体框架 | 工具编排、agent 框架、评测 |
| `iHeadWater/HydroDataCompiler` | 深度学习水文建模数据集编译 notebooks | 数据编译、预处理 |
| `iHeadWater/hydrodatasource` | 多源水文数据获取与处理 | 新数据源、清洗、格式转换 |
| `iHeadWater/hydrotopo` | 站点 + 河网 shapefile 推上下游拓扑 | 拓扑算法、GIS、性能 |
| `iHeadWater/mylibrary` | 个人知识库 / 文献管线（Zotero、paper pipeline） | 文献抓取、PDF 解析、Zotero、语料 |

## GitCode · dlut-water

| 仓库 | 定位 | 适合接的 issue |
|---|---|---|
| `dlut-water/RealTimeForecast` | 实时径流预测 | 实时预报、数据接入、调度 |
| `dlut-water/ServersCI` | 团队服务持续集成管理 | CI/CD、部署脚本、镜像 |
| `dlut-water/flood_data_augmentation_frontend` | 洪水数据增强前端 | 前端 UI、交互 |
| `dlut-water/flooddataaugmentation` | 数据增强后台系统 | 增强算法、后台任务 |
| `dlut-water/git-contribution-stats` | 代码贡献度 / 研发报表 MCP | 采集、报表、MCP 工具 |
| `dlut-water/hydroutils` | 水文工具函数（GitHub 镜像） | 同 GitHub，优先 GitHub |
| `dlut-water/hydroweacast` | 气象模型部署 | 气象数据、模型部署 |
| `dlut-water/mghydro` | 企业级流域划分与水文分析平台 | 流域划分、GIS 分析、部署 |
| `dlut-water/minio-backup` | MinIO 热备 / 冷备归档 | 存储、归档、成本 |
| `dlut-water/model_deployment` | 模型部署到甲方服务器 | 离线部署、环境适配 |
| `dlut-water/myopenclaw` | agent 平台（GitHub 镜像） | 同 GitHub，优先 GitHub |
| `dlut-water/pdf_to_markdown` | 大批量 PDF 转 Markdown | 文档解析、OCR、批处理 |
| `dlut-water/rainfall-oa-data` | A 类数据下载与预处理 | 数据抓取、预处理 |
| `dlut-water/server-monitoring` | Prometheus 服务器监控 | 监控指标、告警规则 |
| `dlut-water/server-ops` | 服务器运维资料 | 运维手册、脚本 |
| `dlut-water/songliao-datax` | 松辽委 DataX 数据同步 | 数据同步、ETL |
| `dlut-water/songliaodb_analysis` | 松辽委数据库分析 | SQL、数据分析 |
| `dlut-water/stac` | 通过 STAC API 下载遥感数据集 | 遥感、STAC、批处理 |
| `dlut-water/torchhydro` | 深度学习水文（GitHub 镜像） | 同 GitHub，优先 GitHub |
| `dlut-water/web_spider_ocr` | OCR 抓取水利部实时水情 | 爬虫、OCR、反爬 |
| `dlut-water/yuque_mcp_server` | 语雀文档备份 / MCP | 语雀 API、备份、MCP |
| `dlut-water/zhixun-agent` | 知汛 Agent（zhixun-core 的能力利用者） | 预报问答、工具调用 |
| `dlut-water/zhixun-core` | 知汛核心服务 | 预报核心、API、数据 |
| `dlut-water/zhixun-vue` | 知汛前端（洪水预报监测） | 前端、图表、地图 |

## 选仓顺序

1. 先按「补的是谁的缺口」定位（不是来源属于谁）。
2. 只命中一个 → 就用它。
3. 命中两个 → 选主责仓库，另一个写进正文「备选」；镜像对优先 GitHub。
4. 一个都不命中 → 不建 issue，回复候选仓库 + 理由，请用户定。