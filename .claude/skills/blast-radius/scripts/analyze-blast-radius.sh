#!/usr/bin/env bash
#
# analyze-blast-radius.sh - 変更 × 影響マップ の突き合わせ（KR2 step 2）
#
# 実処理は同ディレクトリの analyze_blast_radius.py にある。
# 入力は JSON 2 本のみ。ソースも git も読まない。
#   .ai/code-map/raw/pr-diff.json      … KR2 step 1 の出力
#   .ai/code-map/analysis/impact.json  … KR1 step 2 の出力
#
# 使い方:
#   scripts/analyze-blast-radius.sh
#   scripts/analyze-blast-radius.sh --diff path/to/pr-diff.json
#   scripts/analyze-blast-radius.sh --impact path/to/impact.json
#   scripts/analyze-blast-radius.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Python 3 がインストールされた環境で実行してください。" >&2
  exit 1
fi

exec python3 "${SCRIPT_DIR}/analyze_blast_radius.py" "$@"
