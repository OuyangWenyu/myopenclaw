import fs from "node:fs";

const [templatePath, outputPath] = process.argv.slice(2);

if (!templatePath || !outputPath) {
  throw new Error("usage: render-config.mjs <template> <output>");
}

const required = [
  "TIANYI_BOT_FEISHU_APP_ID",
  "TIANYI_BOT_FEISHU_APP_SECRET",
  "TIANYI_BOT_MODEL_API_KEY",
  "TIANYI_BOT_MODEL_ID",
  "TIANYI_BOT_MODEL_BASE_URL",
];

for (const name of required) {
  if (!process.env[name]) {
    throw new Error(`missing required environment variable: ${name}`);
  }
}

const replacements = new Map([
  ["__FEISHU_APP_ID__", process.env.TIANYI_BOT_FEISHU_APP_ID],
  ["__FEISHU_APP_SECRET__", process.env.TIANYI_BOT_FEISHU_APP_SECRET],
  ["__MODEL_API_KEY__", process.env.TIANYI_BOT_MODEL_API_KEY],
  ["__MODEL_ID__", process.env.TIANYI_BOT_MODEL_ID],
  ["__MODEL_BASE_URL__", process.env.TIANYI_BOT_MODEL_BASE_URL],
  ["__YUQUE_MCP_URL__", process.env.TIANYI_BOT_YUQUE_MCP_URL ?? ""],
  // GLM 兜底模型 key（可选，2026-10-15）：替换占位符；无 key 时见下方摘除逻辑
  ["__GLM_API_KEY__", (process.env.TIANYI_BOT_GLM_API_KEY ?? "").trim()],
  // repo-scanner 是必需能力（不可像 yuque / server-monitoring 那样删条目）：
  // 未设/空串/纯空白 = 回落默认（主栈同网逻辑地址，与参数化前行为一致）；
  // 赤兔迁移 / 异地消费时经 env 覆盖。trim 与相邻 yuque/monitoring 的约定一致——
  // 纯空白值若原样渲染会让 config validate 失败 → entrypoint 中止 → crash-loop。
  ["__REPO_SCANNER_MCP_URL__", (process.env.TIANYI_BOT_REPO_SCANNER_MCP_URL ?? "").trim() || "http://repo-scanner-mcp:8001/mcp"],
  ["__SERVER_MONITORING_MCP_URL__", process.env.TIANYI_BOT_SERVER_MONITORING_MCP_URL ?? ""],
]);

function replace(value) {
  if (typeof value === "string") {
    let rendered = value;
    for (const [placeholder, replacement] of replacements) {
      rendered = rendered.replaceAll(placeholder, replacement);
    }
    return rendered;
  }
  if (Array.isArray(value)) {
    return value.map(replace);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, child]) => [
        replacements.get(key) ?? key,
        replace(child),
      ]),
    );
  }
  return value;
}

const template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
const config = replace(template);

// Apply streaming toggle
const feishuStreaming =
  (process.env.TIANYI_BOT_FEISHU_STREAMING ?? "false").toLowerCase() === "true";
// 2.0 起 `streaming` 是**对象**而非布尔。布尔是已被淘汰的旧写法（镜像内
// docs/channels/feishu.md：Legacy boolean `streaming` … migrate to this nested
// shape via `openclaw doctor --fix`），写布尔会让**每次启动渲染出的配置都
// schema-invalid**。取值：partial=开启流式卡片（默认），off=只发完成后的整条回复。
config.channels.feishu.streaming = { mode: feishuStreaming ? "partial" : "off" };

// Drop yuque-mcp entry when no URL is configured (optional capability)
const yuqueUrl = process.env.TIANYI_BOT_YUQUE_MCP_URL?.trim();
if (!yuqueUrl) {
  delete config.mcp.servers["yuque-mcp"];
} else if (!process.env.TIANYI_BOT_MCP_YUQUE_MCP_API_KEY) {
  throw new Error(
    "TIANYI_BOT_YUQUE_MCP_URL is set but TIANYI_BOT_MCP_YUQUE_MCP_API_KEY is missing",
  );
}

// GLM 兜底模型（可选，2026-10-15）：无 key 时摘除 zhipu provider 与 fallbacks 条目。
// 与虾酱的 fallbacks 机制同款（agents.defaults.model.fallbacks）——主模型不可用时
// OpenClaw 自动切换；不配兜底 ≠ 起不来（其余能力不受影响）。
const glmKey = (process.env.TIANYI_BOT_GLM_API_KEY ?? "").trim();
if (!glmKey) {
  delete config.models.providers.zhipu;
  const model = config.agents?.defaults?.model;
  if (model && Array.isArray(model.fallbacks)) {
    model.fallbacks = model.fallbacks.filter((m) => m !== "zhipu/glm-5.3-flash");
    if (model.fallbacks.length === 0) {
      delete model.fallbacks;
    }
  }
}

// Drop server-monitoring entry when no URL is configured (optional capability)
const serverMonitoringUrl = process.env.TIANYI_BOT_SERVER_MONITORING_MCP_URL?.trim();
if (!serverMonitoringUrl) {
  delete config.mcp.servers["server-monitoring"];
} else if (!process.env.TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN) {
  throw new Error(
    "TIANYI_BOT_SERVER_MONITORING_MCP_URL is set but TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN is missing",
  );
}

// ⚠️ 不要给 channels.feishu 加 execApprovals —— 2026.9.1 的飞书 schema 拒绝该键
// （真校验器实测 "must not have additional properties"；飞书原生审批卡是上游未发布
// 功能，canonical 跟踪 openclaw/openclaw#104521）。审批体验走 `tools.exec.mode:
// "auto"`（白名单直通 + 审查员兜底，见模板）；**不要**再加 approvals.exec 转发——
// 它会制造审批路由、绕过 #108935 的 askFallback 内联快速路径，导致队列级卡死。
// 守卫：tests/test_server_monitoring_mcp.py::TestExecApprovalsApprover。

const temporaryPath = `${outputPath}.tmp`;
fs.writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, {
  mode: 0o600,
});
fs.renameSync(temporaryPath, outputPath);
