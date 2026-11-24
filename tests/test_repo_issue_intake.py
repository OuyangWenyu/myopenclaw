"""Guards for 道元's repo-issue-intake capability (gh/gc + allowlist).

Three layers are pinned here:

  ① the wiring: only hermes-daoyuan gets GH_CONFIG_DIR / GITCODE_TOKEN, the
     gitcode config mount, and the skill mount + start.sh symlink;
  ② the allowlist: 39 own repos, mirror rule prefers GitHub, and every entry has
     a row in references/repos.md (docs and machine list cannot drift);
  ③ the guardrails: unknown repos, near-duplicate titles and credential-looking
     bodies are refused *before* any gh/gc write.

Run: uv run --with pytest --with pyyaml pytest tests/test_repo_issue_intake.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "repo-issue-intake"
SCRIPT = SKILL_DIR / "scripts" / "file_issue.py"
ALLOWLIST = SKILL_DIR / "scripts" / "repos.json"
REPOS_MD = SKILL_DIR / "references" / "repos.md"
COMPOSE = REPO_ROOT / "docker-compose.yml"
START_SH = REPO_ROOT / "scripts" / "start.sh"


def _load_file_issue():
    spec = importlib.util.spec_from_file_location("file_issue_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


file_issue = _load_file_issue()


def _allowlist() -> list[dict]:
    return file_issue.load_allowlist()


# ── ① wiring ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


class TestContainerWiring:
    def test_daoyuan_gets_both_cli_credentials(self, compose: dict) -> None:
        env = compose["services"]["hermes-daoyuan"]["environment"]
        assert "GITCODE_TOKEN=${GITCODE_TOKEN:-}" in env
        assert "GH_CONFIG_DIR=/opt/gh-config" in env, (
            "道元需要 GH_CONFIG_DIR，因为 Hermes 会把 GH_TOKEN/GITHUB_TOKEN "
            "从 terminal 子进程剥掉（Tier-1 secret）"
        )

    def test_daoyuan_mounts_gitcode_and_skill(self, compose: dict) -> None:
        volumes = compose["services"]["hermes-daoyuan"]["volumes"]
        assert any("/.gitcode:/opt/gitcode-config" in v for v in volumes)
        assert any(
            "./skills/repo-issue-intake:/opt/hermes-skills/repo-issue-intake:ro" in v
            for v in volumes
        )

    @pytest.mark.parametrize(
        "service", ["hermes", "hermes-coder", "hermes-finance"]
    )
    def test_other_profiles_do_not_get_gh_config_dir(
        self, compose: dict, service: str
    ) -> None:
        env = compose["services"][service]["environment"]
        assert not any(e.startswith("GH_CONFIG_DIR=") for e in env), (
            "GH_CONFIG_DIR 只该给道元：先清空 /opt/data/.config/gh 的全局链接，"
            "就是为了不给爱玛士/finance 开 gh 写权限"
        )

    def test_finance_still_has_no_gitcode_token(self, compose: dict) -> None:
        env = compose["services"]["hermes-finance"]["environment"]
        assert not any(e.startswith("GITCODE_TOKEN=") for e in env)

    def test_start_sh_links_the_skill_into_daoyuan(self) -> None:
        text = START_SH.read_text(encoding="utf-8")
        assert "profiles/daoyuan/skills/repo-issue-intake" in text
        assert "/opt/hermes-skills/repo-issue-intake" in text

    def test_manifest_declares_bundle_and_mount(self) -> None:
        manifest = yaml.safe_load(
            (REPO_ROOT / "agents" / "hermes" / "daoyuan.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert "gitcode" in manifest["bundles"]
        assert "gitcode" in manifest["mounts"]
        assert "repo-issue-intake" in manifest["mounts"]


# ── ② allowlist ─────────────────────────────────────────────────────


class TestAllowlist:
    def test_mirrors_the_daily_scan_registry(self) -> None:
        data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        assert data["generated_from"].endswith("configs/repos.toml")
        assert data["count"] == len(data["repositories"]) == 39

    def test_only_own_repos(self) -> None:
        repos = _allowlist()
        github_owners = {r["owner"] for r in repos if r["platform"] == "github"}
        gitcode_owners = {r["owner"] for r in repos if r["platform"] == "gitcode"}
        assert github_owners == {"OuyangWenyu", "iHeadWater"}
        assert gitcode_owners == {"dlut-water"}

    def test_no_duplicate_slugs(self) -> None:
        seen = [(r["platform"], r["repo"]) for r in _allowlist()]
        assert len(seen) == len(set(seen))

    def test_every_allowlisted_repo_has_a_docs_row(self) -> None:
        docs = REPOS_MD.read_text(encoding="utf-8")
        missing = [r["repo"] for r in _allowlist() if f"`{r['repo']}`" not in docs]
        assert not missing, f"references/repos.md 缺少这些仓库：{missing}"

    def test_bare_name_prefers_github_upstream(self) -> None:
        repos = _allowlist()
        for name in ("myopenclaw", "hydroutils", "torchhydro"):
            chosen, mirrors = file_issue.resolve_target(name, None, repos)
            assert chosen["platform"] == "github", name
            assert {m["platform"] for m in mirrors} == {"gitcode"}, name

    def test_explicit_gitcode_mirror_is_kept_with_a_warning_target(self) -> None:
        chosen, mirrors = file_issue.resolve_target(
            "dlut-water/torchhydro", None, _allowlist()
        )
        assert chosen["platform"] == "gitcode"
        assert [m["repo"] for m in mirrors] == ["OuyangWenyu/torchhydro"]


class TestTargetResolution:
    def test_gitcode_only_repo(self) -> None:
        chosen, mirrors = file_issue.resolve_target(
            "dlut-water/zhixun-core", None, _allowlist()
        )
        assert (chosen["platform"], chosen["repo"]) == (
            "gitcode",
            "dlut-water/zhixun-core",
        )
        assert mirrors == []

    def test_case_insensitive(self) -> None:
        chosen, _ = file_issue.resolve_target(
            "iheadwater/aisecretary", None, _allowlist()
        )
        assert chosen["repo"] == "iHeadWater/aisecretary"

    def test_unknown_repo_is_refused(self) -> None:
        with pytest.raises(file_issue.Refusal) as exc:
            file_issue.resolve_target("pallets/flask", None, _allowlist())
        assert "白名单" in exc.value.reason

    def test_platform_mismatch_is_refused(self) -> None:
        with pytest.raises(file_issue.Refusal):
            file_issue.resolve_target(
                "dlut-water/zhixun-core", "github", _allowlist()
            )

    def test_bad_slug_is_refused(self) -> None:
        with pytest.raises(file_issue.Refusal):
            file_issue.resolve_target("no-such-repo", None, _allowlist())


# ── ③ guardrails ────────────────────────────────────────────────────


class TestDuplicateDetection:
    def test_punctuation_and_width_do_not_hide_a_duplicate(self) -> None:
        a = "接入图表设计能力：结构化出图走 PNG/SVG"
        b = "接入图表设计能力，结构化出图走 PNG 或 SVG"
        assert file_issue.title_similarity(a, b) >= 0.72

    def test_unrelated_titles_stay_apart(self) -> None:
        a = "接入图表设计能力：结构化出图走 PNG/SVG"
        b = "修复松辽委 DataX 同步在断网时的重试"
        assert file_issue.title_similarity(a, b) < 0.72

    def test_short_titles_only_match_exactly(self) -> None:
        assert file_issue.title_similarity("修复 bug", "修复 Bug") == 1.0
        assert file_issue.title_similarity("修复 bug", "新增功能") == 0.0

    def test_find_duplicates_reports_best_first(self) -> None:
        issues = [
            {"number": "1", "title": "接入图表设计能力：结构化出图走 PNG/SVG"},
            {"number": "2", "title": "接入图表设计能力：结构化出图走 SVG"},
            {"number": "3", "title": "完全无关的监控告警调整"},
        ]
        hits = file_issue.find_duplicates(
            issues, "接入图表设计能力：结构化出图走 PNG/SVG"
        )
        assert hits[0][1]["number"] == "1"
        assert hits[0][0] == 1.0
        assert all(issue["number"] != "3" for _, issue in hits)


class TestSecretGuard:
    def test_detects_known_token_shapes(self) -> None:
        assert file_issue.secret_hits("token ghp_" + "a" * 30)
        assert file_issue.secret_hits("gitcode_" + "b" * 20)
        assert file_issue.secret_hits("sk-" + "c" * 30)
        assert file_issue.secret_hits("-----BEGIN RSA PRIVATE KEY-----")

    def test_detects_the_actual_env_token(self) -> None:
        env = {"GH_TOKEN": "ghp_" + "z" * 36}
        hits = file_issue.secret_hits(f"oops {env['GH_TOKEN']}", env)
        assert "value-of-GH_TOKEN" in hits

    def test_clean_body_has_no_hits(self) -> None:
        assert file_issue.secret_hits("接入 STAC API 批量下载遥感影像", {}) == []


class TestBodyChecks:
    GOOD = (
        "## 来源\n- 链接：https://example.com/x\n\n"
        "## 是什么\n一个渲染器。\n\n## 建议做法\n1. 试一下。\n"
    )

    def test_good_body_passes(self) -> None:
        file_issue.check_body(self.GOOD)

    def test_missing_source_section_is_refused(self) -> None:
        with pytest.raises(file_issue.Refusal) as exc:
            file_issue.check_body("## 是什么\n没有来源。\n")
        assert "来源" in exc.value.reason

    def test_oversized_body_is_refused(self) -> None:
        with pytest.raises(file_issue.Refusal):
            file_issue.check_body(self.GOOD + "x" * 60_001)

    def test_secret_in_body_is_refused(self) -> None:
        with pytest.raises(file_issue.Refusal) as exc:
            file_issue.check_body(self.GOOD + "\nghp_" + "a" * 30)
        assert "凭据" in exc.value.reason


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Temp allowlist + body; no gh/gc network calls."""
    allow = tmp_path / "repos.json"
    allow.write_text(
        json.dumps(
            {
                "generated_from": "test",
                "count": 2,
                "repositories": [
                    {
                        "platform": "github",
                        "owner": "iHeadWater",
                        "name": "aisecretary",
                        "repo": "iHeadWater/aisecretary",
                    },
                    {
                        "platform": "gitcode",
                        "owner": "dlut-water",
                        "name": "zhixun-core",
                        "repo": "dlut-water/zhixun-core",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    body = tmp_path / "body.md"
    body.write_text(
        "## 来源\n- 链接：https://example.com\n\n## 是什么\nx\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(file_issue, "ALLOWLIST_PATH", allow)
    monkeypatch.setattr(file_issue, "require_tools", lambda platform: None)
    monkeypatch.setattr(file_issue, "list_issues", lambda *a, **k: [])
    return body


class TestMainDryRun:
    def test_dry_run_passes(self, sandbox: Path, capsys) -> None:
        code = file_issue.main(
            [
                "--repo",
                "iHeadWater/aisecretary",
                "--title",
                "接入 STAC API 批量下载遥感影像",
                "--body-file",
                str(sandbox),
                "--dry-run",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "DRY_RUN=ok" in out
        assert "TARGET=github iHeadWater/aisecretary" in out

    def test_unknown_repo_exits_1(self, sandbox: Path, capsys) -> None:
        code = file_issue.main(
            [
                "--repo",
                "pallets/flask",
                "--title",
                "随便",
                "--body-file",
                str(sandbox),
                "--dry-run",
            ]
        )
        assert code == 1
        assert "REFUSED" in capsys.readouterr().err

    def test_duplicate_exits_1(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setattr(
            file_issue,
            "list_issues",
            lambda *a, **k: [
                {
                    "number": "7",
                    "title": "接入 STAC API 批量下载遥感影像",
                    "url": "https://github.com/iHeadWater/aisecretary/issues/7",
                    "state": "open",
                }
            ],
        )
        code = file_issue.main(
            [
                "--repo",
                "iHeadWater/aisecretary",
                "--title",
                "接入 STAC API 批量下载遥感影像",
                "--body-file",
                str(sandbox),
                "--dry-run",
            ]
        )
        assert code == 1
        assert "已有近似" in capsys.readouterr().err


class TestCreatePath:
    """The POST path is only exercised here: live runs stop at --dry-run."""

    def _github_view(self, title: str = "接入 STAC API 批量下载遥感影像") -> str:
        return json.dumps(
            {
                "number": 42,
                "url": "https://github.com/iHeadWater/aisecretary/issues/42",
                "state": "OPEN",
                "title": title,
            }
        )

    def test_github_create_and_read_back(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str]) -> str:
            calls.append(cmd)
            if cmd[:3] == ["gh", "issue", "create"]:
                return "https://github.com/iHeadWater/aisecretary/issues/42\n"
            if cmd[:3] == ["gh", "issue", "view"]:
                return self._github_view()
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(file_issue, "run", fake_run)
        code = file_issue.main(
            [
                "--repo",
                "iHeadWater/aisecretary",
                "--title",
                "接入 STAC API 批量下载遥感影像",
                "--body-file",
                str(sandbox),
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "ISSUE_URL=https://github.com/iHeadWater/aisecretary/issues/42" in out
        create = next(c for c in calls if c[:3] == ["gh", "issue", "create"])
        assert "--repo" in create and "--title" in create and "--body-file" in create

    def test_gitcode_create_parses_prose_output(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str]) -> str:
            calls.append(cmd)
            if cmd[:3] == ["gc", "issue", "create"]:
                return "✓ Created issue #7\n"
            if cmd[:3] == ["gc", "issue", "view"]:
                return json.dumps(
                    {
                        "number": "7",
                        "html_url": "https://gitcode.com/dlut-water/zhixun-core/issues/7",
                        "state": "open",
                        "title": "为预报结果增加不确定性区间",
                    }
                )
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(file_issue, "run", fake_run)
        code = file_issue.main(
            [
                "--repo",
                "dlut-water/zhixun-core",
                "--title",
                "为预报结果增加不确定性区间",
                "--body-file",
                str(sandbox),
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "ISSUE_URL=https://gitcode.com/dlut-water/zhixun-core/issues/7" in out
        create = next(c for c in calls if c[:3] == ["gc", "issue", "create"])
        assert "-R" in create and "-t" in create and "-F" in create

    def test_mismatched_read_back_never_reports_a_wrong_link(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        def fake_run(cmd: list[str]) -> str:
            if cmd[:3] == ["gh", "issue", "create"]:
                return "https://github.com/iHeadWater/aisecretary/issues/42\n"
            if cmd[:3] == ["gh", "issue", "view"]:
                return self._github_view(title="完全不同的标题")
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(file_issue, "run", fake_run)
        code = file_issue.main(
            [
                "--repo",
                "iHeadWater/aisecretary",
                "--title",
                "接入 STAC API 批量下载遥感影像",
                "--body-file",
                str(sandbox),
            ]
        )
        err = capsys.readouterr().err
        assert code == 2
        assert "不要重复创建" in err


class TestSkillDocs:
    def test_skill_states_the_third_party_ban(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        assert "第三方仓库一律不建 issue" in text
        assert "file_issue.py" in text
        assert "## 来源" in text

    def test_no_tokens_are_committed(self) -> None:
        for path in SKILL_DIR.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert not file_issue.secret_hits(text, {}), (
                    f"{path} 里疑似有凭据"
                )