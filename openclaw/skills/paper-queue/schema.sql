-- =============================================================
-- paper-queue 契约 —— 论文清单的单一事实来源
--
-- 消费者: openclaw/skills/paper-queue/mcp_server.py（写入/查询）
--         scripts/check_paper_queue.py（只读校验）
--         ~/code/mylibrary（读取清单，M3）
-- 文档:   docs/paper-queue.md
--
-- 设计约束（改动前先读 docs/paper-queue.md）:
--   * 本表**只记录"谁要哪篇"**，不追踪是否被消费 —— 消费与否靠人判断。
--   * request_key 只从「用户实际说了什么」派生；虾酱推断的 DOI 不参与，
--     否则一个幻觉 DOI 会生成错误的唯一键并污染去重。
--   * requested_at / cancelled_at 必须是 ISO8601 UTC（以 Z 结尾），
--     因为时间范围查询按 TEXT 字典序比较 —— 非 ISO 值会静默查错。
-- =============================================================

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

-- schema 版本。SQLite 改不了 CHECK 约束，所以只能靠重建表迁移 —— 见
-- mcp_server.py 的 _migrate_if_stale()。**改动本文件的表结构时必须同时 +1**，
-- 否则老库不会被迁移，新枚举值会被旧 CHECK 拒绝（表现是"每次写都失败"）。
PRAGMA user_version = 2;

CREATE TABLE IF NOT EXISTS paper_requests (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  request_key           TEXT    NOT NULL,     -- 去重键，见 docs/paper-queue.md 的派生规则
  input_kind            TEXT    NOT NULL CHECK (input_kind IN ('title','doi','arxiv','url')),
  raw_input             TEXT    NOT NULL,     -- 用户原话片段，人工核对用

  title                 TEXT,                 -- 主要标识：来自 Discord 对话
  doi                   TEXT,                 -- 可选属性
  doi_source            TEXT    CHECK (doi_source IS NULL OR doi_source IN ('user','inferred')),
  arxiv_id              TEXT,
  url                   TEXT,
  note                  TEXT,                 -- 附加说明（如"要正文/补充材料"）

  requester             TEXT    NOT NULL,     -- 权威：宿主插件注入的 Discord senderId
  requester_name        TEXT,                 -- 非权威展示值
  channel_ref           TEXT,                 -- ctx.conversationId
  message_ref           TEXT,                 -- ctx.messageId，归属有争议时溯源
  session_ref           TEXT,
  -- 归属可信度：single=本回合前只收到一条入站（能确定是谁触发的）
  --             batched=本回合前收到多条，归属取最新那条（可能张冠李戴）
  --             session_latest=没绑上回合，退回会话最近一次入站
  attribution_source    TEXT    CHECK (attribution_source IS NULL
                                       OR attribution_source IN ('single','batched','session_latest')),
  attribution_ambiguous INTEGER NOT NULL DEFAULT 0 CHECK (attribution_ambiguous IN (0,1)),

  requested_at          TEXT    NOT NULL,
  cancelled_at          TEXT,
  cancelled_by          TEXT,

  -- 至少要有一种方式能认出这篇论文
  CHECK (title IS NOT NULL OR doi IS NOT NULL OR arxiv_id IS NOT NULL OR url IS NOT NULL),
  -- 撤销时间与撤销人必须成对出现
  CHECK ((cancelled_at IS NULL) = (cancelled_by IS NULL)),
  -- 时间戳必须严格是 YYYY-MM-DDTHH:MM:SSZ（定宽 20 字符）。
  -- 范围查询按 TEXT 字典序比较，所以格式必须唯一：像 '…24.123Z' 这种带毫秒的值
  -- 虽然"看起来是 ISO"，却会让 '.' < 'Z' 的比较静默漏掉记录 —— 因此这里
  -- 逐位限定字符类，而不是用宽松的 'T*Z'。
  CHECK (requested_at GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (cancelled_at IS NULL OR cancelled_at GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- 活跃期去重：未撤销时同一 request_key 只有一条；撤销后可重新请求
CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_requests_active_key
  ON paper_requests(request_key) WHERE cancelled_at IS NULL;

-- 时间范围查询（清单的主要读取方式）
CREATE INDEX IF NOT EXISTS ix_paper_requests_requested_at
  ON paper_requests(requested_at);

-- 按人查清单
CREATE INDEX IF NOT EXISTS ix_paper_requests_requester
  ON paper_requests(requester, requested_at);
