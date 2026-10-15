#!/usr/bin/env bash
# member-check — 团队能力接入自检（薄包装：build 缓存 + docker run）
#
# 用法:
#   cp .env.member.example .env.member   # 首次：填 URL / token（文件在 .gitignore 内）
#   ./scripts/member-check.sh [检查器参数...]   # 参数原样透传（如 --ca /ca/xxx.pem，容器内路径视角）
#
# 可选环境变量:
#   MEMBER_CHECK_DOCKER_NETWORK   让检查器容器加入指定 docker 网络
#     （测本机主栈服务用，例: MEMBER_CHECK_DOCKER_NETWORK=myopenclaw_myopenclaw-net ./scripts/member-check.sh）
#   MEMBER_CHECK_ENV_FILE         改用指定 env 文件（默认 .env.member；支持 ~ 展开）
#
# 退出码（与检查器契约一致）:
#   0 = 无失败（⚪ 跳过允许存在） / 1 = 有探测失败 / 2 = 配置或脚本错误
#   Docker 守护进程不可用 / 构建失败等**基础设施错误统一归 2** —— 不与「探测失败」混淆。

set -euo pipefail

# Windows git-bash（MSYS）会改写 -v 参数里的 /ca/... 路径 —— 关闭该转换（对非 MSYS 无副作用）
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1 ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

IMAGE="myopenclaw/member-check:latest"
ENV_FILE="${MEMBER_CHECK_ENV_FILE:-.env.member}"
ENV_FILE="${ENV_FILE/#\~/${HOME}}"   # bash 3.2 支持的前缀替换：展开开头 ~
CA_FILE="${REPO_ROOT}/docker/tianyi-bot/ca/server-monitoring-ca.pem"
BASE_FALLBACK="docker.m.daocloud.io/library/python:3.12-slim"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "❌ 缺少 ${ENV_FILE} —— 首次使用："
  if [[ "${ENV_FILE}" == ".env.member" ]]; then
    echo "     cp .env.member.example .env.member   # 然后填写 URL / token"
  fi
  echo "   详细说明见 docs/member-check.md"
  exit 2
fi

# 基础镜像选择：本地已有哪个就直接用哪个，避免每次运行都先撞一遍不可达的
# Docker Hub（2026-10-14 实测：不可达网络下每次白等 40-55 秒才回退）。
BASE_ARGS=()
if ! docker image inspect python:3.12-slim >/dev/null 2>&1; then
  if docker image inspect "${BASE_FALLBACK}" >/dev/null 2>&1; then
    BASE_ARGS=(--build-arg "BASE_IMAGE=${BASE_FALLBACK}")
  fi
fi

echo "🔨 构建检查器镜像（有缓存则秒过）..."
if ! docker build -q -t "${IMAGE}" ${BASE_ARGS[@]+"${BASE_ARGS[@]}"} docker/member-check >/dev/null; then
  echo "⚠️  构建失败 —— 回退国内镜像源重试（Docker Hub 不可达时属预期）..."
  if ! docker build -q -t "${IMAGE}" \
      --build-arg "BASE_IMAGE=${BASE_FALLBACK}" docker/member-check >/dev/null; then
    echo "❌ 检查器镜像构建失败（Docker 未运行？网络 / 镜像源不通？）" >&2
    exit 2
  fi
fi

NETWORK_ARGS=()
if [[ -n "${MEMBER_CHECK_DOCKER_NETWORK:-}" ]]; then
  NETWORK_ARGS=(--network "${MEMBER_CHECK_DOCKER_NETWORK}")
  echo "🔗 检查器加入网络: ${MEMBER_CHECK_DOCKER_NETWORK}"
fi

VOLUME_ARGS=()
if [[ -f "${CA_FILE}" ]]; then
  VOLUME_ARGS=(-v "${CA_FILE}:/ca/server-monitoring-ca.pem:ro")
else
  echo "⚠️  仓库内 CA 缺失（${CA_FILE}）—— 跳过挂载；若配置了服务器监控会报配置错误（退出码 2）"
fi

echo "🚀 开始自检（token 不会出现在输出里）..."
echo
rc=0
docker run --rm \
  ${NETWORK_ARGS[@]+"${NETWORK_ARGS[@]}"} \
  ${VOLUME_ARGS[@]+"${VOLUME_ARGS[@]}"} \
  --env-file "${ENV_FILE}" \
  "${IMAGE}" "$@" || rc=$?
if [[ ${rc} -ge 125 ]]; then
  # 125/126/127 = docker 自身错误（daemon 未开、--network 不存在等）—— 契约里属「脚本错误」
  echo "❌ docker 运行失败（退出码 ${rc}；Docker 未运行 / 参数错误？）" >&2
  exit 2
fi
exit "${rc}"
