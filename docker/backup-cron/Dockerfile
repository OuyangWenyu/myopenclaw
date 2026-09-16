FROM alpine:3.20

# tzdata 不是可选项：compose 里声明了 TZ=${TZ:-Asia/Shanghai}，而 Alpine 不带
# zoneinfo —— 缺了它 musl 解析不了时区名、静默退回 UTC，于是
#   ① crond 的 `0 2 * * *` 实际在 02:00 UTC（= 10:00 北京）触发，不是 02:00 北京；
#   ② 快照目录名成了 UTC 时间戳，宿主按本地时区读会差 8 小时。
RUN apk add --no-cache bash rsync sqlite tzdata

COPY entrypoint.sh /entrypoint.sh
COPY check-cloud-root.sh /usr/local/bin/check-cloud-root.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/check-cloud-root.sh

CMD ["/entrypoint.sh"]
