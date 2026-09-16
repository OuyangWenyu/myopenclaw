// =============================================================
// paper-queue-actor — 把「谁提的」从宿主注入论文清单，模型碰不到。
//
// 为什么需要它：`before_tool_call` 的 ctx 里**没有任何身份字段**（实测全集只有
// abortSignal/agentId/channelId/runId/sessionId/sessionKey/toolCallId/toolName/trace），
// 而文档里写的 `ctx.requester` 在这个版本不填充。身份只出现在 `message_received` 上。
//
// 两步走（均已实测）：
//   1. message_received —— 采集 senderId/senderName/messageId（唯一带身份的钩子）
//   2. before_tool_call —— 按 runId/sessionKey 取回该会话最近一次入站的发送者，注入 actor_*
//
// 三个必须记住的坑（踩过才知道，别"优化"掉）：
//   * MCP 工具名是命名空间化的 `<server>__<tool>`，只能按后缀匹配。
//   * **before_tool_call 是 fail-closed** —— 处理器抛异常会阻塞工具调用（等于烧掉
//     虾酱整个工具面）。所有路径都必须吞掉异常并返回 undefined（= 不表态 = 放行）。
//   * 取不到身份时**注入空 actor_id**，由 MCP server 侧拒绝写入。这里不抛异常、也不
//     用模型填的名字兜底 —— 归属错了比没有归属更糟。
//
// ⚠️ 历史教训：早先的版本用正则去提示词里抓 `message_id` 来"精确绑定"。那条路**走过
// 一段提示词文本是用户可写的**——群友只要在消息里贴一个别人的 message_id，就能把这
// 个回合的归属绑到别人头上。现在只用宿主给的结构化事实（senderId + 会话），不再扫描
// 任何自由文本。
// =============================================================
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { createHmac } from "node:crypto";

const TOOL_SUFFIXES = ["__paper_queue_add", "__paper_queue_list", "__paper_queue_cancel"];

// 网关是长期进程，这些表会一直长 —— 每个都设上限，超了丢最旧的（Map 保留插入序）。
const MAX_CACHE = 500;

type Sender = {
  senderId: string | null;
  senderName: string | null;
  messageId: string | null;
  conversationId: string | null;
};

const bySession = new Map<string, Sender>();
const byRun = new Map<string, Sender>();
const activeRunBySession = new Map<string, string>();
const ambiguousRuns = new Set<string>();
/** 本会话自上回绑定以来收到几条入站 —— 用来判断归属可信度（见 attribution_source）。 */
const inboundSinceBind = new Map<string, number>();

function remember<K, V>(map: Map<K, V>, key: K, value: V): void {
  map.set(key, value);
  while (map.size > MAX_CACHE) {
    const oldest = map.keys().next();
    if (oldest.done) break;
    map.delete(oldest.value);
  }
}

function log(level: "warn" | "error", message: string, detail?: unknown): void {
  try {
    const suffix = detail === undefined ? "" : ` :: ${String(detail)}`;
    // 走 stderr → docker compose logs。静默降级比报错更难查。
    console[level](`[paper-queue-actor] ${message}${suffix}`);
  } catch {
    /* logging must never break the tool call */
  }
}

function str(value: unknown): string | null {
  const s = value === undefined || value === null ? "" : String(value).trim();
  return s || null;
}

function isPaperQueueTool(toolName: string): boolean {
  return TOOL_SUFFIXES.some((suffix) => toolName.endsWith(suffix));
}

/** 签名覆盖的 item 字段。必须与服务端 mcp_server.py 的 ITEM_SIGNED_FIELDS 逐字一致。 */
const ITEM_SIGNED_FIELDS = ["title", "doi", "doi_source", "arxiv_id", "url", "note", "raw_input"];
const MAX_SIGNED_ITEMS = 50;
const CANCEL_SUFFIX = "__paper_queue_cancel";

/** 只在两边都是字符串时按原文签；非字符串一律记空串，保证跨语言一致。 */
function signedItemValue(item: any, field: string): string {
  const value = item?.[field];
  return typeof value === "string" ? value : "";
}

/** 本次请求内容的规范字符串 —— 签名必须覆盖它，否则签名只是一枚可重放的 bearer 值。 */
function signablePayload(toolName: string, params: any): string {
  if (toolName.endsWith(CANCEL_SUFFIX)) return String(params?.request_key ?? "");
  const items = params?.items;
  if (!Array.isArray(items)) return "";
  return items
    .slice(0, MAX_SIGNED_ITEMS)
    .map((item: any) =>
      ITEM_SIGNED_FIELDS.map((field) => `${field}=${signedItemValue(item, field)}`).join("|"))
    .join("\n");
}

/**
 * 归属签名。密钥存在配置里（模型读得到），但**算不出 HMAC** —— 所以这一层的价值不是
 * "保密"，而是把「插件没加载」这种失败从**静默伪造**变成**响亮的拒绝写入**。
 *
 * 签名同时覆盖身份与**请求内容**：注入的 actor_* 参数会被写进模型可见的会话记录，
 * 只签身份的话模型可以把见过的值原样重放、配上任意 items。
 *
 * 签名材料必须与服务端 mcp_server.py 的 actor_signature_error 逐字一致。
 */
function sign(
  secret: string,
  actorId: string,
  sessionRef: string,
  messageRef: string,
  payload: string,
): string {
  if (!secret) return "";
  return createHmac("sha256", secret)
    .update([actorId, sessionRef, messageRef, payload].join("|"))
    .digest("hex");
}

export default definePluginEntry({
  id: "paper-queue-actor",
  name: "Paper Queue Actor",
  description: "Inject the host-derived requester into paper_queue_* tool calls.",

  register(api) {
    const secret = String((api as any)?.pluginConfig?.secret ?? "");

    // ── 1. 采集：唯一带身份的钩子 ──────────────────────────────
    api.on("message_received", (event: any, ctx: any) => {
      try {
        const e = event ?? {};
        const c = ctx ?? {};
        const meta = e.metadata ?? {};
        const sessionKey = str(c.sessionKey ?? e.sessionKey);
        const sender: Sender = {
          senderId: str(c.senderId ?? e.senderId ?? meta.senderId),
          senderName: str(meta.senderName),
          messageId: str(c.messageId ?? e.messageId ?? meta.messageId),
          conversationId: str(c.conversationId ?? meta.to),
        };
        if (!sessionKey) return;

        remember(bySession, sessionKey, sender);
        remember(inboundSinceBind, sessionKey, (inboundSinceBind.get(sessionKey) ?? 0) + 1);

        // 归属歧义：本会话已有回合在跑，而这条是**另一个人**发的 ⇒ 那个回合的绑定
        // 已经不可信（后到的消息会继承先到者的归属）。打标，让落库能看出这一点。
        const runningRunId = activeRunBySession.get(sessionKey);
        if (runningRunId) {
          const bound = byRun.get(runningRunId);
          if (bound?.senderId && sender.senderId && bound.senderId !== sender.senderId) {
            ambiguousRuns.add(runningRunId);
            log("warn", `同一回合内出现第二个发言人，已标记归属歧义 runId=${runningRunId}`);
          }
        }
      } catch (err) {
        log("error", "message_received 处理失败", err);
      }
    });

    // ── 2. 绑定：把本会话的发送者绑到该回合 ─────────────────────
    // 入站消息先到、回合后开，所以这里读到的就是触发本回合的那条（或那批里最新的）。
    // 「自上回绑定以来收到几条」决定 attribution_source：一条 = 能确定是它触发的；
    // 多条 = 这个回合可能吞了多条消息，归属取的是最新的那条。
    api.on("before_agent_run", (_event: any, ctx: any) => {
      try {
        const c = ctx ?? {};
        const runId = str(c.runId);
        const sessionKey = str(c.sessionKey);
        if (!runId || !sessionKey) return;

        const sender = bySession.get(sessionKey) ?? null;
        const seen = inboundSinceBind.get(sessionKey) ?? 0;
        inboundSinceBind.set(sessionKey, 0);

        if (!sender) {
          log("warn", `本回合未找到入站发送者，paper_queue_* 将因缺少身份被拒绝 runId=${runId}`);
          return;
        }
        remember(byRun, runId, sender);
        remember(activeRunBySession, sessionKey, runId);

        // 这个回合能看到多条消息 ⇒ 若其中有人与绑定的不是同一个，就已经分不清了
        if (seen > 1) {
          ambiguousRuns.add(runId);
          log("warn", `本回合前收到 ${seen} 条入站消息，归属按最新一条记并标记歧义 runId=${runId}`);
        }
        (sender as Sender & { single?: boolean }).single = seen <= 1;
      } catch (err) {
        log("error", "before_agent_run 处理失败", err);
      }
    });

    // ── 3. 收尾：清理本回合状态，避免长期进程里无限增长 ────────
    api.on("agent_end", (_event: any, ctx: any) => {
      try {
        const runId = str(ctx?.runId);
        const sessionKey = str(ctx?.sessionKey);
        if (runId) {
          byRun.delete(runId);
          ambiguousRuns.delete(runId);
        }
        if (sessionKey && activeRunBySession.get(sessionKey) === runId) {
          activeRunBySession.delete(sessionKey);
        }
      } catch (err) {
        log("error", "agent_end 处理失败", err);
      }
    });

    // ── 4. 注入：工具调用真正落地前改写参数 ────────────────────
    // ⚠️ fail-closed 钩子：抛异常 = 阻塞工具调用。这里永远不要让异常逃出去。
    api.on("before_tool_call", (event: any, ctx: any) => {
      try {
        const toolName = String(event?.toolName ?? "");
        if (!isPaperQueueTool(toolName)) return undefined;

        const c = ctx ?? {};
        const runId = str(c.runId);
        const sessionKey = str(c.sessionKey ?? "");
        const bound = runId ? byRun.get(runId) ?? null : null;
        const fallback = sessionKey ? bySession.get(sessionKey) ?? null : null;
        const sender = bound ?? fallback;

        // 如实标注绑定来源，而不是"哪个表答的"——早先的版本就是后者，结果这个字段
        // 在生产里恒为 message_id，把一个本该暴露低可信度的信号变成了摆设。
        let source = "";
        if (bound) {
          source = (bound as Sender & { single?: boolean }).single === false
            ? "batched"
            : "single";
        } else if (fallback) {
          source = "session_latest";
        }

        const actorId = sender?.senderId ?? "";
        const messageRef = sender?.messageId ?? "";

        return {
          params: {
            ...(event?.params ?? {}),
            // 取不到就注入空串 —— MCP server 会据此拒绝写入（fail closed），
            // 绝不回退到模型填的 requester。
            actor_id: actorId,
            actor_name: sender?.senderName ?? "",
            actor_message: messageRef,
            channel_ref: sender?.conversationId ?? "",
            session_ref: sessionKey,
            attribution_source: source,
            actor_ambiguous: runId ? ambiguousRuns.has(runId) : false,
            actor_sig: sign(secret, actorId, sessionKey, messageRef,
                            signablePayload(toolName, event?.params)),
          },
        };
      } catch (err) {
        log("error", "before_tool_call 处理失败（已放行该次调用）", err);
        return undefined;
      }
    });
  },
});
