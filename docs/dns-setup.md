# 中国域名 DNS 配置

## 问题背景

当系统使用的 DNS 服务器无法正确解析中国域名时，本项目的部分服务会连接失败：

| 服务 | 域名 | 影响范围 |
|------|------|----------|
| DeepSeek API | api.deepseek.com | Hermes / OpenClaw 默认模型 |
| 智谱 GLM API | open.bigmodel.cn | Claude Code 后端 |
| 钉钉 Stream | api.dingtalk.com, wss-open-connection.dingtalk.com | Hermes / OpenClaw 钉钉机器人 |
| 飞书 WebSocket | open.feishu.cn | Hermes 飞书机器人 |
| Moonshot API | api.moonshot.cn | OpenClaw 备用模型 |
| GitCode | gitcode.com | 代码托管 |

典型表现：日志中出现 `ENOTFOUND`、`NameResolutionError`、`getaddrinfo failed` 等错误。

## 解决方案

macOS 支持 `/etc/resolver/` 机制，可以按域名指定 DNS 服务器。本项目涉及的中国域名统一使用阿里云公共 DNS (223.5.5.5) 解析，Docker 容器通过宿主 DNS 转发自动受益，无需额外配置。

## 一键配置

```bash
./scripts/setup-dns.sh
```

脚本会自动：
1. 在 `/etc/resolver/` 创建域名级别的 DNS 路由规则（需 sudo）
2. 在 `/etc/hosts` 写入备份条目（IP 过期时可再次运行刷新）
3. 验证所有域名解析正常

## 手动配置

如果需要手动配置，创建以下文件：

```bash
# /etc/resolver/ 下的每个文件对应一个域名及其子域名
# 文件名 = 域名，内容 = nameserver 指令

DOMAINS="alibabadns.com aliyunddos1022.com bigmodel.cn bytedns1.com cdngslb.com deepseek.com dingtalk.com eo.dnse1.com feishu.cn gitcode.com gtm-a4b8.com moonshot.cn open.bigmodel.cn yundunwaf3.com zhipu.ai"

for domain in $DOMAINS; do
  echo "nameserver 223.5.5.5" | sudo tee /etc/resolver/$domain
done

# 刷新 DNS 缓存
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

## 关键细节

### CNAME 链问题

中国服务的域名通常使用 CDN/GSLB，解析时经过多级 CNAME 跳转。CNAME 链中的某些中间域名不在服务主域名下，如果系统 DNS 无法解析这些外域，整个链路就会失败。

| 服务 | CNAME 链经过的外域 | 说明 |
|------|-------------------|------|
| 钉钉 api.dingtalk.com | `gds.alibabadns.com` | 阿里云 GSLB |
| DeepSeek api.deepseek.com | `eo.dnse1.com` | 火山引擎 CDN |
| 飞书 open.feishu.cn | `bytedns1.com` → `cdngslb.com` | 字节 CDN → GSLB |
| Moonshot api.moonshot.cn | `aliyunddos1022.com` | 阿里云 DDoS 防护 |
| 智谱 open.bigmodel.cn | `yundunwaf3.com` → `gtm-a4b8.com` | 阿里云 WAF → GTM |

以钉钉为例：

```
api.dingtalk.com → v6-cname.dingtalk.com → region-cname.dingtalk.com
→ region-cname.dingtalk.com.gds.alibabadns.com → ... → 最终 IP
```

CNAME 链经过了 `gds.alibabadns.com`（阿里云 GSLB 内部域），它不在 `dingtalk.com` 域下，因此需要单独配置 `/etc/resolver/alibabadns.com`。**如果不配这个，即使 `dingtalk.com` 的 resolver 正确，`api.dingtalk.com` 仍然会解析失败。**

`setup-dns.sh` 已包含所有已知的外域 resolver，新发现的 CNAME 外域也需加入。

### /etc/hosts 备份作用

`/etc/hosts` 中的条目优先级高于 DNS 查询，作为额外的安全网。但其中的 IP 来自 CDN，会随时间变化，需要定期更新。运行 `./scripts/setup-dns.sh` 即可刷新。

### Docker 容器 DNS 链路

```
容器应用 → Docker DNS (127.0.0.11) → 宿主 DNS → /etc/resolver/ → 223.5.5.5
```

Docker Desktop for Mac 的内嵌 DNS 服务器会将查询转发到宿主，宿主按 `/etc/resolver/` 规则路由到指定 DNS。因此只需在宿主配置 resolver，所有容器自动生效，不需要在 `docker-compose.yml` 中用 `extra_hosts` 硬编码 IP。

### 代理工具设置

如果使用网络代理工具，需确保中国 IP 地址走直连（不经过代理隧道），否则即使 DNS 解析正确，TCP 连接也可能超时或被拦截。通常的配置方式是在代理工具中启用"中国网站直连"或"分流规则"。
