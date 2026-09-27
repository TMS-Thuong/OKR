#!/usr/bin/env bash
#
# scan-code-map.sh - コードベースの構造情報を JSON に書き出す（KR1 step 1）
#
# 2種類のスキャナを自動で使い分ける。
#   rails    scan_code_map.py  Rails を深く読む（routes DSL, before_action, Vue 連携）
#   generic  scan_generic.py   言語・フレームワークを問わない（import と型名でつなぐ）
#
# `config/routes.rb` と `app/controllers/` があれば rails、無ければ generic。
# `--scanner rails|generic`、または .claude/code-map.json の "scanner" で固定できる。
# どちらも標準ライブラリのみ使用するので pip / bundler / docker は不要。
#
# 使い方:
#   scripts/scan-code-map.sh                       # 全体スキャン
#   scripts/scan-code-map.sh --since <commit>      # 差分のみ再読込（rails のみ。generic は常に全体）
#   scripts/scan-code-map.sh --scanner generic     # スキャナを指定
#   scripts/scan-code-map.sh --help                # オプション一覧

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Python 3 がインストールされた環境で実行してください。" >&2
  exit 1
fi

scanner=""
root="."
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --scanner) scanner="$2"; shift 2 ;;
    --scanner=*) scanner="${1#--scanner=}"; shift ;;
    --root) root="$2"; args+=("$1" "$2"); shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done

if [ -z "$scanner" ] && [ -f "$root/.claude/code-map.json" ]; then
  scanner="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("scanner") or "")' "$root/.claude/code-map.json" 2>/dev/null || true)"
fi
if [ -z "$scanner" ]; then
  if [ -f "$root/config/routes.rb" ] && [ -d "$root/app/controllers" ]; then
    scanner="rails"
  else
    scanner="generic"
  fi
fi

case "$scanner" in
  rails)   script="scan_code_map.py" ;;
  generic) script="scan_generic.py" ;;
  *) echo "--scanner は rails か generic: $scanner" >&2; exit 1 ;;
esac

exec env PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/${script}" ${args[@]+"${args[@]}"}
