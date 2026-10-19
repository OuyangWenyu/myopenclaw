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
Set ZHIXUN_WATER_AUTH_IMAGE, ZHIXUN_WATER_KEYS_FILE, ZHIXUN_WATER_TOKEN_FILE,
ZHIXUN_WATER_AUTH_DIR and ZHIXUN_WATER_ORIGINAL_RENDERER in a protected deployment
environment file. Keep the original renderer bind-mounted: the wrapper runs it
before adding the Water MCP Authorization header, retaining its current settings.

The server secret is a nonempty JSON key-to-user map. The OpenClaw secret contains
only its dedicated key, owned by the container's runtime UID with mode 0600. Both
are read-only mounts. Never put real keys in this repository.

The overlay binds only 10.48.0.81:18201. External clients use
http://10.48.0.81:18201/sse; OpenClaw keeps
http://zhixun-water-mcp:18201/sse. Both SSE GET and message POST require a key.

Before rollout, back up the old container image ID and runtime configuration.
Validate missing/invalid keys, initialization, tools/list, a real read-only tool,
and OpenClaw's native MCP connection. Stop deployment if those checks fail.
Rotate/revoke keys by updating the server file and restarting Water MCP; existing
SSE sessions are disconnected. Preserve other users' keys when rotating one user.

To roll back, recreate Water MCP with the saved image and original Compose command,
remove the authentication overlay and restore the prior OpenClaw configuration.
The original Compose and renderer files are never overwritten by this adapter.
