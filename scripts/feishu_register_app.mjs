#!/usr/bin/env node
/**
 * Print or run the Feishu QR registration for a chat-only Hermes agent.
 *
 * `--print-options` writes the registerApp options as JSON and does not
 * import the SDK. A real scan writes the credential JSON to stdout and the
 * QR URL to stderr. The app secret is never written to stderr.
 */

import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packageRoot = path.join(repoRoot, "services", "agent-bind");

/**
 * Load the registerApp options from the committed defaults file.
 *
 * @returns {object} Options without the QR callback, which this process adds.
 */
function loadOptions() {
  const result = spawnSync(
    "uv",
    [
      "run",
      "--with",
      "pyyaml",
      "python3",
      "scripts/bind_agent.py",
      "--print-register-options",
    ],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (result.status !== 0) {
    process.stderr.write(result.stderr || "failed to load register options\n");
    process.exit(result.status || 1);
  }
  return JSON.parse(result.stdout);
}

/**
 * Run registerApp and write a credential object to stdout.
 *
 * The QR URL goes to stderr. The returned secret stays on stdout so a caller
 * can pipe it into materialize without it landing in a log.
 */
async function scan() {
  const require = createRequire(path.join(packageRoot, "package.json"));
  const lark = require("@larksuiteoapi/node-sdk");
  const options = loadOptions();
  const result = await lark.registerApp({
    ...options,
    onQRCodeReady(info) {
      process.stderr.write(`${info.url}\n`);
    },
  });
  const user = result.user_info || {};
  process.stdout.write(
    `${JSON.stringify({
      client_id: result.client_id,
      client_secret: result.client_secret,
      open_id: user.open_id || "",
      tenant_brand: user.tenant_brand || "feishu",
    })}\n`,
  );
}

if (process.argv.includes("--print-options")) {
  process.stdout.write(`${JSON.stringify(loadOptions())}\n`);
} else {
  scan().catch((error) => {
    const code = error && error.code ? error.code : "register_failed";
    process.stderr.write(`${code}\n`);
    process.exit(1);
  });
}
