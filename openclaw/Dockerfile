# openclaw Dockerfile
# 根据 openclaw 框架实际要求修改
FROM node:20-slim

WORKDIR /app

# 安装依赖（根据实际框架调整）
# COPY package*.json ./
# RUN npm ci --omit=dev

# 将 agent 配置挂载进容器（见 docker-compose.yml volumes）
# 实际代码通过 volume 挂载或在此 COPY

EXPOSE 3000

CMD ["node", "index.js"]
