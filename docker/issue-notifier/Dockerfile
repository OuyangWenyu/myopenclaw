# Feishu issue notifier — consumes repo-scanner MCP get_issue_events.
FROM python:3.12-slim

WORKDIR /app

# MCP client only; the notifier is stdlib otherwise.
RUN pip install --no-cache-dir mcp==1.28.1

COPY config.py mapping.py feishu.py state.py main.py /app/

ENV PYTHONUNBUFFERED=1
ENV ISSUE_NOTIFIER_CONFIG=/data/config.toml
ENV ISSUE_NOTIFIER_STATE=/data/state.sqlite
ENV ISSUE_NOTIFIER_STATUS=/data/status.json

# Freshness check: successful poll within 5 minutes AND no send failures.
# A permanently poisoned card must not look healthy while nothing is delivered.
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD python3 -c "import json,time; d=json.load(open('/data/status.json')); assert time.time()-d.get('last_success_epoch',0) < 300, 'poll stale'; assert not d.get('send_failed'), 'send failing'; assert not d.get('dead'), 'dead-lettered cards'"

CMD ["python", "/app/main.py"]
