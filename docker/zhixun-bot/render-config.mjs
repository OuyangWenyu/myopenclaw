import fs from "node:fs";

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

const replacements = new Map([
  ["__FEISHU_APP_ID__", process.env.ZHIXUN_BOT_FEISHU_APP_ID],
  ["__FEISHU_APP_SECRET__", process.env.ZHIXUN_BOT_FEISHU_APP_SECRET],
  ["__MODEL_API_KEY__", process.env.ZHIXUN_BOT_MODEL_API_KEY],
  ["__MODEL_ID__", process.env.ZHIXUN_BOT_MODEL_ID],
  ["__MODEL_BASE_URL__", process.env.ZHIXUN_BOT_MODEL_BASE_URL],
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
const enableWriteTools =
  (process.env.ZHIXUN_BOT_ENABLE_WRITE_TOOLS ?? "false").toLowerCase() === "true";
const feishuStreaming =
  (process.env.ZHIXUN_BOT_FEISHU_STREAMING ?? "false").toLowerCase() === "true";

config.channels.feishu.streaming = feishuStreaming;

if (enableWriteTools) {
  delete config.mcp.servers.water_unified.toolFilter;
}

const temporaryPath = `${outputPath}.tmp`;
fs.writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, {
  mode: 0o600,
});
fs.renameSync(temporaryPath, outputPath);
