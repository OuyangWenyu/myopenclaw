---
name: dns-diagnose
description: Diagnose Chinese service DNS connectivity for Docker containers. Use when Chinese services (Feishu, DingTalk, DeepSeek, Zhipu, etc.) are unreachable from containers, ENOTFOUND errors appear, or after Astrill VPN changes cause DNS issues.
---

# DNS 诊断与修复

诊断 Docker 容器内中国服务 DNS 解析问题，检查 `/etc/resolver/` 配置完整性，追踪 CNAME 链断裂点。

## 运行诊断

```bash
bash .claude/skills/dns-diagnose/check-dns.sh
```

一键检查：resolver 文件完整性 → 主机 DNS → 容器内 DNS → 失败域名的 CNAME 链追踪。

## 常见问题与修复

### 1. 新增 CDN 域名无 resolver → 容器内 DNS 失败

**症状**：主机能解析某域名，容器内 `ENOTFOUND`。`msg-frontier.feishu.cn` 通但 `open.feishu.cn` 不通。

**根因**：Docker DNS 转发器逐跳验证 CNAME 链，每跳域名都需对应的 `/etc/resolver/` 条目。当 CNAME 链路经过新的 CDN 域名（如 `queniuyk.com`）时，该域无 resolver 则整条链路断裂。

**修复**：
```bash
echo 'nameserver 223.5.5.5' | sudo tee /etc/resolver/<缺失域名>
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
# 必须重启 Docker Desktop！其 DNS 转发器只在启动时加载 resolver 列表
```

### 2. Astrill VPN DNS 劫持

**症状**：所有中国域名都无法解析，或解析到海外 IP。

**修复**：确保 `223.5.5.5`（阿里云公共 DNS）在 Astrill 不走代理的 IP 列表中。

### 3. Docker 重启后 DNS 仍不生效

**症状**：加了 resolver 文件，刷新了 DNS 缓存，容器内仍然 `ENOTFOUND`。

**修复**：必须重启 Docker Desktop 应用（`osascript -e 'quit app "Docker"'`），仅重启容器无效。Docker 的 DNS 转发器（`192.168.65.7`）在 Docker Desktop 启动时一次性读取 `/etc/resolver/` 列表。

## 维护 resolver 列表

新增中国服务依赖时，同步更新两个文件：

1. **`/etc/resolver/<domain>`** — 立即生效文件（需 Docker 重启）
2. **`scripts/setup-dns.sh`** — 配置脚本，加入 `RESOLVER_DOMAINS` 数组

`check-dns.sh` 中 `EXPECTED_DOMAINS` 数组与 `setup-dns.sh` 保持同步。

## Gotchas

- Docker Desktop 的 DNS 转发器 **不在容器内**（`127.0.0.11` 只是代理），其上游在 Docker VM（`192.168.65.7`）。`/etc/resolver/` 是 macOS 机制，Docker VM 需要在启动时读取它。重启容器不会重读 resolver 列表。
- `queniuyk.com` 和 `queniuck.com` 是飞书使用的金山云 CDN 终端域名，不同于字节 CDN（`bytedns1.com`）和 GSLB（`cdngslb.com`）。
- `scutil --dns` 显示的是 macOS 系统的 DNS 视图，Docker 不一定与之完全一致。
