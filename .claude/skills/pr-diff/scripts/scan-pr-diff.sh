#!/usr/bin/env bash
#
# scan-pr-diff.sh - PR で変わった行を読み取る（KR2 step 1）
#
# 実処理は同ディレクトリの scan_pr_diff.py にある。
# 判断は一切せず、変更行と、その行を含む定義だけを JSON に落とす。
#
# 使い方:
#   scripts/scan-pr-diff.sh                # 分岐点(merge-base)から HEAD まで＝PR相当
#   scripts/scan-pr-diff.sh --working      # まだコミットしていない変更
#   scripts/scan-pr-diff.sh --staged       # git add 済み・未コミットの変更
#   scripts/scan-pr-diff.sh --pr 8415      # 差分に PR のタイトル等を添える
#   scripts/scan-pr-diff.sh --base v1.2.3  # 枝が master から分かれていない場合
#   scripts/scan-pr-diff.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Python 3 がインストールされた環境で実行してください。" >&2
  exit 1
fi

exec python3 "${SCRIPT_DIR}/scan_pr_diff.py" "$@"
