#!/usr/bin/env bash
# =============================================================
# run-paper-pipeline.sh — 论文一键入库流水线
# 输入 DOI 或论文标题，自动完成 下载→上传→Zotero 入库→清理
# 用法: run-paper-pipeline.sh [--dry-run] <DOI_或_论文标题>
# =============================================================
set -euo pipefail

dry_run=false
input=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=true ;;
        --help|-h)
            echo "用法: run-paper-pipeline.sh [--dry-run] <DOI_或_论文标题>" >&2
            exit 0
            ;;
        *) input="$arg" ;;
    esac
done

if [ -z "$input" ]; then
    echo "用法: run-paper-pipeline.sh [--dry-run] <DOI_或_论文标题>" >&2
    exit 2
fi

# DOI 格式: 以 "10." 开头
is_doi() { [[ "$1" =~ ^10\. ]]; }

PAPERS_DIR="/tmp/papers"
PF_JSON="/tmp/pf_pipeline.json"
mkdir -p "$PAPERS_DIR"

echo "📄 论文流水线开始"
echo "   输入: $input"
echo ""

# ── Step 1: 下载 PDF ─────────────────────────────────────────
echo "1️⃣  下载 PDF..."

if is_doi "$input"; then
    cd /opt/data/skills/paper-fetch \
        && python3 scripts/fetch.py "$input" --out "$PAPERS_DIR" --format json > "$PF_JSON"
else
    cd /opt/data/skills/paper-fetch \
        && python3 scripts/fetch.py --title "$input" --out "$PAPERS_DIR" --format json > "$PF_JSON"
fi

ok=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['ok'])")
if [ "$ok" != "True" ]; then
    echo "❌ paper-fetch 下载失败" >&2
    cat "$PF_JSON" >&2
    rm -f "$PF_JSON"
    exit 1
fi

pf_file=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['data']['results'][0]['file'])")
pf_title=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['data']['results'][0]['meta']['title'][:80])")
pf_doi=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['data']['results'][0].get('doi', ''))")
basename=$(basename "$pf_file")

echo "   ✅ $pf_title"
echo "   📁 $basename"
echo ""

# ── Step 2: 上传到 Google Drive ───────────────────────────────
echo "2️⃣  上传到 Google Drive..."
rclone copy "/tmp/papers/$basename" gdrive:
echo "   ✅ $basename → gdrive:"
echo ""

# ── Step 3: 创建 Zotero 条目 ──────────────────────────────────
echo "3️⃣  创建 Zotero 条目..."

if $dry_run; then
    /opt/hermes/scripts/paper-to-zotero.py --dry-run "$PF_JSON"
else
    result=$(/opt/hermes/scripts/paper-to-zotero.py "$PF_JSON")
    zotero_key=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['zotero_key'])")
    zotero_title=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['title'])")
    echo "   ✅ 已创建 Zotero 条目"
    echo "   📚 Key: $zotero_key"
fi
echo ""

# ── Step 4: 清理临时文件 ─────────────────────────────────────
echo "4️⃣  清理临时文件..."
rm -f "/tmp/papers/$basename" "$PF_JSON"
echo "   ✅ 完成"
echo ""

# ── 汇总 ──────────────────────────────────────────────────────
echo "========================================"
echo "✅ 流水线完成"
echo "   📄 $pf_title"
if [ -n "${pf_doi:-}" ]; then
    echo "   🔑 DOI: $pf_doi"
fi
if ! $dry_run; then
    echo "   📚 Zotero: $zotero_key"
fi
echo "========================================"
