FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir mcp httpx pyzotero

COPY server.py /app/

EXPOSE 8002

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/server.py"]
