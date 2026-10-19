// Use the running OpenClaw configuration and its installed MCP SDK, without an LLM call.
const fs = require("node:fs");
const { createRequire } = require("node:module");
const runtimeRequire = createRequire("/app/package.json");
const { Client } = runtimeRequire("@modelcontextprotocol/sdk/client/index.js");
const { SSEClientTransport } = runtimeRequire("@modelcontextprotocol/sdk/client/sse.js");

async function main() {
  if (process.env.ZHIXUN_BOT_WATER_MCP_TOKEN_FILE) {
    const { loadWaterMcpToken } = await import("/opt/zhixun-bot/water-mcp-token.mjs");
    process.env.ZHIXUN_BOT_WATER_MCP_TOKEN = loadWaterMcpToken();
  }
  const config = JSON.parse(fs.readFileSync("/home/node/.openclaw/openclaw.json", "utf8"));
  const server = config.mcp.servers.water_unified;
  const headers = {};
  let authorizationCount = 0;
  for (const [name, raw] of Object.entries(server.headers ?? {})) {
    if (name.toLowerCase() === "authorization") authorizationCount++;
    headers[name] = raw.replace(/\$\{([A-Z0-9_]+)\}/g, (_, variable) => {
      if (!process.env[variable]) throw new Error("Unresolved runtime header variable");
      return process.env[variable];
    });
  }
  const authorization = Object.entries(headers).find(([name]) => name.toLowerCase() === "authorization")?.[1];
  if (authorizationCount !== 1 || !authorization?.startsWith("Bearer ")) {
    throw new Error("Missing configured Water MCP Bearer header");
  }
  const client = new Client({ name: "openclaw-water-auth-probe", version: "1.0" });
  const transport = new SSEClientTransport(new URL(server.url), {
    requestInit: { headers },
    eventSourceInit: {
      fetch: (url, init) => {
        const requestHeaders = new Headers(init?.headers);
        for (const [key, value] of Object.entries(headers)) requestHeaders.set(key, value);
        return fetch(url, { ...init, headers: requestHeaders });
      },
    },
  });
  try {
    await client.connect(transport);
    const tools = await client.listTools();
    const result = await client.callTool({ name: "list_available_stations", arguments: {} });
    if (result.isError) throw new Error("Read-only tool returned an error");
    console.log(`OpenClaw runtime config + installed SDK: initialize OK, tools=${tools.tools.length}, read-only call OK`);
  } finally {
    await client.close();
  }
}

main().catch(() => {
  console.error("OpenClaw Water MCP probe failed; secrets and business records omitted");
  process.exitCode = 1;
});
