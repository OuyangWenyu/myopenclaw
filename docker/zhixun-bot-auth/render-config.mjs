import fs from "node:fs";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";

const [template, output] = process.argv.slice(2);
if (!template || !output) throw new Error("Expected template and output paths");

const secretPath = process.env.ZHIXUN_WATER_TOKEN_FILE;
let token;
let descriptor;
try {
  descriptor = fs.openSync(secretPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  const stat = fs.fstatSync(descriptor);
  if (!stat.isFile() || stat.size > 4096 ||
      stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0) throw new Error();
  token = fs.readFileSync(descriptor, "utf8").replace(/\r?\n$/, "");
  if (!/^[\x21-\x7e]{32,4096}$/.test(token)) throw new Error();
} catch {
  throw new Error("Water MCP requires a protected token file owned by its runtime user");
} finally {
  if (descriptor !== undefined) fs.closeSync(descriptor);
}

// Render using the deployment's original renderer, retaining local capabilities.
execFileSync(process.execPath, [
  "/opt/water-auth/original-render-config.mjs", template, output,
], { stdio: "inherit" });
const config = JSON.parse(fs.readFileSync(output, "utf8"));
const server = config.mcp?.servers?.water_unified;
if (!server) throw new Error("Missing existing water_unified MCP configuration");
server.headers = { ...server.headers, Authorization: `Bearer ${token}` };

const temporary = `${output}.${randomUUID()}.tmp`;
try {
  fs.writeFileSync(temporary, `${JSON.stringify(config, null, 2)}\n`, {
    mode: 0o600, flag: "wx",
  });
  fs.renameSync(temporary, output);
} finally {
  if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
}
