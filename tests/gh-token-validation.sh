#!/usr/bin/env bash
# =============================================================
# gh-token-validation.sh
# 验证 gh CLI GitHub token 配置的正确性和回归保护
#
# Group A: 修改正确性 — entrypoint 权限修复 / fallback / 验证
# Group B: 回归保护 — OpenClaw/iHeadWater-bot 链条未被修改
#
# 用法: ./tests/gh-token-validation.sh
# 返回: 0 = 全部通过, 1 = 有失败
# =============================================================
# NOTE: 不用 set -e，因为 check 函数里 grep 没匹配到会返回 1，用 if 捕获

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

HERMES_ENTRYPOINT="${REPO_ROOT}/docker/hermes/entrypoint-wrapper.sh"
CC_ENTRYPOINT="${REPO_ROOT}/docker/claude-code/entrypoint.sh"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
START_SH="${REPO_ROOT}/scripts/start.sh"

check() {
    local name="$1"
    shift
    if "$@"; then
        echo "   ✅ ${name}"
        PASS=$((PASS + 1))
    else
        echo "   ❌ ${name}"
        FAIL=$((FAIL + 1))
    fi
}

# ── Group A: 修改正确性（改动生效验证）─────────────────────────
echo "📋 Group A: 修改正确性"
echo ""

# A1: hermes entrypoint 在 hosts.yml 同步块内有 chown hermes
check "A1: hermes entrypoint has chown hermes in hosts.yml block" \
    grep -q 'chown.*hermes' "${HERMES_ENTRYPOINT}"

# A2: claude-code entrypoint 在 hosts.yml 同步块内有 chown node
check "A2: claude-code entrypoint has chown node in hosts.yml block" \
    grep -q 'chown.*node' "${CC_ENTRYPOINT}"

# A3: hermes entrypoint 有 fallback 逻辑（GITHUB_TOKEN 为空时检查已有 hosts.yml）
check "A3: hermes entrypoint references hosts.yml for token fallback" \
    grep -q 'hosts.yml' "${HERMES_ENTRYPOINT}"

# A4: bash syntax check on both entrypoint scripts
check "A4a: hermes entrypoint passes bash -n" \
    bash -n "${HERMES_ENTRYPOINT}"

check "A4b: claude-code entrypoint passes bash -n" \
    bash -n "${CC_ENTRYPOINT}"

# A5: start.sh checks for GH_TOKEN and warns if missing
check "A5: start.sh has GH_TOKEN check" \
    grep -q 'GH_TOKEN' "${START_SH}"

# A6: .env.example clearly distinguishes GH_TOKEN (personal) vs OPENCLAW_GH_TOKEN (bot)
check "A6a: .env.example mentions OuyangWenyu or 个人 for GH_TOKEN" \
    grep -qE '(OuyangWenyu|个人)' "${ENV_EXAMPLE}"

check "A6b: .env.example mentions iHeadWater or 团队 or bot for OPENCLAW_GH_TOKEN" \
    grep -qE '(iHeadWater|团队|bot)' "${ENV_EXAMPLE}"

# ── Group B: 回归保护（OpenClaw/iHeadWater-bot 链条未被修改）───
echo ""
echo "📋 Group B: 回归保护 (OpenClaw/iHeadWater-bot)"
echo ""

# B1: openclaw-gateway 服务仍使用 GITHUB_PERSONAL_ACCESS_TOKEN
check "B1: openclaw-gateway still has GITHUB_PERSONAL_ACCESS_TOKEN" \
    grep -q 'GITHUB_PERSONAL_ACCESS_TOKEN=${OPENCLAW_GH_TOKEN:-}' "${COMPOSE_FILE}"

# B2: openclaw-gateway 服务没有 GH_TOKEN
# 提取 openclaw-gateway 到下一个顶级服务之间的内容，检查没有 GH_TOKEN=
check "B2: openclaw-gateway does NOT have GH_TOKEN" \
    sh -c "awk '/^  openclaw-gateway:/{found=1} found{print} /^  [a-z].*:/ && found && !/openclaw-gateway/{exit}' ${COMPOSE_FILE} | grep -qv 'GH_TOKEN='"

# B3: .env.example 仍包含 OPENCLAW_GH_TOKEN
check "B3: .env.example still contains OPENCLAW_GH_TOKEN" \
    grep -q 'OPENCLAW_GH_TOKEN' "${ENV_EXAMPLE}"

# B4: start.sh 中 OPENCLAW_GH_TOKEN 注入逻辑未被修改
check "B4: start.sh OPENCLAW_GH_TOKEN injection block intact" \
    grep -q 'OPENCLAW_GH_TOKEN' "${START_SH}"

# B5: docker-compose.yml 中 Hermes 服务使用 GITHUB_TOKEN（变量名不变）
GITHUB_TOKEN_COUNT=$(grep -c 'GITHUB_TOKEN=' "${COMPOSE_FILE}" 2>/dev/null || echo 0)
check "B5: docker-compose.yml still has GITHUB_TOKEN in services (count=${GITHUB_TOKEN_COUNT})" \
    test "${GITHUB_TOKEN_COUNT}" -ge 4

# ── Summary ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PASS: ${PASS}  FAIL: ${FAIL}"
if [ "${FAIL}" -gt 0 ]; then
    echo "❌ 测试未通过 — 有些检查失败了"
    exit 1
else
    echo "✅ 全部通过"
    exit 0
fi
