#!/bin/sh
# Fix macOS host paths in OpenClaw config files that leak into the container
# via bind-mounted ~/.openclaw directory.
# The host OpenClaw CLI writes paths like /Users/owen/... but inside the
# container the home is /home/node, so those paths cause EACCES errors.

set -e

OPENCLAW_HOME="/home/node/.openclaw"

# Fix exec-approvals.json socket path
EA_FILE="${OPENCLAW_HOME}/exec-approvals.json"
if [ -f "${EA_FILE}" ]; then
  if grep -q '"/Users/' "${EA_FILE}" 2>/dev/null; then
    echo "🔧 Fixing host paths in ${EA_FILE}"
    sed 's|"/Users/[^"]*/\.openclaw|"/home/node/.openclaw|g' "${EA_FILE}" > "${EA_FILE}.tmp"
    mv "${EA_FILE}.tmp" "${EA_FILE}"
  fi
fi

echo "✅ Host path check done"
