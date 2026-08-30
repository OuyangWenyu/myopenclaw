"""Tests for Outlook OAuth config generation (ortie + himalaya).

Run: uv run --with pytest pytest tests/test_outlook_email_config.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGURE_SCRIPT = REPO_ROOT / "docker" / "hermes" / "configure-outlook.sh"
BACKUP_SCRIPT = REPO_ROOT / "hermes" / "scripts" / "backup.sh"
THUNDERBIRD_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"


def _run_configure(
    hermes_data: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run configure-outlook.sh with HERMES_DATA pointed at a temp tree."""
    merged = os.environ.copy()
    merged.update(env)
    merged["HERMES_DATA"] = str(hermes_data)
    return subprocess.run(
        ["bash", str(CONFIGURE_SCRIPT)],
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_toml(path: Path) -> dict:
    """Parse a generated TOML file."""
    return tomllib.loads(path.read_text())


class TestMissingAddress:
    """Script is a no-op when EMAIL_OUTLOOK_ADDRESS is unset."""

    def test_exit_zero_without_address(self, tmp_path: Path):
        result = _run_configure(tmp_path, {})
        assert result.returncode == 0
        assert not (tmp_path / ".config" / "ortie" / "config.toml").exists()
        assert not (tmp_path / ".config" / "himalaya" / "config.toml").exists()


class TestDeviceGrantDefaults:
    """Default device-grant Outlook account on a fresh Hermes data dir."""

    @pytest.fixture
    def generated(self, tmp_path: Path) -> Path:
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_DISPLAY_NAME": "Owen",
            },
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return tmp_path

    def test_ortie_config_device_grant(self, generated: Path):
        cfg = _load_toml(generated / ".config" / "ortie" / "config.toml")
        acct = cfg["accounts"]["outlook"]
        assert acct["client-id"] == THUNDERBIRD_CLIENT_ID
        assert acct["grant"] == "device"
        assert acct["auto-refresh"] is True
        assert "offline_access" in acct["scopes"]
        assert "https://outlook.office.com/IMAP.AccessAsUser.All" in acct["scopes"]
        assert "https://outlook.office.com/SMTP.Send" in acct["scopes"]
        endpoints = acct["endpoints"]
        assert endpoints["token"].endswith("/oauth2/v2.0/token")
        assert endpoints["device-authorization"].endswith("/oauth2/v2.0/devicecode")
        token_file = str(generated / ".config" / "ortie" / "tokens" / "outlook.json")
        assert acct["storage"]["read"]["command"] == ["cat", token_file]
        write_cmd = acct["storage"]["write"]["command"]
        assert write_cmd[0].endswith("ortie-store-token.sh")
        assert write_cmd[1] == token_file

    def test_himalaya_outlook_is_default_when_alone(self, generated: Path):
        cfg = _load_toml(generated / ".config" / "himalaya" / "config.toml")
        acct = cfg["accounts"]["outlook"]
        assert acct["default"] is True
        assert acct["email"] == "owen@outlook.com"
        assert acct["display-name"] == "Owen"
        backend = acct["backend"]
        assert backend["type"] == "imap"
        assert backend["host"] == "outlook.office365.com"
        assert backend["port"] == 993
        assert backend["auth"]["type"] == "oauth2"
        assert backend["auth"]["method"] == "xoauth2"
        assert backend["auth"]["client-id"] == THUNDERBIRD_CLIENT_ID
        token_cmd = backend["auth"]["access-token"]["cmd"]
        assert "ortie" in token_cmd
        assert "-a outlook" in token_cmd
        # himalaya v1.2.0 requires these for deserializing oauth2 auth config
        # (even when only access-token.cmd is used) — verified empirically:
        # missing any of them fails the WHOLE config parse, QQ/DLUT included.
        smtp = acct["message"]["send"]["backend"]
        assert smtp["type"] == "smtp"
        assert smtp["host"] == "smtp.office365.com"
        assert smtp["port"] == 587
        assert smtp["encryption"]["type"] == "start-tls"
        assert smtp["auth"]["method"] == "xoauth2"
        for side_auth in (backend["auth"], smtp["auth"]):
            assert side_auth["auth-url"] == (
                "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
            )
            assert side_auth["token-url"] == (
                "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            )
            assert side_auth["pkce"] is False
            assert "https://outlook.office.com/IMAP.AccessAsUser.All" in side_auth["scopes"]

    def test_warns_when_token_missing(self, tmp_path: Path):
        """Fresh generation without a token must print the auth hint."""
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"},
        )
        assert result.returncode == 0, result.stderr + result.stdout
        token = tmp_path / ".config" / "ortie" / "tokens" / "outlook.json"
        assert not token.exists() or token.stat().st_size == 0
        assert "尚未授权" in result.stdout

    def test_token_dir_is_private(self, generated: Path):
        token_dir = generated / ".config" / "ortie" / "tokens"
        mode = token_dir.stat().st_mode & 0o777
        assert mode == 0o700


class TestAppendToExistingHimalaya:
    """Outlook is an extra account; QQ stays the default."""

    def test_outlook_is_not_default(self, tmp_path: Path):
        himalaya = tmp_path / ".config" / "himalaya" / "config.toml"
        himalaya.parent.mkdir(parents=True)
        himalaya.write_text("""[accounts.default]
email = "user@qq.com"
default = true
backend.type = "imap"
""")
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"},
        )
        assert result.returncode == 0, result.stderr
        cfg = _load_toml(himalaya)
        assert cfg["accounts"]["default"]["default"] is True
        assert cfg["accounts"]["outlook"]["default"] is False
        assert cfg["accounts"]["outlook"]["email"] == "owen@outlook.com"

    def test_existing_qq_section_untouched(self, tmp_path: Path):
        """Appending Outlook must not modify existing password accounts."""
        himalaya = tmp_path / ".config" / "himalaya" / "config.toml"
        himalaya.parent.mkdir(parents=True)
        original = """[accounts.default]
email = "user@qq.com"
default = true
backend.type = "imap"
"""
        himalaya.write_text(original)
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"},
        )
        assert result.returncode == 0, result.stderr
        assert himalaya.read_text().startswith(original)


class TestIdempotency:
    """A second run must not duplicate account sections."""

    def test_rerun_does_not_duplicate(self, tmp_path: Path):
        env = {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"}
        first = _run_configure(tmp_path, env)
        second = _run_configure(tmp_path, env)
        assert first.returncode == 0
        assert second.returncode == 0
        assert "already present" in second.stdout
        himalaya = (tmp_path / ".config" / "himalaya" / "config.toml").read_text()
        assert himalaya.count("[accounts.outlook]") == 1
        ortie = (tmp_path / ".config" / "ortie" / "config.toml").read_text()
        assert ortie.count("[accounts.outlook]") == 1


class TestAuthorizationCodeGrant:
    """authorization-code grant writes PKCE + loopback redirect."""

    def test_grant_block(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_GRANT": "authorization-code",
            },
        )
        assert result.returncode == 0, result.stderr
        cfg = _load_toml(tmp_path / ".config" / "ortie" / "config.toml")
        acct = cfg["accounts"]["outlook"]
        assert acct["grant"] == "authorization-code"
        assert acct["pkce"] is True
        assert acct["endpoints"]["redirection"] == "https://localhost"
        assert "device-authorization" not in acct["endpoints"]


class TestValidation:
    """Reject unsafe account names and unknown grants."""

    def test_rejects_bad_account_name(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_ACCOUNT_NAME": "foo bar",
            },
        )
        assert result.returncode != 0

    def test_rejects_unknown_grant(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_GRANT": "client-credentials",
            },
        )
        assert result.returncode != 0

    def test_custom_account_name(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@contoso.com",
                "EMAIL_OUTLOOK_ACCOUNT_NAME": "ms365",
            },
        )
        assert result.returncode == 0, result.stderr
        himalaya = _load_toml(tmp_path / ".config" / "himalaya" / "config.toml")
        ortie = _load_toml(tmp_path / ".config" / "ortie" / "config.toml")
        assert "ms365" in himalaya["accounts"]
        assert "ms365" in ortie["accounts"]
        cmd = himalaya["accounts"]["ms365"]["backend"]["auth"]["access-token"]["cmd"]
        assert "-a ms365" in cmd


class TestCollision:
    """Refuse to overwrite a foreign himalaya/ortie account with the same name."""

    def test_rejects_existing_password_account(self, tmp_path: Path):
        himalaya = tmp_path / ".config" / "himalaya" / "config.toml"
        himalaya.parent.mkdir(parents=True)
        himalaya.write_text("""[accounts.dlut]
email = "user@dlut.edu.cn"
default = false
backend.type = "imap"
backend.auth.type = "password"
""")
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_ACCOUNT_NAME": "dlut",
            },
        )
        assert result.returncode != 0
        assert "collision" in result.stderr
        cfg = _load_toml(himalaya)
        assert cfg["accounts"]["dlut"]["email"] == "user@dlut.edu.cn"
        assert "auth" in cfg["accounts"]["dlut"]["backend"]
        assert cfg["accounts"]["dlut"]["backend"]["auth"]["type"] == "password"

    def test_rejects_same_name_different_email(self, tmp_path: Path):
        """An existing [accounts.outlook] bound to another mailbox must block."""
        himalaya = tmp_path / ".config" / "himalaya" / "config.toml"
        himalaya.parent.mkdir(parents=True)
        himalaya.write_text("""[accounts.outlook]
email = "someone.else@outlook.com"
default = false
backend.type = "imap"
backend.auth.type = "password"
""")
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"},
        )
        assert result.returncode != 0
        assert "collision" in result.stderr
        cfg = _load_toml(himalaya)
        assert cfg["accounts"]["outlook"]["email"] == "someone.else@outlook.com"


class TestTomlSafety:
    """User-controlled values must be escaped or rejected."""

    def test_escapes_quotes_in_display_name(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_DISPLAY_NAME": 'Owen "Wenyu"',
            },
        )
        assert result.returncode == 0, result.stderr
        cfg = _load_toml(tmp_path / ".config" / "himalaya" / "config.toml")
        assert cfg["accounts"]["outlook"]["display-name"] == 'Owen "Wenyu"'

    def test_rejects_newline_in_address(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com\nextra"},
        )
        assert result.returncode != 0

    def test_rejects_invalid_port(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_IMAP_PORT": "not-a-port",
            },
        )
        assert result.returncode != 0

    def test_rejects_invalid_host(self, tmp_path: Path):
        result = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_IMAP_HOST": 'evil.com"; ignored = true',
            },
        )
        assert result.returncode != 0


class TestConcurrency:
    """Parallel Hermes profile startups must not duplicate TOML sections."""

    def test_parallel_runs_write_one_section(self, tmp_path: Path):
        env = {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"}
        procs = [
            subprocess.Popen(
                ["bash", str(CONFIGURE_SCRIPT)],
                env={**os.environ, **env, "HERMES_DATA": str(tmp_path)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        codes = [p.wait() for p in procs]
        assert codes == [0] * 8, [p.stderr.read() for p in procs]
        himalaya = (tmp_path / ".config" / "himalaya" / "config.toml").read_text()
        ortie = (tmp_path / ".config" / "ortie" / "config.toml").read_text()
        assert himalaya.count("[accounts.outlook]") == 1
        assert ortie.count("[accounts.outlook]") == 1


class TestTokenStoreHelper:
    """Root-created tokens must become readable by hermes when that user exists."""

    def test_writes_token_file_mode_600(self, tmp_path: Path):
        helper = REPO_ROOT / "docker" / "hermes" / "ortie-store-token.sh"
        dest = tmp_path / "outlook.json"
        result = subprocess.run(
            ["bash", str(helper), str(dest)],
            input="access-token-value\n",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert dest.read_text() == "access-token-value\n"
        assert dest.stat().st_mode & 0o777 == 0o600


class TestEmailEnvLoading:
    """load-email-env.sh must load EMAIL_* AND EMAIL2_* (and EMAIL3+...).

    回归：wrapper 用 `grep -E '^EMAIL_'` 过滤，EMAIL2_*（第 6 字符是 2）
    从不被加载——dlut 账户是旧版本脚本生成的残存配置。
    """

    SCRIPT = REPO_ROOT / "docker" / "hermes" / "load-email-env.sh"

    def _load(self, env_lines: list[str], preset: dict[str, str] | None = None):
        """Source the script, load env_lines from a fixture file, report state."""
        env_file = None
        import shlex
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("\n".join(env_lines) + "\n")
            env_file = f.name
        preset_src = "".join(
            f"export {k}={shlex.quote(v)}\n" for k, v in (preset or {}).items()
        )
        proc = subprocess.run(
            [
                "bash",
                "-c",
                f"""
set -euo pipefail
{preset_src}
source {shlex.quote(str(self.SCRIPT))}
load_email_env {shlex.quote(env_file)}
echo SHELLVARS-BEGIN
for v in $(compgen -A variable | grep -E '^EMAIL' | sort); do
  echo "$v=${{!v}}"
done
echo SHELLVARS-END
echo EXPORTED-BEGIN
env | grep -oE '^EMAIL[A-Z0-9_]*' | sort || true
echo EXPORTED-END
""",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout
        shell_vars = {}
        in_block = False
        exported: set[str] = set()
        for line in out.splitlines():
            if line == "SHELLVARS-BEGIN":
                in_block = True
            elif line == "SHELLVARS-END":
                in_block = False
            elif line == "EXPORTED-BEGIN":
                in_block = "exported"
            elif line == "EXPORTED-END":
                in_block = False
            elif in_block is True and "=" in line:
                k, _, v = line.partition("=")
                shell_vars[k] = v
            elif in_block == "exported":
                exported.add(line)
        return shell_vars, exported

    ENV_LINES = [
        "# EMAIL_ADDRESS=user@qq.com",
        "# EMAIL_PASSWORD=qqpass",
        "# EMAIL_IMAP_HOST=imap.qq.com",
        "# EMAIL2_ADDRESS=user@dlut.edu.cn",
        "# EMAIL2_PASSWORD=dlutpass",
        "# EMAIL2_IMAP_HOST=mail.dlut.edu.cn",
        "# EMAIL_OUTLOOK_ADDRESS=from-file@outlook.com",
    ]

    def test_email2_family_is_loaded(self):
        vars_, _ = self._load(self.ENV_LINES)
        assert vars_["EMAIL2_PASSWORD"] == "dlutpass"
        assert vars_["EMAIL2_ADDRESS"] == "user@dlut.edu.cn"

    def test_commented_lines_are_loaded(self):
        vars_, _ = self._load(self.ENV_LINES)
        assert vars_["EMAIL_PASSWORD"] == "qqpass"

    def test_compose_env_wins_over_file(self):
        vars_, _ = self._load(
            self.ENV_LINES,
            preset={"EMAIL_OUTLOOK_ADDRESS": "from-compose@outlook.com"},
        )
        assert vars_["EMAIL_OUTLOOK_ADDRESS"] == "from-compose@outlook.com"

    def test_vars_stay_shell_local_never_exported(self):
        _, exported = self._load(self.ENV_LINES)
        assert not exported, f"EMAIL_* vars leaked to process env: {exported}"

    def test_values_with_spaces_load_intact(self):
        """`EMAIL2_DISPLAY_NAME=Wenyu Ouyang` — eval 把空格后的词当命令执行
        （真机报错 Ouyang: command not found），必须原样赋值。"""
        vars_, _ = self._load(
            [*self.ENV_LINES, "# EMAIL2_DISPLAY_NAME=Wenyu Ouyang"]
        )
        assert vars_["EMAIL2_DISPLAY_NAME"] == "Wenyu Ouyang"

    def test_command_substitution_in_value_is_not_executed(self, tmp_path: Path):
        """值里的 $(...) 必须作为字面量存储，绝不执行（防注入）。"""
        marker = tmp_path / "marker"
        vars_, _ = self._load(
            [f"# EMAIL_INJECT=$({marker})"]
        )
        assert vars_["EMAIL_INJECT"] == f"$({marker})"
        assert not marker.exists(), "注入的命令被执行了！"

    def test_direct_run_prints_names_only(self):
        """直接执行自测只输出变量名，不输出值（防止密码打印）。"""
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("\n".join(self.ENV_LINES) + "\n")
            env_file = f.name
        proc = subprocess.run(
            ["bash", str(self.SCRIPT), env_file],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "EMAIL2_PASSWORD" in proc.stdout
        assert "qqpass" not in proc.stdout and "dlutpass" not in proc.stdout

    def test_wrapper_sources_the_loader(self):
        wrapper = (
            REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
        ).read_text()
        assert "load-email-env.sh" in wrapper
        assert "load_email_env" in wrapper

    def test_dockerfile_ships_the_loader(self):
        dockerfile = (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text()
        assert "load-email-env.sh" in dockerfile


class TestBackupCoverage:
    """Hermes backup must copy himalaya config and ortie tokens."""

    def test_backup_script_includes_email_dirs(self):
        text = BACKUP_SCRIPT.read_text()
        assert ".config/himalaya" in text
        assert ".config/ortie" in text


class TestStaleLock:
    """A lock left by a SIGKILLed run must be reclaimed, not deadlock startup."""

    LOCK_DIR = ".config/outlook-configure.lock"

    def _backdate_lock(self, tmp_path: Path) -> Path:
        lock = tmp_path / self.LOCK_DIR
        lock.mkdir(parents=True, exist_ok=True)
        # mtime 2 minutes in the past; BSD touch has no -d, GNU touch has no -A
        if (
            subprocess.run(
                ["touch", "-d", "2 minutes ago", str(lock)],
                check=False,
                capture_output=True,
            ).returncode
            != 0
        ):
            stamp = time.strftime("%Y%m%d%H%M.%S", time.localtime(time.time() - 120))
            subprocess.run(["touch", "-t", stamp, str(lock)], check=True)
        return lock

    def test_reclaims_stale_lock(self, tmp_path: Path):
        lock = self._backdate_lock(tmp_path)
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"},
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert "过期" in result.stdout
        assert not lock.exists()

    def test_fresh_lock_times_out_and_is_preserved(self, tmp_path: Path):
        lock = tmp_path / self.LOCK_DIR
        lock.mkdir(parents=True)
        result = _run_configure(
            tmp_path,
            {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"},
        )
        assert result.returncode != 0
        assert "timed out" in result.stderr
        assert lock.exists()


class TestDriftWarning:
    """Env changes after the first run must warn, not silently rewrite."""

    def test_warns_and_keeps_old_values(self, tmp_path: Path):
        first = _run_configure(
            tmp_path, {"EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com"}
        )
        assert first.returncode == 0, first.stderr + first.stdout
        second = _run_configure(
            tmp_path,
            {
                "EMAIL_OUTLOOK_ADDRESS": "owen@outlook.com",
                "EMAIL_OUTLOOK_IMAP_HOST": "alt.office365.com",
            },
        )
        assert second.returncode == 0, second.stderr + second.stdout
        assert "不一致" in second.stdout
        himalaya_cfg = tmp_path / ".config" / "himalaya" / "config.toml"
        cfg = _load_toml(himalaya_cfg)
        assert cfg["accounts"]["outlook"]["backend"]["host"] == "outlook.office365.com"
        assert himalaya_cfg.read_text().count("[accounts.outlook]") == 1


class TestEntrypointWiring:
    """EMAIL_* must stay shell-local in the wrapper (never reach Hermes env)."""

    def test_wrapper_does_not_export_email_vars(self):
        wrapper = (
            REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
        ).read_text()
        assert "set -a" not in wrapper
        assert "set +a" not in wrapper
        loader = REPO_ROOT / "docker" / "hermes" / "load-email-env.sh"
        assert "grep -E '^EMAIL'" in loader.read_text()

    def test_wrapper_loads_env_with_compose_precedence(self):
        loader = (
            REPO_ROOT / "docker" / "hermes" / "load-email-env.sh"
        ).read_text()
        # skip-if-set loop: container env (compose) wins over ~/.hermes/.env
        assert "while IFS= read -r kv" in loader
        assert '"${!key:-}"' in loader

    def test_wrapper_gates_email_capability_by_profile(self):
        """Email is granted to the default profile only (爱玛士); others denied."""
        wrapper = (
            REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
        ).read_text()
        # profile gate: coder/finance/daoyuan set HERMES_PROFILE, default does not
        # (gate appears in: env loading, config generation, outlook invocation)
        assert wrapper.count('-z "${HERMES_PROFILE:-}"') >= 3
        # deny stubs replace both binaries in restricted profiles
        assert "邮箱访问未授权" in wrapper
        assert "/usr/local/bin/himalaya" in wrapper
        assert "/usr/local/bin/ortie" in wrapper

    def test_wrapper_folder_alias_handles_last_section(self):
        """Alias insertion must not land in the PREVIOUS account section when
        the target account is the last one (regression: duplicate-key error)."""
        wrapper = (
            REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
        ).read_text()
        # next-section lookup must compare line numbers (> own), not grep -A1
        assert "$1 > own" in wrapper

    def test_wrapper_alias_append_never_nests_quotes_in_expansion(self):
        """Quotes inside ${var:+word} heredoc expansions get eaten by bash —
        the end-of-file alias append must use explicit echo lines instead
        (regression: `folder.aliases.drafts = Drafts` failed TOML parsing)."""
        wrapper = (
            REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
        ).read_text()
        assert 'folder.aliases.drafts = "${H_DRAFTS}"}' not in wrapper


class TestComposeInjection:
    """Repo-root .env is the single config source for Outlook (non-secret vars)."""

    COMPOSE = REPO_ROOT / "docker-compose.yml"
    ENV_EXAMPLE = REPO_ROOT / ".env.example"
    OUTLOOK_VARS = [
        "EMAIL_OUTLOOK_ADDRESS",
        "EMAIL_OUTLOOK_ACCOUNT_NAME",
        "EMAIL_OUTLOOK_DISPLAY_NAME",
        "EMAIL_OUTLOOK_GRANT",
        "EMAIL_OUTLOOK_CLIENT_ID",
        "EMAIL_OUTLOOK_IMAP_HOST",
        "EMAIL_OUTLOOK_IMAP_PORT",
        "EMAIL_OUTLOOK_SMTP_HOST",
        "EMAIL_OUTLOOK_SMTP_PORT",
    ]

    @staticmethod
    def _service_blocks(compose_text: str) -> dict[str, str]:
        parts = re.split(r"\n  ([\w-]+):\n", "\n" + compose_text)
        return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}

    def test_compose_injects_outlook_env_only_for_default_hermes(self):
        """Email capability is granted to the default profile (爱玛士) only."""
        blocks = self._service_blocks(self.COMPOSE.read_text())
        assert "hermes" in blocks, "hermes service missing in compose"
        for var in self.OUTLOOK_VARS:
            assert f"- {var}=${{{var}:-}}" in blocks["hermes"], var
        for service in ("hermes-coder", "hermes-finance", "hermes-daoyuan"):
            assert "- EMAIL_OUTLOOK_" not in blocks[service], (
                f"{service} must not receive email env vars"
            )

    def test_restricted_services_shadow_email_paths_with_empty_volumes(self):
        """coder/finance/daoyuan must not see the shared email config or tokens."""
        blocks = self._service_blocks(self.COMPOSE.read_text())
        for service in ("hermes-coder", "hermes-finance", "hermes-daoyuan"):
            assert "email-config-none:/opt/data/.config/himalaya" in blocks[service], service
            assert "email-tokens-none:/opt/data/.config/ortie" in blocks[service], service
        # 爱玛士 keeps the real shared paths
        assert "email-config-none:/opt/data/.config/himalaya" not in blocks["hermes"]
        assert "email-tokens-none:/opt/data/.config/ortie" not in blocks["hermes"]

    def test_compose_declares_shadow_volumes(self):
        text = self.COMPOSE.read_text()
        assert re.search(r"^  email-config-none:", text, re.M)
        assert re.search(r"^  email-tokens-none:", text, re.M)

    def test_hermes_dashboard_not_injected(self):
        blocks = self._service_blocks(self.COMPOSE.read_text())
        assert "EMAIL_OUTLOOK_ADDRESS" not in blocks["hermes-dashboard"]

    def test_env_example_documents_outlook_vars(self):
        text = self.ENV_EXAMPLE.read_text()
        for var in (
            "EMAIL_OUTLOOK_ADDRESS",
            "EMAIL_OUTLOOK_DISPLAY_NAME",
            "EMAIL_OUTLOOK_GRANT",
            "EMAIL_OUTLOOK_CLIENT_ID",
        ):
            assert f"# {var}=" in text


class TestImageInstall:
    """Dockerfile pins a released ortie linux tarball next to himalaya."""

    def test_dockerfile_installs_ortie_binary(self):
        dockerfile = (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text()
        assert 'ORTIE_VER="2.2.0"' in dockerfile
        assert "ortie.${ARCH}-linux.tgz" in dockerfile
        assert "configure-outlook.sh" in dockerfile
        assert "ortie-store-token.sh" in dockerfile
        # ortie block must map uname directly (no identity sed)
        ortie_block = dockerfile.split("# ── Install ortie")[1]
        assert "ARCH=$(uname -m)" in ortie_block
        assert "s/x86_64/x86_64/" not in ortie_block

    def test_himalaya_built_with_oauth2_feature(self):
        """Release binaries lack the oauth2 cargo feature — must build from source."""
        dockerfile = (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text()
        assert "cargo install himalaya" in dockerfile
        assert "--features oauth2" in dockerfile
        # release-tarball install would ship without oauth2 and break config parsing
        assert "himalaya.tgz" not in dockerfile

    def test_wrapper_wires_configure_script(self):
        wrapper = (
            REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
        ).read_text()
        assert "configure-outlook.sh" in wrapper
        assert "EMAIL_OUTLOOK_ADDRESS" in wrapper
