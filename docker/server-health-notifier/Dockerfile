# Build from ./docker so both notifiers use the same Feishu sender.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --retries 1 --timeout 15 mcp==1.28.1
COPY issue-notifier/notifier_delivery.py /app/
COPY server-health-notifier/health_*.py /app/
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=60s --timeout=5s --start-period=45s --retries=3 \
  CMD ["python", "/app/health_notifier.py", "--healthcheck"]
CMD ["python", "/app/health_notifier.py"]
