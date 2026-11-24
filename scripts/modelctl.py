#!/usr/bin/env python3
"""模型源切换器 —— 全仓模型配置的唯一写者。

背景：换一次模型源，过去要手改 12 个文件（4 个 Hermes profile、Claude Code 的
settings.json + cc-connect config.toml、虾酱 openclaw.json、两个 bot 的 env、
compose 注入……），而且每处写法不同：Hermes 要裸 id + provider，OpenClaw 要
``provider/model`` 前缀 + 能力元数据，Claude Code 认 Anthropic 协议、id 后面还
可能带 ``[1M]`` 后缀。漏一处不会报错，只会让某个 agent 静默跑在旧模型上。

这个脚本把「供应商 / 模型 / 角色 / 绑定」收进 ``config/models.toml``，再由
适配器渲染到每个消费方。日常换源只改注册表 + ``apply`` 一条命令。

用法::

    python3 scripts/modelctl.py status            # 只读：期望值 vs live 值，谁在漂移
    python3 scripts/modelctl.py check             # 守卫：有漂移或缺 key 就 exit 1
    python3 scripts/modelctl.py apply --dry-run   # 打印每份文件的 diff + 重启清单
    python3 scripts/modelctl.py apply             # 幂等写入（写前自动备份）
    python3 scripts/modelctl.py probe             # 真打一次最小请求验证 key/端点/模型 id

设计约束（都是踩过的坑，别改）：

* **密钥不进注册表**。注册表只写 ``key_env``（变量名）；密钥住在仓库根 ``.env``。
  ``status`` / ``apply`` 只回显「有/无」与长度，从不打印值。
* **只改自己拥有的键**。Hermes 的 config.yaml、Claude 的 settings.json、虾酱的
  openclaw.json 都是活文件，适配器逐块改写，注释、键序、别人的段落一律保留。
* **写前备份**。每份被改的文件先落到 ``~/.cache/modelctl/<时间戳>/``。
* **不冒充运行时校验**。apply 只保证「写对了」；能不能调通要 ``probe``，端到端
  行为（多轮工具调用、思考模式回显）还得真跑一个回合。

Exit codes: 0 = ok / 已写入；1 = 漂移或检查失败；2 = 用法或注册表错误。
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import shutil
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "config" / "models.toml"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
BACKUP_ROOT = Path.home() / ".cache" / "modelctl"

MANAGED_BEGIN = "# BEGIN MODELCTL"
MANAGED_END = "# END MODELCTL"
MANAGED_TITLE = "# ── 模型源（scripts/modelctl.py 生成，勿手改）──────────────"

# 这些键的值一律不进日志/diff
SECRET_KEY_RE = re.compile(r"(API_KEY|AUTH_TOKEN|ACCESS_TOKEN|SECRET|PASSWORD)$", re.I)


# ══════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════
class RegistryError(Exception):
    """注册表本身有问题（配置错误，不是运行时故障）。"""


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    key_env: str
    hermes_provider: str
    openclaw_provider: str
    openai_url: str
    anthropic_url: str


@dataclass(frozen=True)
class Model:
    name: str
    label: str
    provider: str
    id: str
    context: int
    max_output: int
    input: tuple[str, ...]
    reasoning: bool
    claude_suffix: str = ""
    claude_max_context: int = 0


@dataclass(frozen=True)
class Role:
    name: str
    model: str
    fallback: str | None


@dataclass(frozen=True)
class Binding:
    name: str
    role: str | None
    model: str | None


@dataclass(frozen=True)
class Pinned:
    name: str
    model: str
    reason: str


@dataclass(frozen=True)
class Resolved:
    binding: str
    model: Model
    provider: Provider
    fallback: Model | None
    fallback_provider: Provider | None


_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_url(raw: str, env: dict[str, str]) -> tuple[str, set[str]]:
    """展开端点里的 ``${VAR}``；缺变量时返回空串 + 缺失的变量名。

    部署专属端点（阿里云业务空间域名）跟密钥一样属于「这台机器/这次采购」的值，
    住在 .env，不写进注册表；注册表只描述路径形状。
    """
    if not raw:
        return "", set()
    missing: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        value = (env.get(match.group(1)) or "").strip().rstrip("/")
        if not value:
            missing.add(match.group(1))
        return value

    expanded = _PLACEHOLDER.sub(_sub, raw).rstrip("/")
    return ("", missing) if missing else (expanded, set())


class Registry:
    def __init__(self, data: dict):
        self.version = int(data.get("version", 0))
        if self.version != 1:
            raise RegistryError(f"不认识的注册表版本 {self.version}（期望 1）")

        self.providers: dict[str, Provider] = {}
        for name, raw in (data.get("providers") or {}).items():
            protocols = raw.get("protocols") or {}
            if not protocols.get("openai"):
                raise RegistryError(f"provider {name}: 缺 protocols.openai")
            self.providers[name] = Provider(
                name=name,
                label=raw.get("label", name),
                key_env=raw.get("key_env", ""),
                hermes_provider=raw.get("hermes_provider", name),
                openclaw_provider=raw.get("openclaw_provider", name),
                openai_url=protocols["openai"].rstrip("/"),
                anthropic_url=(protocols.get("anthropic") or "").rstrip("/"),
            )
            if not self.providers[name].key_env:
                raise RegistryError(f"provider {name}: 缺 key_env")

        self.models: dict[str, Model] = {}
        for name, raw in (data.get("models") or {}).items():
            provider = raw.get("provider", "")
            if provider not in self.providers:
                raise RegistryError(f"model {name}: 未知 provider {provider!r}")
            for required in ("id", "context", "max_output"):
                if not raw.get(required):
                    raise RegistryError(f"model {name}: 缺 {required}")
            self.models[name] = Model(
                name=name,
                label=raw.get("label", name),
                provider=provider,
                id=str(raw["id"]),
                context=int(raw["context"]),
                max_output=int(raw["max_output"]),
                input=tuple(raw.get("input") or ("text",)),
                reasoning=bool(raw.get("reasoning", False)),
                claude_suffix=str(raw.get("claude_suffix", "")),
                claude_max_context=int(raw.get("claude_max_context", 0) or 0),
            )

        self.roles: dict[str, Role] = {}
        for name, raw in (data.get("roles") or {}).items():
            model = raw.get("model", "")
            fallback = raw.get("fallback")
            if model not in self.models:
                raise RegistryError(f"role {name}: 未知 model {model!r}")
            if fallback is not None and fallback not in self.models:
                raise RegistryError(f"role {name}: 未知 fallback {fallback!r}")
            self.roles[name] = Role(name=name, model=model, fallback=fallback)

        self.bindings: dict[str, Binding] = {}
        for name, raw in (data.get("bindings") or {}).items():
            role = raw.get("role")
            model = raw.get("model")
            if bool(role) == bool(model):
                raise RegistryError(f"binding {name}: role 与 model 必须二选一")
            if role and role not in self.roles:
                raise RegistryError(f"binding {name}: 未知 role {role!r}")
            if model and model not in self.models:
                raise RegistryError(f"binding {name}: 未知 model {model!r}")
            self.bindings[name] = Binding(name=name, role=role, model=model)

        self.pinned: dict[str, Pinned] = {}
        for name, raw in (data.get("pinned") or {}).items():
            model = raw.get("model", "")
            if model and model not in self.models:
                raise RegistryError(f"pinned {name}: 未知 model {model!r}")
            self.pinned[name] = Pinned(
                name=name, model=model, reason=raw.get("reason", "")
            )

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "Registry":
        try:
            with path.open("rb") as fh:
                return cls(tomllib.load(fh))
        except FileNotFoundError:
            raise RegistryError(f"注册表不存在: {path}") from None
        except tomllib.TOMLDecodeError as exc:
            raise RegistryError(f"注册表解析失败: {exc}") from None

    def with_env(self, env: dict[str, str]) -> tuple["Registry", list[str]]:
        """返回展开过 ``${VAR}`` 的副本 + 缺失的变量名。

        缺变量时该 provider 的端点为空 —— 调用方据此拒绝写入，而不是把半截
        URL（例如只剩 ``/compatible-mode/v1``）渲染进下游配置。
        """
        missing: set[str] = set()
        providers: dict[str, Provider] = {}
        for provider in self.providers.values():
            openai_url, miss_openai = _expand_url(provider.openai_url, env)
            anthropic_url, miss_anthropic = _expand_url(provider.anthropic_url, env)
            missing |= miss_openai | miss_anthropic
            providers[provider.name] = Provider(
                name=provider.name,
                label=provider.label,
                key_env=provider.key_env,
                hermes_provider=provider.hermes_provider,
                openclaw_provider=provider.openclaw_provider,
                openai_url=openai_url,
                anthropic_url=anthropic_url,
            )
        clone = copy.copy(self)
        clone.providers = providers
        return clone, sorted(missing)

    def resolve(self, binding: Binding) -> Resolved:
        if binding.role:
            role = self.roles[binding.role]
            model = self.models[role.model]
            fallback = self.models[role.fallback] if role.fallback else None
        else:
            model = self.models[binding.model]  # type: ignore[index]
            fallback = None

        provider = self.providers[model.provider]
        fallback_provider = (
            self.providers[fallback.provider] if fallback is not None else None
        )
        return Resolved(
            binding=binding.name,
            model=model,
            provider=provider,
            fallback=fallback,
            fallback_provider=fallback_provider,
        )

    def resolved_bindings(self) -> list[Resolved]:
        return [self.resolve(b) for b in self.bindings.values()]


# ══════════════════════════════════════════════════════════════════
# .env 读写
# ══════════════════════════════════════════════════════════════════
def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _set_dotenv_value(lines: list[str], key: str, value: str, *, anchor: str | None = None) -> bool:
    """改写 ``KEY=VALUE``；不存在时插到 ``anchor`` 之后（没有则追加）。"""
    pattern = re.compile(rf"^{re.escape(key)}[ \t]*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            new = f"{key}={value}\n"
            if line == new:
                return False
            lines[i] = new
            return True
    new_line = f"{key}={value}\n"
    if anchor:
        anchor_pat = re.compile(rf"^{re.escape(anchor)}[ \t]*=")
        for i, line in enumerate(lines):
            if anchor_pat.match(line):
                lines.insert(i + 1, new_line)
                return True
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(new_line)
    return True


def render_dotenv(text: str, updates: dict[str, str], *, anchors: dict[str, str] | None = None) -> str:
    anchors = anchors or {}
    lines = text.splitlines(keepends=True)
    for key, value in updates.items():
        _set_dotenv_value(lines, key, value, anchor=anchors.get(key))
    return "".join(lines)


def render_managed_block(text: str, entries: dict[str, str]) -> str:
    """改写 ``# BEGIN MODELCTL`` … ``# END MODELCTL`` 之间的托管块。"""
    body = [MANAGED_TITLE + "\n", MANAGED_BEGIN + "\n"]
    body += [f"{k}={v}\n" for k, v in sorted(entries.items())]
    body.append(MANAGED_END + "\n")

    lines = text.splitlines(keepends=True)
    begin = next((i for i, l in enumerate(lines) if l.strip() == MANAGED_BEGIN), None)
    end = next((i for i, l in enumerate(lines) if l.strip() == MANAGED_END), None)
    if begin is not None and end is not None and end > begin:
        # 连同上一行的标题注释一起替换（标题由本函数重写）
        head = begin
        if head > 0 and lines[head - 1].lstrip().startswith("#") and "模型源" in lines[head - 1]:
            head -= 1
        lines[head : end + 1] = body
        return "".join(lines)

    if text and not text.endswith("\n"):
        text += "\n"
    sep = "\n" if text.strip() else ""
    return text + sep + "".join(body)


def _redact(text: str) -> str:
    r"""把 diff 里的密钥值打码。

    两个容易写错的点：``\s`` 会吃掉换行（``KEY=\n`` 会被拆成两行），以及
    空值不算密钥（``TDAI_LLM_API_KEY=`` 是"回落"的正常形态，不该显示成
    ``<redacted>`` 让人以为丢了什么）。
    """
    out = []
    for raw in text.splitlines(keepends=True):
        trailing = "\n" if raw.endswith("\n") else ""
        line = raw[: -len(trailing)] if trailing else raw
        m = re.match(r"^(\s*[-+]?\s*)([A-Za-z0-9_]+)([ \t]*[=:][ \t]*)(.*)$", line)
        if m and m.group(4).strip() and SECRET_KEY_RE.search(m.group(2)):
            out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}<redacted>{trailing}")
        else:
            out.append(
                re.sub(r"sk-[A-Za-z0-9._-]{8,}", "sk-<redacted>", line) + trailing
            )
    return "".join(out)


# ══════════════════════════════════════════════════════════════════
# Hermes config.yaml（外科文本编辑，保留注释与键序）
# ══════════════════════════════════════════════════════════════════
def _top_block(lines: list[str], name: str) -> tuple[int, int] | None:
    """定位列 0 的 ``name:`` 块，返回 ``[start, end)``（end 不含空行/下一个顶层键）。"""
    header = re.compile(rf"^{re.escape(name)}:[ \t]*(.*)$")
    start = None
    for i, line in enumerate(lines):
        if header.match(line):
            start = i
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            break
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            end += 1
            continue
        break
    return start, end


def _set_block_keys(lines: list[str], start: int, end: int, updates: dict[str, str]) -> bool:
    changed = False
    remaining = dict(updates)
    for i in range(start + 1, end):
        m = re.match(r"^([ \t]+)([A-Za-z0-9_]+):[ \t]*(.*)$", lines[i])
        if not m or m.group(2) not in remaining:
            continue
        key = m.group(2)
        new = f"{m.group(1)}{key}: {remaining.pop(key)}\n"
        if lines[i] != new:
            lines[i] = new
            changed = True
    # 缺失的键补在块尾（跟随块内已有缩进）
    indent = "  "
    for line in lines[start + 1 : end]:
        m = re.match(r"^([ \t]+)[A-Za-z0-9_]+:", line)
        if m:
            indent = m.group(1)
            break
    insert_at = end
    for key, value in remaining.items():
        lines.insert(insert_at, f"{indent}{key}: {value}\n")
        insert_at += 1
        changed = True
    return changed


def _list_item_indent(lines: list[str]) -> str:
    counts: dict[str, int] = {}
    for line in lines:
        stripped = line.lstrip(" \t")
        if stripped.startswith("- "):
            indent = line[: len(line) - len(stripped)]
            if indent in ("", "  "):
                counts[indent] = counts.get(indent, 0) + 1
    return "  " if counts.get("  ", 0) > counts.get("", 0) else ""


def _block_item_indent(lines: list[str], start: int, end: int) -> str | None:
    """块内已有列表项的缩进；混合缩进会让整份 YAML 解析失败，必须跟随。"""
    for line in lines[start + 1 : end]:
        stripped = line.lstrip(" \t")
        if stripped.startswith("- "):
            return line[: len(line) - len(stripped)]
    return None


def render_hermes(text: str, resolved: Resolved) -> str | None:
    """把 model / fallback 两个块写成注册表要求的样子；无 model 块返回 None。"""
    lines = text.splitlines(keepends=True)
    model_block = _top_block(lines, "model")
    if model_block is None:
        return None

    start, end = model_block
    _set_block_keys(
        lines,
        start,
        end,
        {
            "default": resolved.model.id,
            "provider": resolved.provider.hermes_provider,
            "base_url": resolved.provider.openai_url,
        },
    )

    indent = _list_item_indent(lines)
    fallback = resolved.fallback
    fallback_provider = resolved.fallback_provider

    def _replace_or_insert(name: str, block: list[str]) -> None:
        found = _top_block(lines, name)
        if found is not None:
            lines[found[0] : found[1]] = block
            return
        anchor = _top_block(lines, "model")
        at = anchor[1] if anchor else len(lines)
        lines[at:at] = block

    if fallback is not None and fallback_provider is not None:
        found = _top_block(lines, "fallback_providers")
        if found is not None:
            indent = _block_item_indent(lines, found[0], found[1]) or indent
        _replace_or_insert(
            "fallback_providers",
            ["fallback_providers:\n", f"{indent}- {fallback_provider.hermes_provider}\n"],
        )
        _replace_or_insert(
            "fallback_model",
            [
                "fallback_model:\n",
                f"  provider: {fallback_provider.hermes_provider}\n",
                f"  model: {fallback.id}\n",
            ],
        )
    else:
        for name in ("fallback_providers", "fallback_model"):
            found = _top_block(lines, name)
            if found is not None:
                lines[found[0] : found[1]] = []

    return "".join(lines)


def read_hermes_live(text: str) -> tuple[str, str, str]:
    """读出 ``(model, provider, base_url)``；读不到给空串。"""
    lines = text.splitlines()
    block = _top_block([l + "\n" for l in lines], "model")
    if block is None:
        return "", "", ""
    values = {"default": "", "provider": "", "base_url": ""}
    for line in lines[block[0] + 1 : block[1]]:
        m = re.match(r"^[ \t]+(default|provider|base_url):[ \t]*(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip("\"'")
    return values["default"], values["provider"], values["base_url"]


# ══════════════════════════════════════════════════════════════════
# Claude Code（settings.json + cc-connect config.toml）
# ══════════════════════════════════════════════════════════════════
CLAUDE_TIER_KEYS = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
)


def claude_model_id(resolved: Resolved) -> str:
    return resolved.model.id + resolved.model.claude_suffix


def render_claude_settings(text: str, resolved: Resolved, key: str = "") -> str:
    data = json.loads(text)
    env = data.setdefault("env", {})
    tier = claude_model_id(resolved)
    env["ANTHROPIC_BASE_URL"] = resolved.provider.anthropic_url or resolved.provider.openai_url
    env["ANTHROPIC_MODEL"] = tier
    for tier_key in CLAUDE_TIER_KEYS:   # 不要用 key 当循环变量：会盖掉函数参数
        env[tier_key] = tier
        # cc-connect 会写配套的展示名；只改 *_MODEL 会留下指向旧 id 的显示名
        if tier_key + "_NAME" in env:
            env[tier_key + "_NAME"] = resolved.model.id
    if "ANTHROPIC_MODEL_NAME" in env:
        env["ANTHROPIC_MODEL_NAME"] = resolved.model.id
    if "CLAUDE_CODE_SUBAGENT_MODEL" in env:
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = tier
    # 非 Anthropic 的模型 id 不被识别 ⇒ Claude Code 按 200K 算窗口。
    # 不设这个值不会报错，只会让长会话提前压缩（静默降质）。
    if resolved.model.claude_max_context:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(resolved.model.claude_max_context)
    # 钥匙写在这一层（而不是只靠容器环境）：这是切换前就验证过的路径，
    # cc-connect 拉起 claude 时一定读得到。写错的后果不是报错，而是「拿 A 家的
    # 钥匙打 B 家的端点」的 401 —— 所以每次 apply 都跟着源一起改。
    # 没有钥匙时（--allow-missing-keys）宁可删掉，让 entrypoint 的回落值接手。
    if key:
        env["ANTHROPIC_AUTH_TOKEN"] = key
        env.pop("ANTHROPIC_API_KEY", None)
    else:
        for stale in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            env.pop(stale, None)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_cc_toml(text: str, resolved: Resolved) -> str | None:
    """改写 ``[projects.agent.options.env]`` 段里的模型键；没有该段返回 ``None``。"""
    tier = claude_model_id(resolved)
    wanted = {
        "ANTHROPIC_BASE_URL": resolved.provider.anthropic_url or resolved.provider.openai_url,
        "ANTHROPIC_MODEL": tier,
    }
    wanted.update({key: tier for key in CLAUDE_TIER_KEYS})
    if resolved.model.claude_max_context:
        wanted["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(resolved.model.claude_max_context)

    lines = text.splitlines(keepends=True)
    header = None
    for i, line in enumerate(lines):
        if line.strip() == "[projects.agent.options.env]":
            header = i
            break
    if header is None:
        return None  # 没有这段就不碰（cc-connect 配置形状归它自己）

    end = len(lines)
    for i in range(header + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break

    seen: set[str] = set()
    for i in range(header + 1, end):
        m = re.match(r"^([A-Za-z0-9_]+)[ \t]*=[ \t]*(.*)$", lines[i].strip())
        if not m or m.group(1) not in wanted:
            continue
        key = m.group(1)
        seen.add(key)
        lines[i] = f'{key} = "{wanted[key]}"\n'
    missing = [(k, v) for k, v in wanted.items() if k not in seen]
    for key, value in missing:
        lines.insert(end, f'{key} = "{value}"\n')
        end += 1
    return "".join(lines)


def read_claude_live(settings_text: str) -> tuple[str, str]:
    """返回 ``(model, base_url)``，去掉 ``[1M]`` 之类的后缀。"""
    try:
        env = json.loads(settings_text).get("env", {})
    except json.JSONDecodeError:
        return "", ""
    model = str(env.get("ANTHROPIC_MODEL", ""))
    model = re.sub(r"\[[^\]]*\]$", "", model)
    return model, str(env.get("ANTHROPIC_BASE_URL", ""))


# ══════════════════════════════════════════════════════════════════
# OpenClaw（openclaw.json）
# ══════════════════════════════════════════════════════════════════
def render_openclaw(text: str, resolved: Resolved) -> str:
    data = json.loads(text)
    models = data.setdefault("models", {})
    providers = models.setdefault("providers", {})
    pname = resolved.provider.openclaw_provider
    model = resolved.model

    providers[pname] = {
        "baseUrl": resolved.provider.openai_url,
        "api": "openai-completions",
        # 与 dailyinfo MCP 同一套占位符约定：真钥匙留在容器环境里
        "apiKey": f"${{{resolved.provider.key_env}}}",
        "models": [
            {
                "id": model.id,
                "name": model.label,
                "reasoning": model.reasoning,
                "input": list(model.input),
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": model.context,
                "maxTokens": model.max_output,
            }
        ],
    }

    defaults = data.setdefault("agents", {}).setdefault("defaults", {})
    primary = f"{pname}/{model.id}"
    if resolved.fallback is not None and resolved.fallback_provider is not None:
        fallback_provider = resolved.fallback_provider
        fallback_key = f"{fallback_provider.openclaw_provider}/{resolved.fallback.id}"
        if fallback_provider.openclaw_provider not in providers:
            providers[fallback_provider.openclaw_provider] = {
                "baseUrl": fallback_provider.openai_url,
                "api": "openai-completions",
                "apiKey": f"${{{fallback_provider.key_env}}}",
                "models": [
                    {
                        "id": resolved.fallback.id,
                        "name": resolved.fallback.label,
                        "reasoning": resolved.fallback.reasoning,
                        "input": list(resolved.fallback.input),
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": resolved.fallback.context,
                        "maxTokens": resolved.fallback.max_output,
                    }
                ],
            }
        defaults["model"] = {
            "primary": primary,
            "fallbacks": [fallback_key],
        }
    else:
        defaults["model"] = {"primary": primary}

    allow = defaults.setdefault("models", {})
    allow[primary] = {"alias": model.label}
    if resolved.fallback is not None and resolved.fallback_provider is not None:
        fk = f"{resolved.fallback_provider.openclaw_provider}/{resolved.fallback.id}"
        allow.setdefault(fk, {})
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def read_openclaw_live(text: str) -> tuple[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "", ""
    model = data.get("agents", {}).get("defaults", {}).get("model", {})
    primary = str(model.get("primary", ""))
    provider, _, mid = primary.partition("/")
    return mid, provider


# ══════════════════════════════════════════════════════════════════
# 适配器：目标文件 + 期望值
# ══════════════════════════════════════════════════════════════════
@dataclass
class Change:
    path: Path
    adapter: str
    binding: str
    old: str | None
    new: str
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.old is None or self.old != self.new


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


HERMES_TARGETS = {
    "hermes.default": Path(".hermes/config.yaml"),
    "hermes.coder": Path(".hermes/profiles/coder/config.yaml"),
    "hermes.finance": Path(".hermes/profiles/finance/config.yaml"),
    "hermes.daoyuan": Path(".hermes/profiles/daoyuan/config.yaml"),
}

BOT_TARGETS = {
    "bot.zhixun": ("ZHIXUN_BOT", REPO_ROOT / ".env.zhixun-bot"),
    "bot.tianyi": ("TIANYI_BOT", REPO_ROOT / ".env.tianyi-bot"),
}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def plan_hermes(reg: Registry, root: Path, env: dict[str, str]) -> Plan:
    plan = Plan()
    for binding, rel in HERMES_TARGETS.items():
        if binding not in reg.bindings:
            continue
        path = root / rel
        old = _read(path)
        if old is None:
            plan.skipped.append(f"{path}（不存在，跳过）")
            continue
        resolved = reg.resolve(reg.bindings[binding])
        new = render_hermes(old, resolved)
        if new is None:
            plan.skipped.append(f"{path}（没有 model 块，跳过）")
            continue
        plan.changes.append(Change(path, "hermes", binding, old, new))
    return plan


def plan_claude(reg: Registry, root: Path, env: dict[str, str]) -> Plan:
    plan = Plan()
    if "claude-code" not in reg.bindings:
        return plan
    resolved = reg.resolve(reg.bindings["claude-code"])

    settings = root / ".claude" / "settings.json"
    old = _read(settings)
    if old is None:
        plan.skipped.append(f"{settings}（不存在，跳过）")
    else:
        try:
            plan.changes.append(
                Change(settings, "claude", "claude-code", old,
                       render_claude_settings(
                           old, resolved, env.get(resolved.provider.key_env, "").strip()
                       ))
            )
        except json.JSONDecodeError:
            plan.skipped.append(f"{settings}（不是合法 JSON，跳过）")

    toml_path = root / ".cc-connect" / "config.toml"
    old = _read(toml_path)
    if old is None:
        plan.skipped.append(f"{toml_path}（不存在，跳过）")
    else:
        new = render_cc_toml(old, resolved)
        if new is None:
            plan.skipped.append(f"{toml_path}（没有 [projects.agent.options.env] 段，跳过）")
        else:
            plan.changes.append(Change(toml_path, "claude", "claude-code", old, new))
    return plan


def plan_openclaw(reg: Registry, root: Path, env: dict[str, str]) -> Plan:
    plan = Plan()
    if "openclaw.main" not in reg.bindings:
        return plan
    path = root / ".openclaw" / "openclaw.json"
    old = _read(path)
    if old is None:
        plan.skipped.append(f"{path}（不存在，跳过）")
        return plan
    resolved = reg.resolve(reg.bindings["openclaw.main"])
    try:
        plan.changes.append(Change(path, "openclaw", "openclaw.main", old,
                                   render_openclaw(old, resolved)))
    except json.JSONDecodeError:
        plan.skipped.append(f"{path}（不是合法 JSON，跳过）")
    return plan


def plan_bots(reg: Registry, root: Path, env: dict[str, str]) -> Plan:
    plan = Plan()
    for binding, (prefix, path) in BOT_TARGETS.items():
        if binding not in reg.bindings:
            continue
        old = _read(path)
        if old is None:
            plan.skipped.append(f"{path}（不存在，跳过）")
            continue
        resolved = reg.resolve(reg.bindings[binding])
        key = env.get(resolved.provider.key_env, "").strip()
        updates = {
            f"{prefix}_MODEL_ID": resolved.model.id,
            f"{prefix}_MODEL_BASE_URL": resolved.provider.openai_url,
            f"{prefix}_MODEL_CONTEXT_WINDOW": str(resolved.model.context),
            f"{prefix}_MODEL_MAX_TOKENS": str(resolved.model.max_output),
        }
        if key:
            updates[f"{prefix}_MODEL_API_KEY"] = key
        new = render_dotenv(
            old,
            updates,
            anchors={
                f"{prefix}_MODEL_CONTEXT_WINDOW": f"{prefix}_MODEL_BASE_URL",
                f"{prefix}_MODEL_MAX_TOKENS": f"{prefix}_MODEL_CONTEXT_WINDOW",
            },
        )
        if f"{prefix}_MODEL_API_KEY" not in updates:
            plan.skipped.append(f"{path}（缺 {resolved.provider.key_env}，本次不改钥匙）")
        plan.changes.append(Change(path, "bots", binding, old, new))
    return plan


def plan_env(reg: Registry, root: Path, env: dict[str, str]) -> Plan:
    """仓库 .env 的托管块：给 compose 注入用。

    覆盖两类需要 env 中转的消费方：Claude Code（端点 + 钥匙变量名）和 TDAI
    （模型 + 端点 + 钥匙）。都写进同一个 ``# BEGIN MODELCTL`` 块 —— 谁先写谁后写
    会互相覆盖，所以必须由同一处一次性产出。
    """
    plan = Plan()
    path = DEFAULT_ENV_FILE
    old = _read(path)
    entries = env_entries(reg, env)

    if not entries:
        return plan
    if old is None:
        plan.skipped.append(f"{path}（不存在，跳过）")
        return plan
    new = render_managed_block(old, entries)
    plan.changes.append(Change(path, "env", "env", old, new))
    return plan


def env_entries(reg: Registry, env: dict[str, str]) -> dict[str, str]:
    """``.env`` 托管块里该有哪些键（两个消费方共用一块，必须一次算全）。"""
    entries: dict[str, str] = {}

    if "claude-code" in reg.bindings:
        claude = reg.resolve(reg.bindings["claude-code"])
        entries["ANTHROPIC_BASE_URL"] = (
            claude.provider.anthropic_url or claude.provider.openai_url
        )
        entries["CLAUDE_MODEL_KEY_ENV"] = claude.provider.key_env

    if "tdai-memory" in reg.bindings:
        tdai = reg.resolve(reg.bindings["tdai-memory"])
        entries["TDAI_LLM_MODEL"] = tdai.model.id
        entries["TDAI_LLM_BASE_URL"] = tdai.provider.openai_url
        # 官方源时留空 → compose 回落 DEEPSEEK_API_KEY（见 docker-compose.yml），
        # 不在 .env 里复制一份同样的密钥。换到别家才写值。
        entries["TDAI_LLM_API_KEY"] = (
            ""
            if tdai.provider.key_env == "DEEPSEEK_API_KEY"
            else env.get(tdai.provider.key_env, "").strip()
        )

    return entries


ADAPTERS = {
    "hermes": plan_hermes,
    "claude": plan_claude,
    "openclaw": plan_openclaw,
    "bots": plan_bots,
    "env": plan_env,
}

ADAPTER_LABELS = {
    "hermes": "Hermes 配置（4 个 profile）",
    "claude": "Claude Code（settings.json + cc-connect）",
    "openclaw": "虾酱网关（openclaw.json）",
    "bots": "知汛 / 天一 bot（.env.*-bot）",
    "env": "仓库 .env 托管块（Claude Code + TDAI 经 compose 注入）",
}


# ══════════════════════════════════════════════════════════════════
# status / check
# ══════════════════════════════════════════════════════════════════
def _live_value(reg: Registry, binding: str, root: Path, env: dict[str, str]) -> tuple[str, str]:
    """读 live 配置里的 ``(model_id, provider_name)``；读不到给 ``("?","?")``。"""
    if binding in HERMES_TARGETS:
        text = _read(root / HERMES_TARGETS[binding])
        if text is None:
            return "?", "?"
        # 第二个字段必须是 base_url，不能是 provider 名：`_desired_pair` 对 Hermes
        # 返回的是端点 URL，拿 provider 名去比会永远不等（换源前两边都是 "deepseek"，
        # 掩盖了这个错位）。
        model, _provider, base_url = read_hermes_live(text)
        return model or "?", base_url or "?"
    if binding == "claude-code":
        text = _read(root / ".claude" / "settings.json")
        if text is None:
            return "?", "?"
        model, base = read_claude_live(text)
        return model or "?", base or "?"
    if binding == "openclaw.main":
        text = _read(root / ".openclaw" / "openclaw.json")
        if text is None:
            return "?", "?"
        model, provider = read_openclaw_live(text)
        return model or "?", provider or "?"
    if binding in BOT_TARGETS:
        prefix, path = BOT_TARGETS[binding]
        values = read_env(path)
        return values.get(f"{prefix}_MODEL_ID", "?"), values.get(f"{prefix}_MODEL_BASE_URL", "?")
    if binding == "tdai-memory":
        values = read_env(DEFAULT_ENV_FILE)
        return values.get("TDAI_LLM_MODEL", "?"), values.get("TDAI_LLM_BASE_URL", "?")
    return "?", "?"


def _desired_pair(resolved: Resolved) -> tuple[str, str]:
    if resolved.binding == "claude-code":
        return resolved.model.id, resolved.provider.anthropic_url
    if resolved.binding == "openclaw.main":
        return resolved.model.id, resolved.provider.openclaw_provider
    return resolved.model.id, resolved.provider.openai_url


def cmd_status(
    reg: Registry,
    root: Path,
    env: dict[str, str],
    missing_env: list[str] | None = None,
    *,
    quiet: bool = False,
) -> int:
    drift = 0
    missing_env = missing_env or []
    missing_keys = sorted(
        {r.provider.key_env for r in reg.resolved_bindings() if not env.get(r.provider.key_env)}
    )

    if not quiet:
        print("模型源注册表 → live 配置\n")
    for resolved in reg.resolved_bindings():
        want_model, want_target = _desired_pair(resolved)
        live_model, live_target = _live_value(reg, resolved.binding, root, env)
        ok = live_model == want_model and live_target == want_target
        if not ok:
            drift += 1
        mark = "✔" if ok else "✘"
        binding = reg.bindings[resolved.binding]
        role = binding.role or "model"
        print(
            f"{mark} {resolved.binding:<18} {role:<8} "
            f"{resolved.provider.name + '/' + resolved.model.id:<40} "
            f"live={live_model}"
        )
        if not ok:
            print(f"    期望 {want_model}  ({want_target})")
            print(f"    实际 {live_model}  ({live_target})")

    if reg.pinned:
        if not quiet:
            print("\n钉住（不参与切换）：")
        for name, pinned in reg.pinned.items():
            print(f"• {name:<18} {pinned.model:<40} {pinned.reason[:48]}…")

    if missing_keys or missing_env:
        print("\n缺少部署配置（apply 会拒绝执行）：")
        for key in missing_keys:
            print(f"  ! {key} 未在 .env 中配置（模型钥匙）")
        for name in missing_env:
            print(f"  ! {name} 未在 .env 中配置（服务端点）")
    if not quiet:
        print()
    return 1 if (drift or missing_keys or missing_env) else 0


def cmd_check(
    reg: Registry, root: Path, env: dict[str, str], missing_env: list[str] | None = None
) -> int:
    return cmd_status(reg, root, env, missing_env)


# ══════════════════════════════════════════════════════════════════
# apply
# ══════════════════════════════════════════════════════════════════
def _backup(path: Path, stamp: str) -> Path:
    label = str(path).lstrip("/").replace("/", "__")
    dest = BACKUP_ROOT / stamp / label
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    os.chmod(dest, 0o600)
    return dest


def _print_change(change: Change, *, show_diff: bool) -> None:
    tag = "写" if change.changed else "="
    print(f"  [{tag}] {change.path}")
    if not show_diff or not change.changed or change.old is None:
        return
    diff = difflib.unified_diff(
        _redact(change.old).splitlines(),
        _redact(change.new).splitlines(),
        fromfile=str(change.path),
        tofile=str(change.path),
        n=2,
        lineterm="",
    )
    for line in diff:
        print("      " + line)


def cmd_apply(
    reg: Registry,
    root: Path,
    env: dict[str, str],
    *,
    only: list[str] | None,
    dry_run: bool,
    allow_missing_keys: bool,
    missing_env: list[str] | None = None,
) -> int:
    adapters = only or list(ADAPTERS)
    unknown = [a for a in adapters if a not in ADAPTERS]
    if unknown:
        print(f"未知适配器: {', '.join(unknown)}；可选 {', '.join(ADAPTERS)}", file=sys.stderr)
        return 2

    missing_env = missing_env or []
    wanted_keys = sorted(
        {r.provider.key_env for r in reg.resolved_bindings() if not env.get(r.provider.key_env)}
    )
    if (wanted_keys or missing_env) and not allow_missing_keys:
        if wanted_keys:
            print("拒绝执行：以下密钥未在 .env 中配置 →", ", ".join(wanted_keys), file=sys.stderr)
        if missing_env:
            print("拒绝执行：以下端点未在 .env 中配置 →", ", ".join(missing_env), file=sys.stderr)
        print("  · 缺的值都写在仓库根 .env（模板见 .env.example）：模型钥匙 + 业务空间端点",
              file=sys.stderr)
        if not missing_env:
            print("  · 或只改部分消费方：--only hermes", file=sys.stderr)
        print("  · 确认要继续写「连不上」的配置：--allow-missing-keys", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    written = 0
    skipped: list[str] = []
    for name in adapters:
        print(f"\n[{name}] {ADAPTER_LABELS[name]}")
        plan = ADAPTERS[name](reg, root, env)
        for change in plan.changes:
            _print_change(change, show_diff=dry_run)
            if not change.changed:
                continue
            written += 1
            if dry_run:
                continue
            change.path.parent.mkdir(parents=True, exist_ok=True)
            _backup(change.path, stamp)
            tmp = change.path.with_name(change.path.name + ".modelctl.tmp")
            tmp.write_text(change.new, encoding="utf-8")
            tmp.replace(change.path)
            try:
                os.chmod(change.path, 0o600)
            except OSError:
                pass
        for note in plan.skipped:
            skipped.append(note)
            print(f"  [~] {note}")

    if dry_run:
        print(f"\n（dry-run）将修改 {written} 个文件；备份目录 {BACKUP_ROOT}/<时间戳>/")
        return 0

    if written:
        print(f"\n已写入 {written} 个文件；改动前的副本在 {BACKUP_ROOT}/{stamp}/")
    else:
        print("\n没有需要修改的文件（已是最新）。")

    print(
        """
下一步（环境变量变更必须 recreate，restart 不够）：
  docker compose up -d hermes hermes-coder hermes-finance hermes-daoyuan claude-code
  ./scripts/start-zhixun-bot.sh && ./scripts/start-tianyi-bot.sh
  ⚠ 虾酱：docker compose up -d openclaw-gateway —— 若网关正在跑启动迁移则**不要**现在重启
    （见 CLAUDE.md「OpenClaw 启动迁移期间不要重启网关」），等它自行完成后再说。
  ⚠ 4 个 hermes profile 的 config.yaml 在网关启动时读取；up -d 会一并生效。
"""
    )
    return 0


# ══════════════════════════════════════════════════════════════════
# probe
# ══════════════════════════════════════════════════════════════════
def _http_json(url: str, *, method: str, headers: dict[str, str], body: dict | None) -> tuple[int, dict | str, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", "replace")
            elapsed = time.time() - started
            try:
                return resp.status, json.loads(raw), elapsed
            except json.JSONDecodeError:
                return resp.status, raw[:400], elapsed
    except urllib.error.HTTPError as exc:
        elapsed = time.time() - started
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, raw[:400], elapsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}", time.time() - started


def _probe_one(label: str, url: str, headers: dict[str, str]) -> tuple[bool, str]:
    status, payload, elapsed = _http_json(url, method="GET", headers=headers, body=None)
    if status == 200 and isinstance(payload, dict):
        ids = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
        return True, f"HTTP 200 · {len(ids)} 个模型 · {elapsed:.2f}s"
    return False, f"HTTP {status} · {str(payload)[:200]}"


def probe_targets(reg: Registry) -> list[tuple[Provider, Model]]:
    """要探的 (provider, model) 组合：主源 + 备用源。

    备用源平时不跑，等到主源挂了才发现它不可用就晚了。按 provider 去重，但**不能**
    把主模型套到备用 provider 上 —— 同一家上主/备可能是两个 id（DeepSeek 官方只认
    ``deepseek-flash``，不认百炼那个 ``deepseek-v4.1-flash-1017``），拿错就是假红。
    """
    used: dict[str, tuple[Provider, Model]] = {}
    for resolved in reg.resolved_bindings():
        used.setdefault(resolved.provider.name, (resolved.provider, resolved.model))
        if resolved.fallback is not None and resolved.fallback_provider is not None:
            used.setdefault(
                resolved.fallback_provider.name,
                (resolved.fallback_provider, resolved.fallback),
            )
    return list(used.values())


def cmd_probe(reg: Registry, env: dict[str, str], missing_env: list[str] | None = None) -> int:
    failed = 0
    for provider, model in probe_targets(reg):
        key = env.get(provider.key_env, "").strip()
        print(f"\n[{provider.name}] {provider.label}")
        print(f"  key_env={provider.key_env}  " + ("已配置（长度 %d）" % len(key) if key else "未配置"))
        if not provider.openai_url:
            failed += 1
            print(f"  端点未配置（缺 {', '.join(missing_env or ['端点变量'])}）")
            continue
        if not key:
            failed += 1
            continue

        auth = {"Authorization": f"Bearer {key}"}
        ok, detail = _probe_one("openai", provider.openai_url + "/models", auth)
        suffix = "" if ok else "  （部分端点不实现 /models，以下面的 chat 为准）"
        print(f"  GET  {provider.openai_url}/models → {detail}{suffix}")

        # 一次最小 chat 才是权威判据：/models 有的端点不实现，也可能列了不可调的 id。
        # 不带任何思考参数（各家名字不同），max_tokens 留够以免「预算不足」类 400。
        body = {
            "model": model.id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 64,
            "stream": False,
        }
        status, payload, elapsed = _http_json(
            provider.openai_url + "/chat/completions", method="POST", headers=auth, body=body
        )
        if status == 200 and isinstance(payload, dict):
            got = payload.get("model") or resolved.model.id
            print(f"  POST chat/completions ({model.id}) → HTTP 200 · {elapsed:.2f}s · model={got}")
        else:
            failed += 1
            print(f"  POST chat/completions ({model.id}) → HTTP {status} · {str(payload)[:220]}")

        if provider.anthropic_url:
            headers = {
                "x-api-key": key,
                "Authorization": f"Bearer {key}",
                "anthropic-version": "2023-06-01",
            }
            body = {
                "model": model.id,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "ping"}],
            }
            status, payload, elapsed = _http_json(
                provider.anthropic_url + "/v1/messages", method="POST", headers=headers, body=body
            )
            ok = status == 200 and isinstance(payload, dict)
            failed += 0 if ok else 1
            note = "HTTP 200" if ok else f"HTTP {status} · {str(payload)[:220]}"
            print(f"  POST /v1/messages (Anthropic) → {note} · {elapsed:.2f}s")
    return 1 if failed else 0


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="模型源切换器：注册表 → 各消费方配置（唯一写者）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 scripts/modelctl.py status\n"
            "  python3 scripts/modelctl.py apply --dry-run\n"
            "  python3 scripts/modelctl.py apply --only hermes\n"
            "  python3 scripts/modelctl.py probe\n"
        ),
    )
    parser.add_argument("--root", type=Path, default=Path.home(),
                        help="用户配置根目录（默认 $HOME；测试用）")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help="密钥所在的 dotenv（默认仓库根 .env）")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="期望值 vs live 值")
    sub.add_parser("check", help="同 status，供守卫使用（有漂移/缺 key → exit 1）")

    apply_p = sub.add_parser("apply", help="渲染并写入下游配置（幂等）")
    apply_p.add_argument("--dry-run", action="store_true", help="只打印 diff，不写文件")
    apply_p.add_argument("--only", action="append", choices=sorted(ADAPTERS),
                         help="只处理某个适配器（可重复）")
    apply_p.add_argument("--allow-missing-keys", action="store_true",
                         help="缺密钥也照写（会得到跑不起来的配置）")

    sub.add_parser("probe", help="真打最小请求，验证 key / 端点 / 模型 id")

    args = parser.parse_args(argv)

    try:
        reg = Registry.load(args.registry)
    except RegistryError as exc:
        print(f"注册表错误: {exc}", file=sys.stderr)
        return 2

    env = read_env(args.env_file)
    reg, missing_env = reg.with_env(env)

    if args.command in ("status", "check"):
        return cmd_status(reg, args.root, env, missing_env)
    if args.command == "apply":
        return cmd_apply(
            reg,
            args.root,
            env,
            only=args.only,
            dry_run=args.dry_run,
            allow_missing_keys=args.allow_missing_keys,
            missing_env=missing_env,
        )
    if args.command == "probe":
        return cmd_probe(reg, env, missing_env)
    return 2


if __name__ == "__main__":
    sys.exit(main())