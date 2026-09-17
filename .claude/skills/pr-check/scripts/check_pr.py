#!/usr/bin/env python3
# check_pr.py - PR の変更を点検する（KR2 step 3）
#
# 入力: .ai/code-map/raw/pr-diff.json（必須） / .ai/code-map/analysis/blast-radius.json（任意）/ git の base 版と作業ツリー
# 出力: .ai/code-map/analysis/pr-check.json
#
# 出すのは「根拠つきの疑い」で、確定ではない。確定は SKILL.md の手順で Claude がソースを読んで行う。
#   checks_breaks.py    … 他の箇所を壊していないか
#   checks_duplicate.py … 重複・不要なコード
#   checks_rules.py     … 規約違反
#
# 標準ライブラリのみ。Python 3.8 以降で動く記法に限る。

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checks_breaks  # noqa: E402
import checks_duplicate  # noqa: E402
import checks_rules  # noqa: E402
from common import RISK_RANK, Change, Corpus, Findings, die, run  # noqa: E402

TOOL_VERSION = '2.0.0'
PR_DIFF_MAJOR = '3'


def load(path, required):
    if not os.path.exists(path):
        if required:
            die('見つかりません: {}\n  先に .claude/skills/pr-diff/scripts/scan-pr-diff.sh を実行してください。'.format(path))
        return None
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def main():
    p = argparse.ArgumentParser(description='PR の変更を点検する（KR2 step 3）')
    p.add_argument('--diff', default='.ai/code-map/raw/pr-diff.json')
    p.add_argument('--blast', default='.ai/code-map/analysis/blast-radius.json')
    p.add_argument('--out', default='.ai/code-map/analysis/pr-check.json')
    p.add_argument('--quiet', action='store_true')
    options = p.parse_args()

    root, ok = run('git', 'rev-parse', '--show-toplevel')
    if not ok:
        die('git リポジトリの中で実行してください。')
    os.chdir(root.strip())

    diff = load(options.diff, True)
    meta = diff['meta']
    if str(meta.get('tool_version', '')).split('.')[0] != PR_DIFF_MAJOR:
        die('pr-diff.json の形式が古いです。pr-diff を再実行してください。')
    blast = load(options.blast, False) or {}
    risk_by_file = {f['path']: f['risk'] for f in blast.get('files') or [] if f.get('risk') in RISK_RANK}

    started = time.time()
    base_ref = meta.get('base') if meta.get('mode') == 'branch' else 'HEAD'
    corpus = Corpus()
    changes = [Change(e, base_ref) for e in diff.get('files') or []]
    pr_added = {c.path: c.added for c in changes}

    commits = []
    if meta.get('mode') == 'branch' and meta.get('base'):
        out, ok = run('git', 'log', '--no-merges', '--format=%H%x09%s', '{}..{}'.format(meta['base'], meta.get('head_sha') or 'HEAD'))
        commits = [tuple(l.split('\t', 1)) for l in out.strip().split('\n') if '\t' in l] if ok else []

    # HEAD が diff を取った時点から動いていると行番号がずれる
    head_now, _ = run('git', 'rev-parse', 'HEAD')
    stale = meta.get('mode') == 'branch' and meta.get('head_sha') not in (None, head_now.strip())

    found = Findings(risk_by_file)
    checks_breaks.run_all(changes, corpus, found, pr_added)
    checks_duplicate.run_all(changes, corpus, found, pr_added)
    checks_rules.run_all(changes, corpus, found, commits)
    items = found.sorted()

    severity = Counter(f['severity'] for f in items)
    result = {
        'meta': {
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'tool_version': TOOL_VERSION,
            'diff_head_sha': meta.get('head_sha'),
            'branch': meta.get('branch'),
            'blast_radius_used': bool(blast),
            'diff_possibly_stale': bool(stale),
            'files_checked': len(changes),
            'seconds': round(time.time() - started, 1),
        },
        'stats': {
            'findings': len(items),
            'errors': severity['error'],
            'warnings': severity['warning'],
            'infos': severity['info'],
            'by_group': dict(Counter(f['group'] for f in items)),
            'by_check': dict(sorted(Counter(f['check'] for f in items).items())),
        },
        'findings': items,
    }
    os.makedirs(os.path.dirname(options.out), exist_ok=True)
    with open(options.out, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write('\n')

    if not options.quiet:
        if stale:
            print('注意: pr-diff.json の head と現在の HEAD が違います。pr-diff を再実行してください。', file=sys.stderr)
        print('{} 件（error {}, warning {}, info {}）{} 秒 → {}'.format(
            len(items), severity['error'], severity['warning'], severity['info'], result['meta']['seconds'], options.out),
            file=sys.stderr)


if __name__ == '__main__':
    main()
