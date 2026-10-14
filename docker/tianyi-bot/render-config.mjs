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
// （真校验器实测 "must not have additional properties"；飞书原生审批卡是上游
// 未发布功能，PR #60328）。审批体验走顶层 approvals.exec 转发（见模板）。
// 守卫：tests/test_server_monitoring_mcp.py::TestExecApprovalsApprover。

const temporaryPath = `${outputPath}.tmp`;
fs.writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, {
  mode: 0o600,
});
fs.renameSync(temporaryPath, outputPath);
