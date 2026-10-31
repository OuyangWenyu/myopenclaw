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

# Freshness follows the configured poll interval (floor 5 min); a send failure
# still fails. Dead cards are intentionally NOT a healthcheck failure: one
# poisoned card would pin the container unhealthy for the whole 7-day retention
# window. AgentOps has a dedicated dead-letter signal instead.
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD python3 -c "import json,os,time; path=os.environ.get('ISSUE_NOTIFIER_STATUS','/data/status.json'); d=json.load(open(path)); raw=os.environ.get('ISSUE_NOTIFIER_POLL_SECONDS','60'); interval=int(raw) if raw.isdigit() else 60; assert time.time()-d.get('last_success_epoch',0) < max(300, 3*interval), 'poll stale'; assert not d.get('send_failed'), 'send failing'"

CMD ["python", "/app/main.py"]
