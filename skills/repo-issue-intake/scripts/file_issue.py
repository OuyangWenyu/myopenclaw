#!/usr/bin/env python3
"""File ONE intake issue into an allowlisted own repository (GitHub or GitCode).

The skill decides *what* to say and *which* repo it belongs to. This script owns
the guardrails that must not depend on model judgement:

  1. the target repo is in ``repos.json`` (our own repos only),
  2. the body carries a ``## 来源`` section (traceability),
  3. the body/title contains no credential-looking string,
  4. no open/closed issue in that repo already has a near-identical title,
  5. the created issue is read back and its URL is printed.

Exit codes: 0 ok (created, or --dry-run passed every guard), 1 refused by a
guard, 2 execution error (tool missing, auth failure, API failure).

Usage:
    python3 file_issue.py --list-targets
    python3 file_issue.py --repo iHeadWater/aisecretary --title "..." \
        --body-file /tmp/body.md --dry-run
    python3 file_issue.py --repo dlut-water/zhixun-core --title "..." \
        --body-file /tmp/body.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ALLOWLIST_PATH = SKILL_DIR / "scripts" / "repos.json"

DUP_THRESHOLD = 0.72
MAX_TITLE_CHARS = 120
MAX_BODY_BYTES = 60_000
RECENT_ISSUES = 60
RUN_TIMEOUT = 120

_PUNCT_RE = re.compile(
    r"[\s\u3000\-—–_:：,，.。;；!！?？/\\|()（）\[\]【】\"'“”‘’<>《》·、]+"
)
_FILLER_RE = re.compile(r"^(建议|提案|设想|需求|想法|feature|feat|idea)[:：\s]*")
_SECRET_PATTERNS = (
    ("github-classic-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-fine-grained-token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("gitcode-token", re.compile(r"gitcode_[A-Za-z0-9]{10,}")),
    ("llm-api-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
_SECRET_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GITCODE_TOKEN")
_SOURCE_HEADING_RE = re.compile(r"^##\s*来源\s*$", re.MULTILINE)


class Refusal(Exception):
    """A guardrail refused the write. ``detail`` is safe to print."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class ExecError(Exception):
    """A CLI call failed (auth, network, missing binary)."""


# ── allowlist ────────────────────────────────────────────────────────


def load_allowlist(path: Path | None = None) -> list[dict]:
    path = path or ALLOWLIST_PATH
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    repos = data["repositories"]
    for entry in repos:
        for key in ("platform", "owner", "name", "repo"):
            if not entry.get(key):
                raise ExecError(f"allowlist entry missing {key}: {entry!r}")
        if entry["platform"] not in ("github", "gitcode"):
            raise ExecError(f"unknown platform in allowlist: {entry['platform']!r}")
        if entry["repo"] != f"{entry['owner']}/{entry['name']}":
            raise ExecError(f"inconsistent repo field: {entry!r}")
    return repos


def resolve_target(
    repo: str, platform: str | None, allowlist: list[dict]
) -> tuple[dict, list[dict]]:
    """Return the chosen allowlist entry and same-name entries on other owners.

    ``owner/name`` pins one repo. A bare ``name`` resolves across the mirror
    pair (aisecretary / myopenclaw / hydroutils / torchhydro) and prefers
    GitHub; same-name entries that were not chosen are returned as mirrors so
    the caller can warn before writing to a GitCode copy.
    """
    wanted = repo.strip().strip("/").lower()
    if not wanted or wanted.count("/") > 1:
        raise Refusal(
            "仓库名必须是 owner/name 或唯一的 name",
            f"收到 {repo!r}；示例：iHeadWater/aisecretary",
        )
    if "/" in wanted:
        candidates = [e for e in allowlist if e["repo"].lower() == wanted]
    else:
        candidates = [e for e in allowlist if e["name"].lower() == wanted]
    if not candidates:
        raise Refusal(
            "目标仓库不在白名单",
            f"{repo} 不是我们自己的仓库。只允许 GitHub OuyangWenyu/*、"
            "iHeadWater/* 与 GitCode dlut-water/*（见 references/repos.md）。"
            "第三方仓库一律不写。",
        )
    if platform:
        filtered = [e for e in candidates if e["platform"] == platform]
        if not filtered:
            raise Refusal(
                f"该仓库不在 {platform} 白名单",
                f"{repo} 在 {platform} 上不存在或未登记；"
                f"可选平台：{', '.join(sorted({e['platform'] for e in candidates}))}",
            )
        candidates = filtered
    # Mirrors (same repo name, different owner/platform) prefer GitHub: it is the
    # upstream for aisecretary / myopenclaw / hydroutils / torchhydro.
    chosen = next(
        (e for e in candidates if e["platform"] == "github"), candidates[0]
    )
    same_platform = [e for e in candidates if e["platform"] == chosen["platform"]]
    if len(same_platform) > 1:
        raise Refusal(
            "裸仓库名有歧义",
            f"{repo} 命中 {', '.join(e['repo'] for e in same_platform)}；"
            "请用 owner/name 指定。",
        )
    mirrors = [
        e
        for e in allowlist
        if e["name"].lower() == chosen["name"].lower() and e is not chosen
    ]
    return chosen, mirrors


# ── text guards ─────────────────────────────────────────────────────


def normalize_title(title: str) -> str:
    """Lowercase, drop filler prefixes and punctuation, keep CJK/alnum only."""
    text = unicodedata.normalize("NFKC", title).lower().strip()
    text = _FILLER_RE.sub("", text)
    return _PUNCT_RE.sub("", text)


def title_similarity(a: str, b: str) -> float:
    left, right = normalize_title(a), normalize_title(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # Very short titles match spuriously; require an exact hit there.
    if min(len(left), len(right)) < 6:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def secret_hits(text: str, env: dict | None = None) -> list[str]:
    """Names of credential patterns found. Never returns the matched value."""
    env = os.environ if env is None else env
    hits = [name for name, pattern in _SECRET_PATTERNS if pattern.search(text)]
    for var in _SECRET_ENV_VARS:
        value = env.get(var, "")
        if len(value) >= 20 and value in text:
            hits.append(f"value-of-{var}")
    return hits


def check_body(body: str) -> None:
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise Refusal(
            "issue 正文过长",
            f"{len(body.encode('utf-8'))} 字节 > {MAX_BODY_BYTES}；"
            "请压缩成「是什么 / 为什么 / 建议 / 验收」。",
        )
    if not _SOURCE_HEADING_RE.search(body):
        raise Refusal(
            "正文缺少 `## 来源` 段",
            "每条 intake issue 必须能追溯来源（链接，或「对话：用户口述，无外部链接」）。"
            "模板见 references/issue-template.md。",
        )
    hits = secret_hits(body)
    if hits:
        raise Refusal(
            "正文疑似包含凭据",
            f"命中：{', '.join(hits)}。删掉 token/私钥后再建；issue 是公开可读的。",
        )


# ── CLI wrappers ────────────────────────────────────────────────────


def run(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT, check=False
        )
    except FileNotFoundError as exc:
        raise ExecError(f"找不到命令 {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExecError(f"{cmd[0]} 超时（{RUN_TIMEOUT}s）") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else f"exit {proc.returncode}"
        raise ExecError(f"{cmd[0]} {cmd[1]} 失败：{tail}")
    return proc.stdout


def require_tools(platform: str) -> None:
    tool = "gh" if platform == "github" else "gc"
    if shutil.which(tool) is None:
        raise ExecError(f"容器内没有 {tool}，无法写 {platform} issue")


def list_issues(platform: str, repo: str, limit: int = RECENT_ISSUES) -> list[dict]:
    if platform == "github":
        cmd = [
            "gh", "issue", "list", "--repo", repo, "--state", "all",
            "--limit", str(limit), "--json", "number,title,url,state",
        ]
    else:
        cmd = [
            "gc", "issue", "list", "-R", repo, "-s", "all", "-L", str(limit),
            "--json",
        ]
    raw = run(cmd)
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ExecError(f"{platform} 返回的不是 JSON：{raw[:120]!r}") from exc
    if isinstance(data, dict):  # defensive: some APIs wrap the list
        data = data.get("issues") or data.get("list") or []
    issues = []
    for item in data:
        issues.append(
            {
                "number": str(item.get("number") or item.get("iid") or ""),
                "title": item.get("title") or "",
                "url": item.get("html_url") or item.get("url") or "",
                "state": item.get("state") or "",
            }
        )
    return issues


def find_duplicates(
    issues: list[dict], title: str, threshold: float = DUP_THRESHOLD
) -> list[tuple[float, dict]]:
    scored = []
    for issue in issues:
        score = title_similarity(title, issue["title"])
        if score >= threshold:
            scored.append((score, issue))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:3]


def create_issue(
    platform: str, repo: str, title: str, body_path: Path
) -> str:
    if platform == "github":
        cmd = [
            "gh", "issue", "create", "--repo", repo, "--title", title,
            "--body-file", str(body_path),
        ]
    else:
        cmd = [
            "gc", "issue", "create", "-R", repo, "-t", title,
            "-F", str(body_path),
        ]
    return run(cmd)


def parse_issue_number(output: str) -> str | None:
    match = re.search(r"/issues/(\d+)", output)
    if match:
        return match.group(1)
    match = re.search(r"#\s*(\d+)", output)
    return match.group(1) if match else None


def find_number_by_title(platform: str, repo: str, title: str) -> str | None:
    """Last resort when create output carries no number (GitCode prints prose)."""
    for issue in list_issues(platform, repo, limit=10):
        if issue["title"].strip() == title.strip():
            return issue["number"]
    return None


def view_issue(platform: str, repo: str, number: str) -> dict:
    if platform == "github":
        cmd = [
            "gh", "issue", "view", number, "--repo", repo,
            "--json", "number,url,state,title",
        ]
    else:
        cmd = ["gc", "issue", "view", number, "-R", repo, "--json"]
    data = json.loads(run(cmd) or "{}")
    return {
        "number": str(data.get("number") or number),
        "url": data.get("html_url") or data.get("url") or "",
        "state": data.get("state") or "",
        "title": data.get("title") or "",
    }


# ── entry point ─────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="File one intake issue into an allowlisted own repo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", help="owner/name (must be in the allowlist)")
    parser.add_argument("--title", help=f"issue title, <= {MAX_TITLE_CHARS} chars")
    parser.add_argument("--body-file", help="path to the issue body (markdown)")
    parser.add_argument(
        "--platform", choices=("github", "gitcode"),
        help="pin the platform; bare mirror names default to GitHub",
    )
    parser.add_argument("--dry-run", action="store_true", help="check, do not create")
    parser.add_argument(
        "--force", action="store_true",
        help="create despite a near-duplicate title (never bypasses the allowlist)",
    )
    parser.add_argument(
        "--list-targets", action="store_true", help="print the allowlist and exit"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        allowlist = load_allowlist()
        if args.list_targets:
            for entry in allowlist:
                print(f"{entry['platform']:8s} {entry['repo']}")
            print(f"TOTAL={len(allowlist)}")
            return 0

        if not args.repo or not args.title or not args.body_file:
            raise Refusal(
                "缺少参数",
                "需要 --repo、--title、--body-file（或用 --list-targets）。",
            )

        target, mirrors = resolve_target(args.repo, args.platform, allowlist)
        platform, repo = target["platform"], target["repo"]

        title = args.title.strip()
        if not title:
            raise Refusal("标题为空")
        if len(title) > MAX_TITLE_CHARS:
            raise Refusal(
                "标题过长",
                f"{len(title)} 字 > {MAX_TITLE_CHARS}；标题只写「做什么」，细节放正文。",
            )

        body_path = Path(args.body_file)
        if not body_path.is_file():
            raise Refusal("正文文件不存在", str(body_path))
        body = body_path.read_text(encoding="utf-8")
        check_body(body)
        if hits := secret_hits(title):
            raise Refusal("标题疑似包含凭据", f"命中：{', '.join(hits)}")

        require_tools(platform)
        issues = list_issues(platform, repo)
        duplicates = find_duplicates(issues, title)
        print(f"TARGET={platform} {repo}")
        if mirrors:
            print(
                "MIRROR="
                + ", ".join(f"{m['platform']} {m['repo']}" for m in mirrors)
            )
            if platform == "gitcode" and any(
                m["platform"] == "github" for m in mirrors
            ):
                print(
                    "WARN=GitCode 副本是 GitHub 的镜像；上游改动通常写 GitHub。"
                    "确认要写 GitCode 才继续。"
                )
        print(f"DEDUPE=checked {len(issues)} issues")
        if duplicates and not args.force:
            print("DUPLICATES=")
            for score, issue in duplicates:
                print(
                    f"  {score:.2f} #{issue['number']} [{issue['state']}] "
                    f"{issue['url']} {issue['title']}"
                )
            raise Refusal(
                "已有近似 issue，未创建",
                "先看上面的链接：能追加信息就评论，确实不同再 --force 新建。",
            )
        if duplicates:
            print("DUPLICATES=ignored (--force)")

        if args.dry_run:
            print(f"TITLE={title}")
            print(f"BODY_BYTES={len(body.encode('utf-8'))}")
            print("DRY_RUN=ok (no issue created)")
            return 0

        output = create_issue(platform, repo, title, body_path)
        number = parse_issue_number(output) or find_number_by_title(
            platform, repo, title
        )
        if not number:
            raise ExecError(
                "issue 可能已创建，但输出里没有编号；"
                "请先到仓库确认再重试，避免重复。原始输出："
                + " ".join(output.split())[:200]
            )
        created = view_issue(platform, repo, number)
        if created["title"].strip() != title.strip():
            # The parsed number may have come from prose that mentioned another
            # issue. Re-resolve by exact title before reporting a link.
            alt = find_number_by_title(platform, repo, title)
            if alt and alt != number:
                number = alt
                created = view_issue(platform, repo, number)
        if created["title"].strip() != title.strip() or not created["url"]:
            raise ExecError(
                "issue 可能已创建，但回读的标题/链接对不上；"
                "请先到仓库确认，不要重复创建。"
            )
        print(f"CREATED={platform} {repo}#{created['number']} state={created['state']}")
        print(f"ISSUE_URL={created['url']}")
        return 0
    except Refusal as exc:
        print(f"REFUSED={exc.reason}", file=sys.stderr)
        if exc.detail:
            print(f"  {exc.detail}", file=sys.stderr)
        return 1
    except ExecError as exc:
        print(f"ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())