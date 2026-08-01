# ── Zotero MCP Server ────────────────────────────────────────────────
# Source: mylibrary (build-time install, local-first + git fallback)
# start.sh rsyncs ~/code/mylibrary into .mylibrary-src/ before build.
# When missing (e.g. CI / remote build), falls back to git clone.

FROM python:3.12-slim

WORKDIR /app

# Copy mylibrary source (rsync'd by start.sh, or empty dir for git fallback)
COPY .mylibrary-src/ /tmp/mylibrary-src/

RUN if [ -f /tmp/mylibrary-src/hydrolitagent/zotero/server.py ]; then \
      echo "   📚 zotero-mcp: installing from local source" && \
      cp /tmp/mylibrary-src/hydrolitagent/zotero/server.py /app/server.py; \
    else \
      echo "   📚 zotero-mcp: local source not found, cloning from GitHub" && \
      apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
      git clone --depth 1 https://github.com/OuyangWenyu/mylibrary.git /tmp/mylibrary-git && \
      cp /tmp/mylibrary-git/hydrolitagent/zotero/server.py /app/server.py && \
      apt-get purge -y git && rm -rf /var/lib/apt/lists/*; \
    fi && \
    pip install --no-cache-dir mcp httpx pyzotero && \
    rm -rf /tmp/mylibrary-src /tmp/mylibrary-git

EXPOSE 8002

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/server.py"]
