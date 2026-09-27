#!/usr/bin/env bash
#
# build-pr-report.sh - PR レビュー報告を1本にまとめる（KR2 step 4）
#
# 実処理は同ディレクトリの build_pr_report.py にある。
#   入力: .ai/code-map/raw/pr-diff.json（必須）
#         .ai/code-map/analysis/blast-radius.json, pr-check.json（任意）
#   出力: .ai/code-map/analysis/pr-report.json, .ai/code-map/doc/pr-report.md
#
# 使い方:
#   scripts/build-pr-report.sh
#   scripts/build-pr-report.sh --lang en
#   scripts/build-pr-report.sh --verdicts /tmp/verdicts.json
#   scripts/build-pr-report.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Python 3 がインストールされた環境で実行してください。" >&2
  exit 1
fi

exec env PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/build_pr_report.py" "$@"
