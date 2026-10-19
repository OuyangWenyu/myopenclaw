# ── member-check 检查器 ──────────────────────────────────────────────
# 成员接入自检：对成员直连三项团队能力做 MCP 握手 + token 校验（token 从不打印）。
# 逻辑全在容器内 ⇒ Windows 成员也能用（只需 docker，无需 bash/python/uv）。
#
# mcp pin 与 scripts/check_server_monitoring_mcp.py 一致（mcp==2.2.0）：
# 协议协商细节随版本漂移，「验收时能通的，今天还通」的前提是客户端同版。
# CA 由 scripts/member-check.sh 从仓库 docker/tianyi-bot/ca/ 挂到 /ca/。

# BASE_IMAGE 可用 --build-arg 覆盖：国内网络拉不动 Docker Hub 时，
# scripts/member-check.sh 会自动回退 docker.m.daocloud.io/library/python:3.12-slim
# （与 zhixun/tianyi compose 的 openclaw 镜像同源，2026-10-14 实测可达）。
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

WORKDIR /work

RUN pip install --no-cache-dir "mcp==2.2.0"

COPY member_check.py /work/member_check.py

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/work/member_check.py"]
