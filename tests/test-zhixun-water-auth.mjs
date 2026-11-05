import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const root = fileURLToPath(new URL("../", import.meta.url));
const token = "test-water-key-0123456789abcdefghijk";
const env = {
  ...process.env,
  ZHIXUN_BOT_FEISHU_APP_ID: "test_id",
  ZHIXUN_BOT_FEISHU_APP_SECRET: "test_secret",
  ZHIXUN_BOT_MODEL_API_KEY: "test_model",
  ZHIXUN_BOT_MODEL_ID: "test-model",
  ZHIXUN_BOT_MODEL_BASE_URL: "https://model.example.invalid",
  ZHIXUN_BOT_GLM_API_KEY: "test_glm",
  ZHIXUN_BOT_WATER_MCP_URL: "http://zhixun-water-mcp:18201/sse",
  ZHIXUN_BOT_WATER_MCP_TRANSPORT: "sse",
  ZHIXUN_BOT_WATER_MCP_TOKEN: "",
  ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: "",
};

function fixture(t, content = token + "\n") {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "water-auth-test-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const secret = path.join(directory, "token");
  fs.writeFileSync(secret, content, { mode: 0o600 });
  return { directory, secret };
}

function readSecret(extraEnv) {
  return spawnSync(process.execPath, ["docker/zhixun-bot/water-mcp-token.mjs"], {
    cwd: root, env: { ...env, ...extraEnv }, encoding: "utf8",
  });
}

test("protected file token can be loaded without an env token", (t) => {
  const { secret } = fixture(t);
  const result = readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, token);
});

test("environment token mode remains compatible", () => {
  const result = readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN: "legacy_token" });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, "legacy_token");
});

test("missing or conflicting credential sources fail closed", (t) => {
  const { secret } = fixture(t);
  assert.notEqual(readSecret({}).status, 0);
  assert.notEqual(readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret,
    ZHIXUN_BOT_WATER_MCP_TOKEN: token }).status, 0);
  assert.notEqual(readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret + ".missing" }).status, 0);
});

for (const content of ["", "short", "x".repeat(4097), token + "\nother", "中".repeat(32)]) {
  test(`invalid file secret rejected (${content.length} characters)`, (t) => {
    const { secret } = fixture(t, content);
    const result = readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret });
    assert.notEqual(result.status, 0);
    assert.equal(result.stdout, "");
    assert.ok(!result.stderr.includes(token));
  });
}

test("directory cannot be used as a token file", (t) => {
  const { directory } = fixture(t);
  assert.notEqual(readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: directory }).status, 0);
});

test("POSIX permissions and symlinks are rejected", { skip: process.platform === "win32" }, (t) => {
  const { directory, secret } = fixture(t);
  fs.chmodSync(secret, 0o644);
  assert.notEqual(readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret }).status, 0);
  fs.chmodSync(secret, 0o600);
  const link = path.join(directory, "link");
  fs.symlinkSync(secret, link);
  assert.notEqual(readSecret({ ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: link }).status, 0);
});

test("actual entrypoint token stanza exports the key without printing it", {
  skip: process.platform === "win32",
}, (t) => {
  const { secret } = fixture(t);
  const source = fs.readFileSync(path.join(root, "docker/zhixun-bot/entrypoint.sh"), "utf8");
  const start = source.indexOf('if [ -n "${ZHIXUN_BOT_WATER_MCP_TOKEN_FILE:-}" ]; then');
  assert.ok(start >= 0);
  const end = source.indexOf("\nfi", start);
  assert.ok(end > start);
  const stanza = source.slice(start, end + 3).replaceAll(
    "/opt/zhixun-bot/water-mcp-token.mjs", path.join(root, "docker/zhixun-bot/water-mcp-token.mjs"),
  );
  const check = 'if(!process.env.ZHIXUN_BOT_WATER_MCP_TOKEN || process.env.ZHIXUN_BOT_WATER_MCP_TOKEN_FILE) process.exit(1); console.log("gateway inherited token")';
  const script = `set -eu\n${stanza}\nnode -e '${check}'`;
  const result = spawnSync("sh", ["-c", script], {
    cwd: root, env: { ...env, ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret }, encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "gateway inherited token");
  assert.ok(!result.stdout.includes(token));

  const rejected = spawnSync("sh", ["-c", script], {
    cwd: root, env: { ...env, ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret + ".missing" }, encoding: "utf8",
  });
  assert.notEqual(rejected.status, 0);
  assert.equal(rejected.stdout, "");
});

for (const mode of ["env", "file"]) {
  test(`native renderer preserves one runtime authorization header (${mode})`, (t) => {
    const { directory, secret } = fixture(t);
    const output = path.join(directory, "openclaw.json");
    const extra = mode === "file" ? { ZHIXUN_BOT_WATER_MCP_TOKEN_FILE: secret } :
      { ZHIXUN_BOT_WATER_MCP_TOKEN: token };
    const result = spawnSync(process.execPath, ["docker/zhixun-bot/render-config.mjs",
      "docker/zhixun-bot/openclaw.json.template", output], {
      cwd: root, env: { ...env, ...extra }, encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    const text = fs.readFileSync(output, "utf8");
    assert.ok(!text.includes(token));
    const config = JSON.parse(text);
    const server = config.mcp.servers.water_unified;
    assert.equal(server.url, env.ZHIXUN_BOT_WATER_MCP_URL);
    assert.equal(server.transport, "sse");
    const headers = Object.entries(server.headers).filter(([key]) => key.toLowerCase() === "authorization");
    assert.deepEqual(headers, [["authorization", "Bearer ${ZHIXUN_BOT_WATER_MCP_TOKEN}"]]);
    assert.ok(server.toolFilter.exclude.includes("dispatch_task_execute"));
    assert.deepEqual(config.agents.defaults.model.fallbacks, ["zhipu/glm-5.3-flash"]);
    if (process.platform !== "win32") assert.equal(fs.statSync(output).mode & 0o777, 0o600);
  });
}

test("Compose overlay explicitly enables one authenticated local instance", () => {
  const result = spawnSync("docker", ["compose", "--env-file", ".env.zhixun-bot.example",
    "-f", "docker-compose.zhixun-bot.yml", "-f", "docker-compose.zhixun-water-auth.yml",
    "config", "--format", "json"], {
    cwd: root, env: { ...process.env,
      ZHIXUN_WATER_AUTH_IMAGE: "water-auth:test",
      ZHIXUN_WATER_KEYS_FILE: path.join(root, "test-keys.json"),
      ZHIXUN_WATER_TOKEN_FILE: path.join(root, "test-token"),
    }, encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  const { services } = JSON.parse(result.stdout);
  const water = services["zhixun-water-mcp"];
  assert.equal(water.image, "water-auth:test");
  assert.ok(!water.profiles?.length);
  assert.ok(!water.build);
  assert.equal(water.ports.length, 1);
  assert.equal(water.ports[0].host_ip, "10.48.0.81");
  assert.equal(String(water.ports[0].published), "18201");
  const bot = services["openclaw-zhixun"];
  assert.equal(bot.environment.ZHIXUN_BOT_WATER_MCP_URL, env.ZHIXUN_BOT_WATER_MCP_URL);
  assert.equal(bot.environment.ZHIXUN_BOT_WATER_MCP_TRANSPORT, "sse");
  assert.equal(bot.environment.ZHIXUN_BOT_WATER_MCP_TOKEN, "");
  assert.equal(bot.environment.ZHIXUN_BOT_WATER_MCP_TOKEN_FILE, "/run/secrets/water-mcp-token");
  assert.equal(bot.depends_on["zhixun-water-mcp"].condition, "service_healthy");
  assert.ok(bot.volumes.some((mount) => mount.target === "/run/secrets/water-mcp-token" && mount.read_only));
  assert.ok(!bot.volumes.some((mount) => mount.target === "/opt/zhixun-bot/render-config.mjs"));
});

test("base Compose keeps remote Water configuration and local debug profile", () => {
  const result = spawnSync("docker", ["compose", "--env-file", ".env.zhixun-bot.example",
    "--profile", "local-water-mcp", "-f", "docker-compose.zhixun-bot.yml",
    "config", "--format", "json"], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const { services } = JSON.parse(result.stdout);
  assert.deepEqual(services["zhixun-water-mcp"].profiles, ["local-water-mcp"]);
  assert.ok(!services["zhixun-water-mcp"].ports?.length);
  assert.equal(services["openclaw-zhixun"].environment.ZHIXUN_BOT_WATER_MCP_TOKEN, "replace_me");
  assert.ok(!services["openclaw-zhixun"].depends_on);
});
