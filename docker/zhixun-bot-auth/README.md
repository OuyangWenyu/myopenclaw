# Existing Water MCP private SSE authentication

This adapter upgrades the existing Water MCP container while retaining its business
code and compatibility adapters. It uses the authentication module from a fixed
zhixun-agent commit. It does not start another MCP instance.

Build with an immutable local base-image tag and an additional BuildKit context:

```bash
docker buildx build --load -f docker/zhixun-bot-auth/Dockerfile \
  --build-arg WATER_BASE_IMAGE=<saved-base-image-tag> \
  --build-context zhixun_src=<fixed-zhixun-agent-checkout> \
  -t <versioned-auth-image> docker/zhixun-bot-auth
```

Use docker-compose.zhixun-water-auth.yml after the current bot Compose file.
Docker Compose 2.24 or newer is required for the reset tags. This overlay explicitly
enables the authenticated local Water MCP, removes its inherited debug profile and
build configuration, and makes OpenClaw wait for its health check. Without the
overlay, the upstream remote-MCP default and local debug profile are unchanged.

Set these in a separate deployment environment file (for example, auth.env):

```dotenv
ZHIXUN_WATER_AUTH_IMAGE=water-mcp:verified-auth-version
ZHIXUN_WATER_KEYS_FILE=/srv/water-auth/keys.json
ZHIXUN_WATER_TOKEN_FILE=/srv/water-auth/openclaw-token
```

```bash
docker compose --env-file .env.zhixun-bot --env-file auth.env \
  -f docker-compose.zhixun-bot.yml -f docker-compose.zhixun-water-auth.yml \
  config --quiet
docker compose --env-file .env.zhixun-bot --env-file auth.env \
  -f docker-compose.zhixun-bot.yml -f docker-compose.zhixun-water-auth.yml \
  up -d --no-build
```

No manual profile flag is needed. Use these complete arguments for later updates;
the normal start-zhixun-bot.sh helper intentionally uses only the default Compose.
Other required bot settings remain in .env.zhixun-bot.

The overlay chooses the internal SSE URL and file credential mode explicitly,
clearing any remote env-token value. The native entrypoint reads the protected file
and exports its value only to the gateway's runtime environment. The native renderer
retains the single lowercase authorization header with its environment reference,
so the actual key never appears in rendered JSON. No renderer wrapper or extra
renderer mounts are needed. Existing env-token mode still works without this overlay.

The server secret is a nonempty JSON key-to-user map. The OpenClaw secret contains
only its dedicated key, owned by the container's runtime UID with mode 0600. Both
are read-only mounts. Never put real keys in this repository.

The overlay binds only 10.48.0.81:18201. External clients use
http://10.48.0.81:18201/sse; OpenClaw keeps
http://zhixun-water-mcp:18201/sse. Both SSE GET and message POST require a key.

Before rollout, back up the old container image ID and runtime configuration.
Validate missing/invalid keys, initialization, tools/list, a real read-only tool,
and OpenClaw's native MCP connection. Stop deployment if those checks fail.
Rotate/revoke keys by updating the server file and recreating Water MCP to refresh
the single-file bind mount; existing SSE sessions are disconnected. Preserve other
users' keys when rotating one user. When rotating OpenClaw's dedicated key, update
its token file and recreate OpenClaw as well.

To roll back, recreate Water MCP with the saved image and original Compose command,
remove the authentication overlay and restore the prior OpenClaw configuration.
The base remote-MCP deployment remains available without the optional overlay.

Regression checks: node --test tests/test-zhixun-water-auth.mjs (token validation,
renderer confidentiality, authorization headers, and real Compose merging).
Linux is needed for ownership/mode/symlink assertions. Deployment probes are
probe.py and probe-openclaw.cjs; neither sends channel messages or invokes an LLM.
