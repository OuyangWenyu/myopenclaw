#!/usr/bin/env node
// 从 `openclaw cron list --all --json` 的 stdout 提取指定 declarationKey 的 job id。
// 用途：entrypoint 的 OFF 态收敛（URL 空 / DIGEST_CRON=off）需要主动回收既有
// automation —— cron rm 只接受 id（无 --declaration-key），而框架不会自动回收
// service 声明的 job（2026-10-14 复核实测）。
// 找不到 / 解析失败 → 输出空、退出码 0（调用方按"无需回收"处理，绝不误删别的 job）。
const key = process.argv[2];
let d = "";
process.stdin.on("data", (c) => (d += c));
process.stdin.on("end", () => {
  try {
    const parsed = JSON.parse(d.slice(d.indexOf("{")));
    const job = (parsed.jobs || []).find((j) => j.declarationKey === key);
    if (job && job.id) process.stdout.write(job.id);
  } catch {
    /* 空输出 = 未找到 */
  }
});
