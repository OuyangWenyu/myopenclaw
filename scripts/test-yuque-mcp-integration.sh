#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=0

check() {
  local description="$1"
  shift
  if "$@"; then
    printf 'PASS: %s\n' "${description}"
  else
    printf 'FAIL: %s\n' "${description}"
    FAILURES=$((FAILURES + 1))
  fi
}

contains() {
  local file="$1"
  local text="$2"
  grep -Fq -- "${text}" "${file}"
}

not_contains() {
  local file="$1"
  local text="$2"
  ! grep -Fq -- "${text}" "${file}"
}

contains_implementation() {
  local file="$1"
  local text="$2"
  grep -F -- "${text}" "${file}" | grep -vq '^check '
}

docker_host_path() {
  local path="$1"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) cygpath -m "${path}" ;;
    *) printf '%s\n' "${path}" ;;
  esac
}

posix_root_start_fails_closed() {
  local fixture
  fixture="$(mktemp -d)"
  local marker="${fixture}/docker-called"
  uname() { echo Linux; }
  id() {
    case "${1:-}" in
      -u|-g) echo 0 ;;
      *) command id "$@" ;;
    esac
  }
  docker() { touch "${DOCKER_CALLED_MARKER:?}"; }
  export -f uname id docker

  local output status docker_called=false
  set +e
  output="$(
    DOCKER_CALLED_MARKER="${marker}" \
      "${BASH}" "${START_SCRIPT}" --yuque 2>&1
  )"
  status=$?
  set -e
  unset -f uname id docker
  if [[ -e "${marker}" ]]; then
    docker_called=true
  fi
  rm -rf -- "${fixture}"

  if ! [[ "${status}" -ne 0 \
    && "${output}" == *"refusing to run yuque-mcp as root"* \
    && "${docker_called}" == "false" ]]; then
    printf 'root guard diagnostic: status=%s docker_called=%s output=%q\n' \
      "${status}" "${docker_called}" "${output}" >&2
    return 1
  fi
}

CLONE_SCRIPT="${ROOT}/scripts/clone-deps.sh"
COMPOSE_FILE="${ROOT}/docker-compose.yml"
ENV_EXAMPLE="${ROOT}/.env.example"
START_SCRIPT="${ROOT}/scripts/start.sh"
STOP_SCRIPT="${ROOT}/scripts/stop.sh"
ENTRYPOINT="${ROOT}/docker/hermes/entrypoint-wrapper.sh"
SKILL="${ROOT}/skills/yuque-knowledge/SKILL.md"
UPTIME="${ROOT}/scripts/setup-uptime-kuma.sh"

check "dependency URL" contains "${CLONE_SCRIPT}" 'https://gitcode.com/dlut-water/yuque_mcp_server.git'
check "dependency source ref" contains "${CLONE_SCRIPT}" 'codex/docs-yuque-mcp-deployment-status'
check "dependency pinned commit" contains "${CLONE_SCRIPT}" 'cc68fd0df172d3b8f24ae325998d56bdfd0e36e6'

for variable in YUQUE_TOKEN MCP_API_KEY YUQUE_MCP_PORT YUQUE_CHANGE_RETENTION_DAYS YUQUE_MCP_UID YUQUE_MCP_GID; do
  check ".env.example declares ${variable}" contains "${ENV_EXAMPLE}" "${variable}"
done

check "yuque compose service" contains "${COMPOSE_FILE}" 'yuque-mcp:'
check "yuque profile" contains "${COMPOSE_FILE}" 'profiles: ["yuque"]'
check "localhost-only port" contains "${COMPOSE_FILE}" '127.0.0.1:${YUQUE_MCP_PORT:-18000}:18000'
check "change-data mount" contains "${COMPOSE_FILE}" '.myagentdata/yuque-mcp/change-data:/data/change-data'
check "backup mount" contains "${COMPOSE_FILE}" '.myagentdata/yuque-mcp/backups:/app/yuque/backup'
check "compose maps Yuque UID and GID" contains "${COMPOSE_FILE}" 'user: "${YUQUE_MCP_UID:-10001}:${YUQUE_MCP_GID:-10001}"'
check "compose passes snapshot permission mode" contains "${COMPOSE_FILE}" 'YUQUE_CHANGE_DATA_PERMISSION_MODE=${YUQUE_CHANGE_DATA_PERMISSION_MODE:-strict}'
check "startup supports explicit yuque" contains "${START_SCRIPT}" 'yuque-mcp'
check "Windows startup delegates snapshot permissions to host ACLs" contains "${START_SCRIPT}" 'YUQUE_CHANGE_DATA_PERMISSION_MODE=host'
check "container tests define cross-platform path conversion" contains_implementation "${BASH_SOURCE[0]}" 'docker_host_path()'
check "POSIX path conversion avoids cygpath" contains_implementation "${BASH_SOURCE[0]}" 'printf '\''%s\n'\'' "${path}"'
check "shutdown includes yuque profile" contains "${STOP_SCRIPT}" '--profile yuque'
check "POSIX startup maps current UID" contains "${START_SCRIPT}" 'YUQUE_MCP_UID="$(id -u)"'
check "POSIX startup maps current GID" contains "${START_SCRIPT}" 'YUQUE_MCP_GID="$(id -g)"'
check "POSIX root startup fails before Compose" posix_root_start_fails_closed
check "compose rejects root runtime" contains "${COMPOSE_FILE}" 'refusing to run yuque-mcp as root'
check "default startup includes Yuque" contains "${START_SCRIPT}" 'COMPOSE_SERVICES=(hermes backup-cron yuque-mcp)'
check "Hermes SSE transport" contains "${ENTRYPOINT}" 'transport: sse'
check "Hermes bearer variable" contains "${ROOT}/docker/hermes/configure-yuque-mcp.py" 'MCP_YUQUE_MCP_API_KEY'
check "Hermes config helper exists" test -f "${ROOT}/docker/hermes/configure-yuque-mcp.py"
check "yuque skill exists" test -f "${SKILL}"

for tool in list_docs get_doc_content get_repo_toc search_docs backup_repo collect_and_get_change_summary; do
  check "skill documents ${tool}" contains "${SKILL}" "${tool}"
done

check "Uptime Kuma has no yuque monitor" not_contains "${UPTIME}" 'yuque-mcp'
check "Yuque operator documentation exists" test -f "${ROOT}/docs/yuque-mcp.md"
check "documentation index links Yuque" contains "${ROOT}/docs/index.md" 'yuque-mcp.md'
check "five mock user journeys" python "${ROOT}/scripts/verify-yuque-skill-journeys.py"
check "Hermes managed lifecycle fixtures" python "${ROOT}/scripts/test-configure-yuque-mcp.py"

if [[ "${YUQUE_MCP_CONTAINER_TESTS:-0}" == "1" ]]; then
  printf 'INFO: container checks enabled\n'
  check "Docker is available" docker info >/dev/null 2>&1
  TEST_ROOT_UNIX="$(mktemp -d)"
  TEST_ROOT="$(docker_host_path "${TEST_ROOT_UNIX}")"
  TEST_HOME_UNIX="${TEST_ROOT_UNIX}/home"
  TEST_PROJECT="yuque-mcp-test-$$"
  TEST_KEY="test-$RANDOM-$RANDOM-$$"
  TEST_PORT="${YUQUE_MCP_TEST_PORT:-28080}"
  export HOME="$(docker_host_path "${TEST_HOME_UNIX}")"
  export YUQUE_TOKEN="placeholder-token"
  export MCP_API_KEY="${TEST_KEY}"
  export YUQUE_MCP_PORT="${TEST_PORT}"
  mkdir -p \
    "${TEST_HOME_UNIX}/.myagentdata/yuque-mcp/change-data" \
    "${TEST_HOME_UNIX}/.myagentdata/yuque-mcp/backups"

  cleanup_container_test() {
    docker compose -p "${TEST_PROJECT}" --profile yuque down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "${TEST_ROOT_UNIX}"
  }
  trap cleanup_container_test EXIT

  check "only yuque-mcp image builds" \
    docker compose -p "${TEST_PROJECT}" --profile yuque build yuque-mcp

  ROOT_LOG="${TEST_ROOT_UNIX}/root-runtime.log"
  if timeout 10 env HOME="${HOME}" YUQUE_MCP_UID=0 YUQUE_MCP_GID=0 \
      docker compose -p "${TEST_PROJECT}" --profile yuque run --rm --no-deps \
      yuque-mcp >"${ROOT_LOG}" 2>&1; then
    printf 'FAIL: direct root runtime fails safely\n'
    FAILURES=$((FAILURES + 1))
  elif grep -Fq 'refusing to run yuque-mcp as root' "${ROOT_LOG}"; then
    printf 'PASS: direct root runtime fails safely\n'
  else
    printf 'FAIL: direct root runtime fails safely\n'
    FAILURES=$((FAILURES + 1))
  fi

  MISSING_LOG="${TEST_ROOT_UNIX}/missing-credentials.log"
  if HOME="${HOME}" YUQUE_TOKEN= MCP_API_KEY= docker compose -p "${TEST_PROJECT}" --profile yuque \
      run --rm --no-deps yuque-mcp >"${MISSING_LOG}" 2>&1; then
    printf 'FAIL: missing credentials fail safely\n'
    FAILURES=$((FAILURES + 1))
  elif grep -Eq 'YUQUE_TOKEN is required|MCP_API_KEY is required' "${MISSING_LOG}"; then
    printf 'PASS: missing credentials fail safely\n'
  else
    printf 'FAIL: missing credentials fail safely\n'
    FAILURES=$((FAILURES + 1))
  fi

  check "yuque-mcp starts with placeholder credentials" \
    docker compose -p "${TEST_PROJECT}" --profile yuque up -d --no-deps yuque-mcp

  check "yuque-mcp runs as the configured non-root UID" \
    docker compose -p "${TEST_PROJECT}" --profile yuque exec -T \
      -e EXPECTED_UID="${YUQUE_MCP_UID:-10001}" \
      -e EXPECTED_GID="${YUQUE_MCP_GID:-10001}" \
      yuque-mcp sh -c \
      'test "$(id -u)" = "$EXPECTED_UID" && test "$(id -g)" = "$EXPECTED_GID" && test "$(id -u)" != 0'
  check "application code remains read-only to yuque-mcp" \
    docker compose -p "${TEST_PROJECT}" --profile yuque exec -T yuque-mcp \
      sh -c 'test ! -w /app/yuque/server.py && test ! -w /app/.venv'
  check "both Yuque bind mounts are writable" \
    docker compose -p "${TEST_PROJECT}" --profile yuque exec -T yuque-mcp \
      sh -c 'test -w /data/change-data && test -w /app/yuque/backup'
  check "Linux owner fixture requires mapped UID" \
    env MSYS_NO_PATHCONV=1 docker run --rm --user root --entrypoint sh \
      myopenclaw/yuque-mcp:latest -c \
      'mkdir /tmp/owner-fixture && chown 12345:12345 /tmp/owner-fixture && ! setpriv --reuid=10001 --regid=10001 --clear-groups touch /tmp/owner-fixture/fixed && setpriv --reuid=12345 --regid=12345 --clear-groups touch /tmp/owner-fixture/mapped'

  for _ in $(seq 1 30); do
    if docker compose -p "${TEST_PROJECT}" --profile yuque exec -T yuque-mcp \
        uv run python -c 'import socket; socket.create_connection(("127.0.0.1", 18000), 1).close()' \
        >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  CLIENT_SCRIPT="$(docker_host_path "${ROOT}/scripts/verify-yuque-mcp-sse.py")"
  CLIENT_BASE=(env MSYS_NO_PATHCONV=1 docker compose -p "${TEST_PROJECT}" --profile yuque run -T --rm --no-deps
    -v "${CLIENT_SCRIPT}:/tmp/verify-yuque-mcp-sse.py:ro" yuque-mcp
    uv run python /tmp/verify-yuque-mcp-sse.py --url http://yuque-mcp:18000/sse)
  check "wrong Bearer credential is rejected" \
    "${CLIENT_BASE[@]}" --key definitely-wrong --expect unauthorized
  check "correct Bearer discovers exactly six tools" \
    "${CLIENT_BASE[@]}" --key "${TEST_KEY}" --expect success

  REPO_ROOT_MOUNT="$(docker_host_path "${ROOT}")"
  check "Linux lifecycle preserves non-managed Skill paths" \
    env MSYS_NO_PATHCONV=1 docker run --rm \
      --entrypoint /opt/hermes/.venv/bin/python \
      -v "${REPO_ROOT_MOUNT}:/repo:ro" \
      myopenclaw/hermes:latest /repo/scripts/test-configure-yuque-mcp.py

  TEST_HERMES_HOME="${TEST_HOME_UNIX}/.hermes"
  mkdir -p "${TEST_HERMES_HOME}"
  HERMES_HOME="${TEST_HERMES_HOME}" \
    YUQUE_SKILL_SOURCE="${ROOT}/skills/yuque-knowledge" \
    MCP_YUQUE_MCP_API_KEY="${TEST_KEY}" \
    YUQUE_MANAGE_SKILL_LINK=false \
    python "${ROOT}/docker/hermes/configure-yuque-mcp.py"
  HERMES_CLIENT_SCRIPT="$(docker_host_path "${ROOT}/scripts/verify-hermes-yuque-mcp.py")"
  check "isolated Hermes consumes helper config and discovers six tools" \
    env MSYS_NO_PATHCONV=1 docker compose -p "${TEST_PROJECT}" --profile yuque run -T --rm --no-deps \
      --entrypoint /opt/hermes/.venv/bin/python \
      -e HOME=/opt/data \
      -v "${HERMES_CLIENT_SCRIPT}:/tmp/verify-hermes-yuque-mcp.py:ro" \
      hermes /tmp/verify-hermes-yuque-mcp.py --expect present
  HERMES_HOME="${TEST_HERMES_HOME}" \
    YUQUE_SKILL_SOURCE="${ROOT}/skills/yuque-knowledge" \
    YUQUE_MANAGE_SKILL_LINK=false \
    python "${ROOT}/docker/hermes/configure-yuque-mcp.py" --disable
  check "isolated Hermes config is absent after disable" \
    env MSYS_NO_PATHCONV=1 docker compose -p "${TEST_PROJECT}" --profile yuque run -T --rm --no-deps \
      --entrypoint /opt/hermes/.venv/bin/python \
      -e HOME=/opt/data \
      -v "${HERMES_CLIENT_SCRIPT}:/tmp/verify-hermes-yuque-mcp.py:ro" \
      hermes /tmp/verify-hermes-yuque-mcp.py --expect absent

  check "fixture data is written through mounted directories" \
    env MSYS_NO_PATHCONV=1 docker compose -p "${TEST_PROJECT}" --profile yuque exec -T yuque-mcp /bin/sh -ec \
      'printf "snapshot fixture\n" >/data/change-data/task7.fixture; printf "backup fixture\n" >/app/yuque/backup/task7.fixture'
  check "yuque-mcp container recreates" \
    docker compose -p "${TEST_PROJECT}" --profile yuque up -d --no-deps --force-recreate yuque-mcp
  check "snapshot fixture persists after recreation" \
    env MSYS_NO_PATHCONV=1 docker compose -p "${TEST_PROJECT}" --profile yuque exec -T yuque-mcp \
      test -f /data/change-data/task7.fixture
  check "backup fixture persists after recreation" \
    env MSYS_NO_PATHCONV=1 docker compose -p "${TEST_PROJECT}" --profile yuque exec -T yuque-mcp \
      test -f /app/yuque/backup/task7.fixture
else
  printf 'SKIP: container checks (set YUQUE_MCP_CONTAINER_TESTS=1)\n'
fi

if (( FAILURES > 0 )); then
  printf 'RESULT: %d contract check(s) failed\n' "${FAILURES}"
  exit 1
fi

printf 'RESULT: all contract checks passed\n'
