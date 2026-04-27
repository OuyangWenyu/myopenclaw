#!/usr/bin/env bash
# =============================================================
# Hermes entrypoint wrapper — sets up tool config symlinks
# before handing off to the original Hermes entrypoint.
#
# Why: gh CLI looks for config at $HOME/.config/gh/ which is
# /opt/data/.config/gh/ inside the container (hermes home = /opt/data).
# We symlink it to /opt/gh-config so the host-mounted config
# (from ~/.config/gh) is found automatically.
# =============================================================
set -euo pipefail

# hermes user home = /opt/data; Hermes bash subprocess runs as root (HOME=/root)
# gh CLI reads $HOME/.config/gh/ — symlink both to the host-mounted config dir
mkdir -p /opt/data/.config /root/.config
ln -sf /opt/gh-config /opt/data/.config/gh
ln -sf /opt/gh-config /root/.config/gh

# Hand off to original Hermes entrypoint (handles UID mapping + gosu)
exec /opt/hermes/docker/entrypoint.sh "$@"
