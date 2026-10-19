// Use the running OpenClaw configuration and its installed MCP SDK, without an LLM call.
const fs = require("node:fs");
const { createRequire } = require("node:module");
const runtimeRequire = createRequire("/app/package.json");
const { Client } = runtimeRequire("@modelcontextprotocol/sdk/client/index.js");
const { SSEClientTransport } = runtimeRequire("@modelcontextprotocol/sdk/client/sse.js");

async function main() {
  const config = JSON.parse(fs.readFileSync("/home/node/.openclaw/openclaw.json", "utf8"));
  const server = config.mcp.servers.water_unified;
  if (!server.headers?.Authorization?.startsWith("Bearer ")) {
    throw new Error("Missing configured Water MCP Bearer header");
  }
  const client = new Client({ name: "openclaw-water-auth-probe", version: "1.0" });
  const transport = new SSEClientTransport(new URL(server.url), {
    requestInit: { headers: server.headers },
    eventSourceInit: {
      fetch: (url, init) => {
        const headers = new Headers(init?.headers);
        for (const [key, value] of Object.entries(server.headers)) headers.set(key, value);
        return fetch(url, { ...init, headers });
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
