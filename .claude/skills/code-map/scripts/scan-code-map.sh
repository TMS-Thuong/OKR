#!/usr/bin/env bash
#
# scan-code-map.sh - Rails コードベースの構造情報を JSON に書き出す（KR1 step 1）
#
# 実処理は同ディレクトリの scan_code_map.rb にある。ネストした JSON の生成と
# マージが中心のため、bash + jq ではなく Ruby で実装している。
# Ruby は本リポジトリの必須依存であり、stdlib のみ使用するので
# bundler / docker は不要。
#
# 使い方:
#   scripts/scan-code-map.sh                       # 全体スキャン
#   scripts/scan-code-map.sh --since <commit>      # 差分のみ再読込
#   scripts/scan-code-map.sh --help                # オプション一覧

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v ruby >/dev/null 2>&1; then
  echo "ruby が見つかりません。Ruby がインストールされた環境で実行してください。" >&2
  exit 1
fi

exec ruby "${SCRIPT_DIR}/scan_code_map.rb" "$@"
