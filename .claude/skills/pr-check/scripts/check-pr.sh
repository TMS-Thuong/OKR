#!/usr/bin/env bash
#
# check-pr.sh - PR の変更を機械的に点検する（KR2 step 3）
#
# 実処理は同ディレクトリの check_pr.py にある。
#   入力: .ai/code-map/raw/pr-diff.json（必須）, .ai/code-map/analysis/blast-radius.json（任意）
#   出力: .ai/code-map/analysis/pr-check.json
#
# 使い方:
#   scripts/check-pr.sh
#   scripts/check-pr.sh --quiet
#   scripts/check-pr.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Python 3 がインストールされた環境で実行してください。" >&2
  exit 1
fi

exec env PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/check_pr.py" "$@"
