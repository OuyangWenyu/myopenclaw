# ── paper-queue MCP Server ───────────────────────────────────────────
# 论文清单的写入/查询口。**独立容器**是刻意的：队列目录只挂给它，
# 虾酱所在的 openclaw-gateway 不再挂载，于是模型够不到库（见 server.py 头部注释）。
#
# 源码就在本目录（本仓库持有），构建期拷进去即可 —— 不需要外部仓库或网络取源码。

FROM python:3.12-slim

WORKDIR /app

# 只装跑服务必需的：mcp 提供 FastMCP + streamable HTTP 传输
RUN pip install --no-cache-dir "mcp>=1.28,<2"

COPY server.py schema.sql /app/

EXPOSE 8003

ENV PYTHONUNBUFFERED=1
# PAPER_QUEUE_DB / PAPER_QUEUE_ACTOR_SECRET 由 compose 注入

# 先把库升到当前 schema 再对外服务：迁移要是在第一次工具调用时才跑，
# 中间那段窗口检查器会把存量老标签判成非法值（假红淹没真异常）。
CMD ["sh", "-c", "python /app/server.py --migrate && exec python /app/server.py"]
