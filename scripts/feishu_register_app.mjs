#!/usr/bin/env node
/**
 * Print or run the Feishu QR registration for a chat-only Hermes agent.
 *
 * `--print-options` writes the registerApp options as JSON and does not
 * import the SDK. `--render-page` and `--serve-check` draw a verification
 * URL as a QR code. A real scan serves that page on 127.0.0.1 and writes the
 * credential JSON to stdout. The app secret is never written to the page or
 * to stderr.
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packageRoot = path.join(repoRoot, "services", "agent-bind");
const LOOPBACK_HOST = "127.0.0.1";
const BIND_PORT = 8766;

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
 * Refuse any bind address other than loopback.
 *
 * @param {string} host Address the page would bind.
 * @throws {Error} When `host` is not 127.0.0.1. No socket is opened.
 */
function requireLoopback(host) {
  if (host !== LOOPBACK_HOST) {
    const error = new Error("the bind page listens on 127.0.0.1 only");
    error.code = "public_bind_refused";
    throw error;
  }
}

/**
 * Escape text that is interpolated into HTML.
 *
 * @param {string} value Raw text.
 * @returns {string} Text safe to place in an HTML text node or attribute.
 */
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => {
    switch (character) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      default:
        return "&#39;";
    }
  });
}

/**
 * Build the one-page QR document for a verification URL.
 *
 * @param {string} url Feishu or Lark verification URL from `onQRCodeReady`.
 * @returns {Promise<string>} HTML. It contains the QR image and the URL, not
 *   an app secret.
 */
async function renderPage(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    const error = new Error("verification url must be http(s)");
    error.code = "bad_url";
    throw error;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    const error = new Error("verification url must be http(s)");
    error.code = "bad_url";
    throw error;
  }
  const require = createRequire(path.join(packageRoot, "package.json"));
  const qrcode = require("qrcode");
  const svg = (
    await qrcode.toString(url, {
      type: "svg",
      margin: 1,
      errorCorrectionLevel: "M",
    })
  ).replace("<svg ", '<svg width="280" height="280" ');
  const safeUrl = escapeHtml(url);
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="1">
<title>绑定飞书助手</title>
<style>
  html, body { height: 100%; margin: 0; }
  body {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    font: 15px/1.4 sans-serif;
  }
  svg { width: 280px; height: 280px; }
  .link { max-width: 280px; margin: 0; font-size: 12px; word-break: break-all; }
  .note { margin: 0; color: #555; }
</style>
</head>
<body>
<p class="note">用飞书扫描下面的二维码，新建一个只负责聊天的助手。</p>
${svg}
<p class="link"><a href="${safeUrl}">${safeUrl}</a></p>
<p class="note">应用密钥不会显示在这个页面上。</p>
</body>
</html>
`;
}

/**
 * Build the page shown after the container has been started.
 *
 * @returns {string} HTML with no credential fields.
 */
function renderReadyPage() {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>绑定飞书助手</title></head>
<body>
<p>已启动，可以私聊</p>
<p>应用密钥不会显示在这个页面上。</p>
</body>
</html>
`;
}

/**
 * Serve one HTML document on loopback. ``setHtml`` replaces later responses.
 *
 * @param {string} html Initial document.
 * @param {string} host Must be 127.0.0.1.
 * @param {number} port TCP port. Zero asks the OS for a free port.
 * @returns {Promise<{server: import("node:http").Server, pageUrl: string, setHtml: (next: string) => void}>}
 */
function createPageServer(html, host, port) {
  requireLoopback(host);
  let body = html;
  const server = http.createServer((request, response) => {
    const payload = Buffer.from(body);
    response.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Length": String(payload.length),
      "Cache-Control": "no-store",
    });
    response.end(payload);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      const address = server.address();
      resolve({
        server,
        pageUrl: `http://${host}:${address.port}/`,
        setHtml(next) {
          body = next;
        },
      });
    });
  });
}

/**
 * Serve the QR page on loopback.
 *
 * @param {string} url Verification URL drawn as a QR code.
 * @param {string} host Must be 127.0.0.1.
 * @param {number} port TCP port. Zero asks the OS for a free port.
 * @returns {Promise<{server: import("node:http").Server, pageUrl: string}>}
 *   The open server and the URL an operator opens in a browser.
 */
function listen(url, host, port) {
  return renderPage(url).then((html) => createPageServer(html, host, port));
}

/**
 * Write the agent home and, unless this is a dry run, start its container.
 *
 * The credential travels on the child's stdin. The returned record and both
 * stdio streams must not contain the app secret.
 *
 * @param {{root: string, envFile: string, dryRun: boolean, credential: object}} request
 * @returns {object} Public launch record from ``bind_agent.py``.
 */
function applyCredential(request) {
  const result = spawnSync(
    "uv",
    [
      "run",
      "--with",
      "pyyaml",
      "python3",
      "scripts/bind_agent.py",
      "--materialize-stdin",
      "--root",
      request.root,
      "--env-file",
      request.envFile,
    ],
    {
      cwd: repoRoot,
      input: JSON.stringify(request.credential),
      encoding: "utf8",
    },
  );
  const secret = request.credential.client_secret || "";
  const combined = `${result.stdout || ""}${result.stderr || ""}`;
  if (secret && combined.includes(secret)) {
    const error = new Error("launch record contained a secret");
    error.code = "secret_leaked";
    throw error;
  }
  if (result.status !== 0) {
    process.stderr.write(result.stderr || "materialize failed\n");
    const error = new Error("materialize failed");
    error.code = "materialize_failed";
    throw error;
  }
  const record = JSON.parse(result.stdout);
  if (!request.dryRun) {
    const up = spawnSync(record.up_command[0], record.up_command.slice(1), {
      encoding: "utf8",
    });
    if (up.status !== 0) {
      process.stderr.write(up.stderr || "compose up failed\n");
      const error = new Error("compose up failed");
      error.code = "compose_failed";
      throw error;
    }
  }
  return record;
}

/**
 * Bind the page, request it once, and write the body to stdout.
 *
 * @param {string} url Verification URL.
 * @returns {Promise<void>} Resolves after the server has closed.
 */
async function serveCheck(url) {
  const { server, pageUrl } = await listen(url, LOOPBACK_HOST, 0);
  try {
    const response = await fetch(pageUrl);
    process.stdout.write(Buffer.from(await response.arrayBuffer()));
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

/**
 * Run registerApp, write the agent home, and start its container.
 *
 * Stdout receives the public launch record. The app secret stays on the
 * stdin of the materialize process and is never written to the page or stderr.
 *
 * @returns {Promise<void>}
 */
async function scan() {
  const require = createRequire(path.join(packageRoot, "package.json"));
  const lark = require("@larksuiteoapi/node-sdk");
  const options = loadOptions();
  let page;
  try {
    const result = await lark.registerApp({
      ...options,
      onQRCodeReady(info) {
        listen(info.url, LOOPBACK_HOST, BIND_PORT)
          .then((bound) => {
            page = bound;
            process.stderr.write(`${bound.pageUrl}\n`);
            if (process.platform === "darwin") {
              spawn("open", [bound.pageUrl], { stdio: "ignore", detached: true }).unref();
            }
          })
          .catch((error) => {
            process.stderr.write(`${error.message}\n`);
          });
      },
    });
    const user = result.user_info || {};
    const record = applyCredential({
      root: path.join(os.homedir(), ".hermes-agents"),
      envFile: path.join(repoRoot, ".env"),
      dryRun: false,
      credential: {
        client_id: result.client_id,
        client_secret: result.client_secret,
        open_id: user.open_id || "",
        tenant_brand: user.tenant_brand || "feishu",
      },
    });
    if (page) {
      page.setHtml(renderReadyPage());
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    process.stdout.write(
      `${JSON.stringify({
        agent_id: record.agent_id,
        container_name: record.container_name,
        compose_file: record.compose_file,
        created: record.created,
      })}\n`,
    );
  } finally {
    if (page) {
      await new Promise((resolve) => page.server.close(resolve));
    }
  }
}

/**
 * Read one flag value from argv.
 *
 * @param {string} flag Flag including the leading dashes.
 * @returns {string | undefined} The following argument, if present.
 */
function argValue(flag) {
  const index = process.argv.indexOf(flag);
  if (index === -1 || index + 1 >= process.argv.length) {
    return undefined;
  }
  return process.argv[index + 1];
}

/**
 * Report a handled error without printing a credential.
 *
 * @param {Error & {code?: string}} error Failure from render, listen, or scan.
 */
function fail(error) {
  const code = error && error.code ? error.code : "register_failed";
  process.stderr.write(`${error.message || code}\n`);
  process.exit(1);
}

const pageUrl = argValue("--render-page") || argValue("--serve-check");
const host = argValue("--host") || LOOPBACK_HOST;

if (process.argv.includes("--print-options")) {
  process.stdout.write(`${JSON.stringify(loadOptions())}\n`);
} else if (pageUrl && host !== LOOPBACK_HOST) {
  process.stderr.write("the bind page listens on 127.0.0.1 only\n");
  process.exit(1);
} else if (process.argv.includes("--render-ready")) {
  process.stdout.write(renderReadyPage());
} else if (argValue("--render-page")) {
  renderPage(argValue("--render-page")).then(
    (html) => process.stdout.write(html),
    fail,
  );
} else if (process.argv.includes("--apply-credential")) {
  const root = argValue("--root");
  const envFile = argValue("--env-file");
  if (!root || !envFile) {
    process.stderr.write("--apply-credential requires --root and --env-file\n");
    process.exit(1);
  }
  try {
    const credential = JSON.parse(fs.readFileSync(0, "utf8"));
    const record = applyCredential({
      root,
      envFile,
      dryRun: process.argv.includes("--dry-run"),
      credential,
    });
    process.stdout.write(`${JSON.stringify(record)}\n`);
  } catch (error) {
    fail(error);
  }
} else if (argValue("--serve-check")) {
  serveCheck(argValue("--serve-check")).catch(fail);
} else {
  scan().catch(fail);
}
