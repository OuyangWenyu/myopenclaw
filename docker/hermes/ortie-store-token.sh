#!/usr/bin/env bash
# Write an OAuth token from stdin to a file owned by the hermes user.
#
# docker compose exec runs as root by default. Without this helper, ortie
# would create a root:root mode-0600 token that the hermes agent cannot
# read through storage.read.command. Root can still read the hermes-owned
# file.
#
# Args:
#   $1: Destination token file path.
set -euo pipefail
umask 077

token_file="${1:?token file path required}"
mkdir -p "$(dirname "${token_file}")"
cat > "${token_file}"
chmod 600 "${token_file}"
if id -u hermes >/dev/null 2>&1; then
  chown hermes:hermes "${token_file}" 2>/dev/null || true
fi
