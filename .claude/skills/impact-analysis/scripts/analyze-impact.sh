#!/usr/bin/env bash
#
# analyze-impact.sh - scan.json を突き合わせて影響マップを作る（KR1 step 2）
#
# 実処理は同ディレクトリの analyze_code_map.rb にある。
# ソースは一切読まず、code-map skill が出した scan.json のみを入力とする。
#
# 使い方:
#   scripts/analyze-impact.sh                 # 既定パスで解析
#   scripts/analyze-impact.sh --max-depth 4   # 追跡するホップ数を変える
#   scripts/analyze-impact.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v ruby >/dev/null 2>&1; then
  echo "ruby が見つかりません。Ruby がインストールされた環境で実行してください。" >&2
  exit 1
fi

exec ruby "${SCRIPT_DIR}/analyze_code_map.rb" "$@"
