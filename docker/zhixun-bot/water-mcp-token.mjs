import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function loadWaterMcpToken(env = process.env) {
  const token = (env.ZHIXUN_BOT_WATER_MCP_TOKEN ?? "").trim();
  const secret = (env.ZHIXUN_BOT_WATER_MCP_TOKEN_FILE ?? "").trim();
  if (token && secret) throw new Error("Configure only one Water MCP token source");
  if (!secret) {
    if (!token) throw new Error("Water MCP requires a token or token file");
    return token;
  }

  let descriptor;
  try {
    descriptor = fs.openSync(secret, fs.constants.O_RDONLY |
      (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.size > 4096) throw new Error();
    if (process.platform !== "win32" &&
        (stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0)) throw new Error();
    const buffer = Buffer.alloc(4097);
    const length = fs.readSync(descriptor, buffer, 0, buffer.length, 0);
    if (length > 4096) throw new Error();
    const value = buffer.subarray(0, length).toString("utf8").replace(/\r?\n$/, "");
    if (!/^[\x21-\x7e]{32,4096}$/.test(value)) throw new Error();
    return value;
  } catch {
    throw new Error("Water MCP token file must be a readable protected regular file owned by the runtime user");
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // For the entrypoint's captured command substitution; never call with shell tracing.
  process.stdout.write(loadWaterMcpToken());
}
