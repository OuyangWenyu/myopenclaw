"""Static guards for cross-file consistency between compose / env template / entrypoint.

Run: uv run --with pytest pytest tests/test_repo_consistency.py -v

These assert facts that are easy to break silently: a variable read but never
documented, an entrypoint that installs something nothing uses, a cron job
pointing at a script the image never copies. Each one was a real defect.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text()
CLAUDE_ENTRYPOINT = (REPO_ROOT / "docker" / "claude-code" / "entrypoint.sh").read_text()
CLAUDE_DOCKERFILE = (REPO_ROOT / "docker" / "claude-code" / "Dockerfile").read_text()


class TestLarkVarsDocumented:
    """A var passed into containers but absent from .env.example is undiscoverable.

    Trigger: LARK_CHAT_ID was passed to four hermes services and set in the live
    deployment while the template only documented its sibling LARK_USER_OPEN_ID.
    Auditing it showed no consumer anywhere (code, skills, memories, cron — and
    the configured value itself was never referenced), so it was deleted from
    compose rather than documented. This guard keeps the next such var honest:
    either wire it up and document it, or don't pass it in.
    """

    def test_every_lark_var_in_compose_is_documented(self):
        used = set(re.findall(r"\$\{(LARK_[A-Z0-9_]+)[:\-}]", COMPOSE))
        assert used, "compose 里应当存在 LARK_* 变量，正则可能失效了"
        undocumented = sorted(v for v in used if v not in ENV_EXAMPLE)
        assert not undocumented, (
            f"docker-compose.yml 读取但 .env.example 未文档化的变量: {undocumented}"
        )


class TestNoDoomedInstall:
    """The dailyinfo pip install block could never succeed and was fully silenced.

    Ubuntu 24.04 marks system Python externally-managed (PEP 668), so
    `pip install -e` always failed; `2>/dev/null ... || true` swallowed it, leaving
    only the "installing" line in the logs forever. Nothing in the container
    imports dailyinfo — the one script that needs it shells out via `uv run`
    against the mounted source tree.
    """

    def test_entrypoint_does_not_pip_install_dailyinfo(self):
        assert "pip install -e /home/node/code/dailyinfo" not in CLAUDE_ENTRYPOINT
        assert "安装 dailyinfo" not in CLAUDE_ENTRYPOINT


class TestCronScriptsAreInImage:
    """A cron job whose script was never COPYed fails every time it fires.

    entrypoint.sh registers `--exec "bash /opt/claude-code/weekly-ai-news-generate.sh"`
    but the Dockerfile only copied entrypoint.sh into /opt/claude-code/, so the
    Sunday 08:00 AI News job ran against a nonexistent path.
    """

    def test_every_exec_script_is_copied(self):
        referenced = re.findall(r'--exec\s+"bash\s+(/opt/[^"]+)"', CLAUDE_ENTRYPOINT)
        assert referenced, "entrypoint 里应当存在 --exec \"bash /opt/...\"，正则可能失效了"
        for path in referenced:
            name = Path(path).name
            assert name in CLAUDE_DOCKERFILE, (
                f"entrypoint 注册的 --exec 脚本 {path} 没有被 Dockerfile COPY 进镜像"
            )
