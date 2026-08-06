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
config.channels.feishu.streaming = feishuStreaming;

// Drop yuque-mcp entry when no URL is configured (optional capability)
const yuqueUrl = process.env.TIANYI_BOT_YUQUE_MCP_URL?.trim();
if (!yuqueUrl) {
  delete config.mcp.servers["yuque-mcp"];
} else if (!process.env.TIANYI_BOT_MCP_YUQUE_MCP_API_KEY) {
  throw new Error(
    "TIANYI_BOT_YUQUE_MCP_URL is set but TIANYI_BOT_MCP_YUQUE_MCP_API_KEY is missing",
  );
}

const temporaryPath = `${outputPath}.tmp`;
fs.writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, {
  mode: 0o600,
});
fs.renameSync(temporaryPath, outputPath);
