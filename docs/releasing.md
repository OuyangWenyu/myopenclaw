# 发布流程

> 最后更新：2026-11-08（v0.2.1 起采用 uv 管理版本 + Release checks）

## 原则

- **版本唯一来源是 `pyproject.toml` 的 `[project].version`**；`uv.lock` 与安装环境由 uv 同步。
- **不手改** `pyproject.toml` 或 `uv.lock`；用 `uv version`。
- **不基于 Git tag 动态生成版本**（没有 hatch-vcs / setuptools-scm）：先改版本、提交、验收，再对验收过的 SHA 打 tag。
- 只发 **GitHub tag + Release**；不上传 PyPI，不制作 wheel/sdist。文档由 `docs.yml` 自动发布到 GitHub Pages。
- **已公开的 tag 不删除、不移动**；发布错误时发修正版（见 [v0.2.1](releases/v0.2.1.md) 对 v0.2.0 的修正）。

## 选择版本

```bash
# 修复：0.2.1 -> 0.2.2
uv version --bump patch

# 新能力：0.2.1 -> 0.3.0
uv version --bump minor

# 不兼容变更：0.2.1 -> 1.0.0
uv version --bump major

# 精确版本（少用）
uv version 0.3.0
```

`uv version` 会同时更新 `pyproject.toml` 与 `uv.lock`。执行后确认：

```bash
uv lock --check
uv version --short
```

## 发布前验收

```bash
uv sync --locked
uv run --with pytest --with pyyaml pytest tests/ -q
uv sync --group docs && uv run mkdocs build --strict

# 部署面冒烟（本机栈在跑时）
docker compose ps
docker compose exec hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan mcp test dailyinfo
```

## 发布步骤

1. **写发布说明**：新增 `docs/releases/vX.Y.Z.md`，写清本版能力、升级注意、验收结果与已知边界；在 `mkdocs.yml` 的「版本说明」下加条目。
2. **提交并推送 main**：
   ```bash
   git add -- pyproject.toml uv.lock docs/releases/ mkdocs.yml ...
   git commit -m "chore(release): X.Y.Z"
   git push origin main
   ```
3. **等主线的 Release checks 通过**，记下验收过的完整提交 SHA（不要对远端最新 main 盲目打 tag）。
4. **对验收过的 SHA 打 tag**：
   ```bash
   git tag -a vX.Y.Z <已验证提交SHA> -m "myopenclaw vX.Y.Z"
   git push origin refs/tags/vX.Y.Z
   ```
5. **等该 tag 的 Release checks 通过**，再创建 GitHub Release：
   ```bash
   gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" \
     --notes-file docs/releases/vX.Y.Z.md
   ```
6. 复核：
   ```bash
   uv lock --check
   gh release view vX.Y.Z
   ```

## Release checks 检查什么

`.github/workflows/release-checks.yml` 在 `main`、`v*` tag、PR 和手动触发时运行：

- `uv sync --locked`：lockfile 必须与 `pyproject.toml` 一致（v0.2.0 的教训）
- `uv lock --check`
- tag 推送时：`GITHUB_REF_NAME == v$(uv version --short)`
- `pytest tests/ -q`
- `mkdocs build --strict`

它**不**替维护者选版本号，也不自动创建 tag / Release。

## 常见问题

| 现象 | 处理 |
|---|---|
| `uv sync --locked` 报 lockfile 需要更新 | 忘了 `uv version`；重新用 `uv version --bump ...`，不要手改 lock |
| tag 与版本不一致 | 删除未推送的本地 tag；已推送则发修正版，不移动 tag |
| Release checks 在 tag 上红 | 修 main 后发下一个 patch；不要把 tag 指到新提交 |
| 文档 nav 报孤儿页 | 新页面必须加进 `mkdocs.yml` nav（`tests/test_member_docs.py` 会拦） |
