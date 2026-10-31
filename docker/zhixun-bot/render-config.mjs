import fs from "node:fs";
import { loadWaterMcpToken } from "./water-mcp-token.mjs";

const [templatePath, outputPath] = process.argv.slice(2);

if (!templatePath || !outputPath) {
  throw new Error("usage: render-config.mjs <template> <output>");
}

const required = [
  "ZHIXUN_BOT_FEISHU_APP_ID",
  "ZHIXUN_BOT_FEISHU_APP_SECRET",
  "ZHIXUN_BOT_MODEL_API_KEY",
  "ZHIXUN_BOT_MODEL_ID",
  "ZHIXUN_BOT_MODEL_BASE_URL",
];

for (const name of required) {
  if (!process.env[name]) {
    throw new Error(`missing required environment variable: ${name}`);
  }
}

// 默认连接远程 MCP；认证覆盖配置可显式选择已升级的本地 SSE 实例。
// URL 与凭据必填（env token 或只读文件）：TOKEN 由 OpenClaw 运行时展开到 headers.authorization，
// 渲染产物里只有字面量 `${ZHIXUN_BOT_WATER_MCP_TOKEN}`，不会落真实凭据。
const waterMcpUrl = (process.env.ZHIXUN_BOT_WATER_MCP_URL ?? "").trim();

if (!waterMcpUrl) {
  throw new Error("missing required environment variable: ZHIXUN_BOT_WATER_MCP_URL");
}
loadWaterMcpToken();

const replacements = new Map([
  ["__FEISHU_APP_ID__", process.env.ZHIXUN_BOT_FEISHU_APP_ID],
  ["__FEISHU_APP_SECRET__", process.env.ZHIXUN_BOT_FEISHU_APP_SECRET],
  ["__MODEL_API_KEY__", process.env.ZHIXUN_BOT_MODEL_API_KEY],
  ["__MODEL_ID__", process.env.ZHIXUN_BOT_MODEL_ID],
  ["__MODEL_BASE_URL__", process.env.ZHIXUN_BOT_MODEL_BASE_URL],
  // GLM 兜底模型 key（可选）：替换占位符；无 key 时见下方摘除逻辑
  ["__GLM_API_KEY__", (process.env.ZHIXUN_BOT_GLM_API_KEY ?? "").trim()],
  ["__WATER_MCP_URL__", waterMcpUrl],
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

// GLM 兜底模型（可选）：无 key 时摘除 zhipu provider 与 fallbacks 条目。
// 与天一的兜底机制同款（agents.defaults.model.fallbacks）——主模型不可用时
// OpenClaw 自动切换；不配兜底 ≠ 起不来（其余能力不受影响）。
const glmKey = (process.env.ZHIXUN_BOT_GLM_API_KEY ?? "").trim();
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

const enableWriteTools =
  (process.env.ZHIXUN_BOT_ENABLE_WRITE_TOOLS ?? "false").toLowerCase() === "true";
const feishuStreaming =
  (process.env.ZHIXUN_BOT_FEISHU_STREAMING ?? "false").toLowerCase() === "true";

// 2.0 起 `streaming` 是**对象**而非布尔。布尔是已被淘汰的旧写法（镜像内
// docs/channels/feishu.md：Legacy boolean `streaming` … migrate to this nested
// shape via `openclaw doctor --fix`），写布尔会让**每次启动渲染出的配置都
// schema-invalid**。取值：partial=开启流式卡片（默认），off=只发完成后的整条回复。
config.channels.feishu.streaming = { mode: feishuStreaming ? "partial" : "off" };

// 远程端点协议由部署方决定：sse（默认）或 streamable-http。
// 非法值直接抛错，避免渲染出一份 schema 不接受、或连不上的配置。
const waterTransport = (process.env.ZHIXUN_BOT_WATER_MCP_TRANSPORT ?? "sse")
  .trim()
  .toLowerCase();
if (!["sse", "streamable-http"].includes(waterTransport)) {
  throw new Error(
    `invalid ZHIXUN_BOT_WATER_MCP_TRANSPORT: ${waterTransport} (expected "sse" or "streamable-http")`,
  );
}
config.mcp.servers.water_unified.transport = waterTransport;

if (enableWriteTools) {
  delete config.mcp.servers.water_unified.toolFilter;
}

const temporaryPath = `${outputPath}.tmp`;
fs.writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, {
  mode: 0o600,
});
fs.renameSync(temporaryPath, outputPath);
