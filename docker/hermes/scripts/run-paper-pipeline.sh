#!/usr/bin/env bash
# =============================================================
# run-paper-pipeline.sh — 论文一键入库流水线
# 输入 DOI 或论文标题，自动完成 下载→上传→Zotero 入库→清理
# 用法: run-paper-pipeline.sh [--dry-run] <DOI_或_论文标题>
# =============================================================
set -euo pipefail

dry_run=false
pdf_url=""
input=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) dry_run=true; shift ;;
        --pdf-url) pdf_url="$2"; shift 2 ;;
        --help|-h)
            echo "用法: run-paper-pipeline.sh [--dry-run] [--pdf-url <URL>] <DOI_或_论文标题>" >&2
            exit 0
            ;;
        *) input="$1"; shift ;;
    esac
done

if [ -z "$input" ]; then
    echo "用法: run-paper-pipeline.sh [--dry-run] <DOI_或_论文标题>" >&2
    exit 2
fi

# DOI 格式: 以 "10." 开头
is_doi() { [[ "$1" =~ ^10\. ]]; }

# arXiv ID 格式: "arxiv:" 前缀 + 数字.数字 (如 arxiv:2605.28713)
# 或纯 arXiv ID: 4-5位数字.5位数字 (如 2605.28713)
if [[ "$input" =~ ^arxiv:([0-9]+\.[0-9]+) ]]; then
    # arxiv:2605.28713 → 10.48550/arXiv.2605.28713
    input="10.48550/arXiv.${BASH_REMATCH[1]}"
    echo "   🔍 检测到 arXiv ID → DOI: $input"
elif [[ "$input" =~ ^([0-9]{4,5}\.[0-9]{4,6}(v[0-9]+)?)$ ]]; then
    # 2605.28713 or 2605.28713v1 → 10.48550/arXiv.2605.28713
    input="10.48550/arXiv.${BASH_REMATCH[1]}"
    echo "   🔍 检测到 arXiv ID → DOI: $input"
fi

PAPERS_DIR=$(mktemp -d /tmp/paper-pipeline-XXXXXX)
PF_JSON=$(mktemp /tmp/pf_pipeline-XXXXXX.json)

echo "📄 论文流水线开始"
echo "   输入: $input"
echo ""

# ── Step 1: 下载 PDF ─────────────────────────────────────────
if [ -n "$pdf_url" ]; then
    # ── PDF 直链模式：跳过 paper-fetch，直接下载 ──────────────
    echo "1️⃣  下载 PDF（直链）..."
    echo "   🔗 $pdf_url"

    # Derive filename from URL or DOI
    if is_doi "$input"; then
        pdf_filename=$(basename "$pdf_url" | sed 's/[?#].*//')
    else
        pdf_filename="${input}.pdf"
    fi
    pdf_filename="${pdf_filename:-paper.pdf}"

    if $dry_run; then
        echo "   [dry-run] 将下载: $pdf_url → $PAPERS_DIR/$pdf_filename"
        pf_title="(PDF 直链)"
        pf_doi="$input"
        pf_file=""
        basename="$pdf_filename"
    else
        # Use Python urllib (no --no-check-certificate needed)
        python3 -c "
import urllib.request, ssl, sys
url = '$pdf_url'
dest = '$PAPERS_DIR/$pdf_filename'
ctx = ssl.create_default_context()
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    resp = urllib.request.urlopen(req, context=ctx, timeout=60)
    with open(dest, 'wb') as f:
        f.write(resp.read())
    print(f'✅ 已下载: {dest}')
except Exception as e:
    # Fallback: try without cert verification (some publishers have bad certs)
    ctx2 = ssl._create_unverified_context()
    req2 = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp2 = urllib.request.urlopen(req2, context=ctx2, timeout=60)
    with open(dest, 'wb') as f:
        f.write(resp2.read())
    print(f'⚠️  已下载（跳过证书验证）: {dest}', file=sys.stderr)
" || true

        if [ ! -f "$PAPERS_DIR/$pdf_filename" ]; then
            echo "❌ PDF 下载失败" >&2
            # Fallback to metadata-only
            echo "   → 使用 Crossref 元数据创建 Zotero 条目（无 PDF）..."
            if $dry_run; then
                /opt/hermes/scripts/paper-to-zotero.py --dry-run --metadata-only "$input"
            else
                result=$(/opt/hermes/scripts/paper-to-zotero.py --metadata-only "$input")
                zotero_key=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['zotero_key'])")
                echo ""
                echo "========================================"
                echo "✅ 流水线完成（仅元数据）"
                echo "   🔑 DOI: $input"
                echo "   📚 Zotero: $zotero_key"
                echo "========================================"
            fi
            rm -f "$PF_JSON"
            rmdir "$PAPERS_DIR" 2>/dev/null || true
            exit 0
        fi
        pf_title="(PDF 直链)"
        pf_doi="$input"
        pf_file=""
        basename="$pdf_filename"
    fi
else
    # ── 常规模式：paper-fetch ──────────────────────────────
    echo "1️⃣  下载 PDF..."

    if is_doi "$input"; then
        cd /opt/data/skills/paper-fetch \
            && python3 scripts/fetch.py "$input" --out "$PAPERS_DIR" --format json > "$PF_JSON"
    else
        cd /opt/data/skills/paper-fetch \
            && python3 scripts/fetch.py --title "$input" --out "$PAPERS_DIR" --format json > "$PF_JSON"
    fi
fi

# ── Parse paper-fetch output (normal mode only) ──────────────
if [ -z "$pdf_url" ]; then
    ok=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['ok'])")
    if [ "$ok" != "True" ]; then
        echo "⚠️  paper-fetch 下载失败（期刊爬虫阻挡或无法访问）" >&2
        # Fallback: metadata-only Zotero entry via Crossref
        if is_doi "$input"; then
            echo "   → 使用 Crossref 元数据创建 Zotero 条目（无 PDF）..."
            if $dry_run; then
                /opt/hermes/scripts/paper-to-zotero.py --dry-run --metadata-only "$input"
            else
                result=$(/opt/hermes/scripts/paper-to-zotero.py --metadata-only "$input")
                zotero_key=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['zotero_key'])")
                echo ""
                echo "========================================"
                echo "✅ 流水线完成（仅元数据）"
                echo "   🔑 DOI: $input"
                echo "   📚 Zotero: $zotero_key"
                echo "========================================"
            fi
            rm -f "$PF_JSON"
            rmdir "$PAPERS_DIR" 2>/dev/null || true
            exit 0
        else
            echo "❌ paper-fetch 下载失败（无法获取 DOI）" >&2
            cat "$PF_JSON" >&2
            rm -f "$PF_JSON"
            rmdir "$PAPERS_DIR" 2>/dev/null || true
            exit 1
        fi
    fi

    pf_file=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['data']['results'][0].get('file') or '')")
    pf_title=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['data']['results'][0]['meta']['title'][:80])")
    pf_doi=$(python3 -c "import json; print(json.load(open('$PF_JSON'))['data']['results'][0].get('doi', ''))")

    if [ -n "$pf_file" ]; then
        basename=$(basename "$pf_file")
        echo "   ✅ $pf_title"
        echo "   📁 $basename"
        echo ""

        # ── Step 2: 上传到 Google Drive ───────────────────────
        echo "2️⃣  上传到 Google Drive..."
        rclone copy "$PAPERS_DIR/$basename" gdrive:
        echo "   ✅ $basename → gdrive:"
        echo ""
    else
        basename=""
        echo "   ✅ $pf_title"
        echo "   ℹ️  无 PDF 文件（期刊爬虫阻挡），将创建仅元数据条目"
        echo ""
    fi
else
    # ── PDF 直链模式：上传到 Google Drive ────────────────────
    pf_title="(PDF 直链)"
    pf_doi="$input"
    if [ -n "$basename" ]; then
        echo ""
        if $dry_run; then
            echo "2️⃣  [dry-run] 将上传: $PAPERS_DIR/$basename → gdrive:"
        else
            echo "2️⃣  上传到 Google Drive..."
            rclone copy "$PAPERS_DIR/$basename" gdrive:
            echo "   ✅ $basename → gdrive:"
        fi
        echo ""
    fi
fi

# ── Step 3: 创建 Zotero 条目 ──────────────────────────────────
echo "3️⃣  创建 Zotero 条目..."

if [ -n "$pdf_url" ]; then
    # PDF 直链模式：metadata-only + linked_file attachment
    if $dry_run; then
        if [ -n "$basename" ]; then
            /opt/hermes/scripts/paper-to-zotero.py --dry-run --metadata-only --pdf-filename "$basename" "$input"
        else
            /opt/hermes/scripts/paper-to-zotero.py --dry-run --metadata-only "$input"
        fi
    else
        if [ -n "$basename" ]; then
            result=$(/opt/hermes/scripts/paper-to-zotero.py --metadata-only --pdf-filename "$basename" "$input")
        else
            result=$(/opt/hermes/scripts/paper-to-zotero.py --metadata-only "$input")
        fi
        zotero_key=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['zotero_key'])")
        zotero_title=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['title'])")
        echo "   ✅ 已创建 Zotero 条目"
        echo "   📚 Key: $zotero_key"
    fi
elif $dry_run; then
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
if [ -n "$basename" ]; then
    rm -f "$PAPERS_DIR/$basename"
fi
rm -f "$PF_JSON"
rmdir "$PAPERS_DIR" 2>/dev/null || true
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
