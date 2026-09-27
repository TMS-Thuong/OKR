#!/usr/bin/env python3
# build_pr_report.py - KR2 step 4: 3つの JSON を 1本のレビュー報告にまとめる
#
#   入力: .ai/code-map/raw/pr-diff.json          (必須)
#         .ai/code-map/analysis/blast-radius.json (任意・無いと影響範囲の節が落ちる)
#         .ai/code-map/analysis/pr-check.json     (任意・無いと指摘の節が落ちる)
#         --verdicts <json>                       (任意・Claude が読んで付けた判定)
#   出力: .ai/code-map/analysis/pr-report.json
#         .ai/code-map/doc/pr-report.md
#
# 標準ライブラリのみ。Python 3.8 以降で動く記法に限る。

import argparse
from datetime import datetime, timezone
import json
import os
import sys

TOOL_VERSION = '1.0.0'
PR_DIFF_MAJOR = '3'

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
OUT_DIR = os.path.join(ROOT, '.ai', 'code-map')

DIFF_PATH = os.path.join(OUT_DIR, 'raw', 'pr-diff.json')
BLAST_PATH = os.path.join(OUT_DIR, 'analysis', 'blast-radius.json')
CHECK_PATH = os.path.join(OUT_DIR, 'analysis', 'pr-check.json')
JSON_OUT = os.path.join(OUT_DIR, 'analysis', 'pr-report.json')
MD_OUT = os.path.join(OUT_DIR, 'doc', 'pr-report.md')

RISK_RANK = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, None: 4}
SEVERITY_RANK = {'error': 0, 'warning': 1, 'info': 2}

# 判定のしきい値。ここを読めば「なぜ高リスクなのか」が全部わかるようにしておく。
# 数字を変えるならこの表と REASONS の文言を一緒に直すこと。
WIDE_RISKS = ('critical', 'high')

T = {
    'vi': {
        'title': 'Báo cáo review PR',
        'verdict': 'Kết luận',
        'risk_high': 'RỦI RO CAO — cần senior đọc kỹ trước khi merge',
        'risk_medium': 'RỦI RO VỪA — đọc các mục bên dưới rồi merge',
        'risk_low': 'RỦI RO THẤP — review thường là đủ',
        'risk_unknown': 'CHƯA KẾT LUẬN ĐƯỢC — thiếu dữ liệu, không được coi là an toàn',
        'scope': 'Phạm vi thay đổi',
        'blast': 'Phạm vi ảnh hưởng',
        'findings': 'Phát hiện',
        'order': 'Đọc theo thứ tự này',
        'todo': 'Việc người review phải tự quyết',
        'limits': 'Giới hạn của báo cáo này',
        'none': 'Không có.',
        'file': 'File', 'risk': 'Rủi ro', 'screens': 'Màn hình chạm tới',
        'refs': 'File tham chiếu', 'layer': 'Tầng', 'lines': 'Dòng',
        'sev': 'Mức', 'check': 'Loại', 'where': 'Chỗ', 'what': 'Nội dung',
        'verdict_col': 'Xác nhận',
        'unconfirmed': 'chưa đọc',
        'scope_line': '- %s file, +%s / -%s, %s symbol',
        'stale_diff': 'Diff có thể cũ: HEAD đã đổi sau khi chạy step 1. Chạy lại `/review-pr`.',
        'stale_map': 'Bản đồ KR1 quét ở commit khác với PR này. Chạy `/map --refresh` để số màn hình chính xác.',
        'no_blast': 'Chưa chạy blast-radius, nên không có phần phạm vi ảnh hưởng.',
        'no_check': 'Chưa chạy pr-check, nên không có phần phát hiện.',
    },
    'en': {
        'title': 'PR review report',
        'verdict': 'Verdict',
        'risk_high': 'HIGH RISK — a senior should read this before merge',
        'risk_medium': 'MEDIUM RISK — read the sections below, then merge',
        'risk_low': 'LOW RISK — a normal review is enough',
        'risk_unknown': 'NO VERDICT — data is missing; do not read this as safe',
        'scope': 'What changed',
        'blast': 'Blast radius',
        'findings': 'Findings',
        'order': 'Read in this order',
        'todo': 'Left to the reviewer',
        'limits': 'Limits of this report',
        'none': 'None.',
        'file': 'File', 'risk': 'Risk', 'screens': 'Screens reached',
        'refs': 'Referencing files', 'layer': 'Layer', 'lines': 'Lines',
        'sev': 'Severity', 'check': 'Check', 'where': 'Where', 'what': 'What',
        'verdict_col': 'Confirmed',
        'unconfirmed': 'not read yet',
        'scope_line': '- %s files, +%s / -%s, %s symbols',
        'stale_diff': 'The diff may be stale: HEAD moved after step 1. Re-run `/review-pr`.',
        'stale_map': 'The KR1 map was scanned at a different commit. Run `/map --refresh` for exact screen counts.',
        'no_blast': 'blast-radius was not run, so there is no blast radius section.',
        'no_check': 'pr-check was not run, so there is no findings section.',
    },
}

GROUP_LABEL = {
    'vi': {'breaks': 'Phá chỗ khác', 'duplicate': 'Code lặp / code chết', 'rules': 'Sai quy chuẩn'},
    'en': {'breaks': 'Breaks other code', 'duplicate': 'Duplicate / dead code', 'rules': 'Rule violations'},
}

REASONS = {
    'vi': {
        'errors': 'có {n} lỗi chặn (error)',
        'wide_and_warn': 'đụng file dùng chung mức {risk} và có {n} cảnh báo',
        'rewritten': 'viết lại hẳn {n} màn hình',
        'wide': 'đụng file dùng chung mức {risk}',
        'warnings': 'có {n} cảnh báo (warning)',
        'clean': 'không có lỗi, không đụng file dùng chung',
        'no_check': 'chưa chạy pr-check nên chưa biết có vi phạm hay không',
        'no_blast': 'chưa chạy blast-radius nên chưa biết đụng tới đâu',
    },
    'en': {
        'errors': '{n} blocking error(s)',
        'wide_and_warn': 'touches a {risk}-risk shared file and has {n} warnings',
        'rewritten': '{n} screen(s) rewritten outright',
        'wide': 'touches a {risk}-risk shared file',
        'warnings': '{n} warning(s)',
        'clean': 'no errors and no shared files touched',
        'no_check': 'pr-check was not run, so rule violations are unknown',
        'no_blast': 'blast-radius was not run, so the reach is unknown',
    },
}


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load(path, required, what):
    if not os.path.exists(path):
        if required:
            die('%s が見つかりません。先に %s を実行してください。' % (path, what))
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def verdict(blast, check, lang):
    """総合判定。しきい値は WIDE_RISKS と下の分岐がすべて。"""
    st = (check or {}).get('stats', {})
    bs = (blast or {}).get('stats', {})
    errors = st.get('errors', 0)
    warnings = st.get('warnings', 0)
    pr_risk = bs.get('pr_risk')
    rewritten = bs.get('screens_rewritten', 0)
    r = REASONS[lang]
    reasons = []

    # pr-check を回していないのに「低リスク」と言うと、点検していないことが
    # 点検して問題なしだったことに化ける。判定そのものを保留する。
    if check is None:
        reasons.append(r['no_check'])
        if blast is None:
            reasons.append(r['no_blast'])
        elif pr_risk in WIDE_RISKS:
            reasons.append(r['wide'].format(risk=pr_risk))
        if rewritten:
            reasons.append(r['rewritten'].format(n=rewritten))
        return 'unknown', reasons

    if blast is None:
        reasons.append(r['no_blast'])

    if errors:
        level = 'high'
        reasons.append(r['errors'].format(n=errors))
    elif pr_risk in WIDE_RISKS and warnings:
        level = 'high'
        reasons.append(r['wide_and_warn'].format(risk=pr_risk, n=warnings))
    elif pr_risk in WIDE_RISKS or warnings:
        level = 'medium'
        if pr_risk in WIDE_RISKS:
            reasons.append(r['wide'].format(risk=pr_risk))
        if warnings:
            reasons.append(r['warnings'].format(n=warnings))
    else:
        level = 'low'
        reasons.append(r['clean'])

    if rewritten:
        reasons.append(r['rewritten'].format(n=rewritten))
    return level, reasons


def read_order(blast, check):
    """レビューを読む順。共有度の高いファイルと、指摘の多いファイルを先頭に出す。"""
    per_file = {}
    for f in (check or {}).get('findings', []):
        if not f.get('file'):
            continue
        b = per_file.setdefault(f['file'], {'error': 0, 'warning': 0, 'info': 0})
        b[f['severity']] = b.get(f['severity'], 0) + 1

    rows = []
    for f in (blast or {}).get('files', []):
        if f.get('non_code'):
            continue
        c = per_file.pop(f['path'], {})
        rows.append({
            'file': f['path'],
            'risk': f.get('risk'),
            'layer': f.get('layer'),
            'screens_reached': f.get('screens_reached'),
            'referenced_by_files': f.get('referenced_by_files'),
            'lines_added': f.get('lines_added'),
            'lines_removed': f.get('lines_removed'),
            'errors': c.get('error', 0),
            'warnings': c.get('warning', 0),
            'infos': c.get('info', 0),
        })
    # blast-radius に出てこないファイル（spec など）でも指摘があれば必ず載せる
    for path, c in per_file.items():
        rows.append({
            'file': path, 'risk': None, 'layer': None, 'screens_reached': None,
            'referenced_by_files': None, 'lines_added': None, 'lines_removed': None,
            'errors': c.get('error', 0), 'warnings': c.get('warning', 0), 'infos': c.get('info', 0),
        })

    rows.sort(key=lambda r: (
        -r['errors'], RISK_RANK.get(r['risk'], 4), -(r['screens_reached'] or 0),
        -r['warnings'], r['file']))
    return rows


def md_table(head, rows):
    if not rows:
        return ''
    out = ['| ' + ' | '.join(head) + ' |', '|' + '|'.join(['---'] * len(head)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join('' if c is None else str(c) for c in r) + ' |')
    return '\n'.join(out)


def build_md(report, lang):
    t = T[lang]
    gl = GROUP_LABEL[lang]
    m = report['meta']
    L = []
    # detached HEAD だと branch は 'HEAD' になる。表題に出しても何も指さない
    branch = m.get('branch')
    L.append('# %s — %s' % (t['title'], (branch if branch and branch != 'HEAD' else (m.get('head_sha') or '')[:9])))
    L.append('')
    L.append('`%s` → `%s` · %s · %s' % (
        (m.get('base') or '')[:9], (m.get('head_sha') or '')[:9],
        m['generated_at'], 'pr-report %s' % TOOL_VERSION))
    L.append('')

    for w in report['warnings']:
        L.append('> ⚠️ %s' % w)
    if report['warnings']:
        L.append('')

    L.append('## %s' % t['verdict'])
    L.append('')
    L.append('**%s**' % t['risk_' + report['verdict']['level']])
    L.append('')
    for r in report['verdict']['reasons']:
        L.append('- %s' % r)
    L.append('')

    s = report['scope']
    L.append('## %s' % t['scope'])
    L.append('')
    L.append(t['scope_line'] % (
        s['files_changed'], s['lines_added'], s['lines_removed'], s['symbols_touched']))
    if report['blast']:
        b = report['blast']
        L.append('- %s/%s màn hình có thể chạm tới (%s%%)' % (
            b['screens_at_risk'], b['screens_total'], round((b['screen_risk_ratio'] or 0) * 100)) if lang == 'vi'
            else '- %s/%s screens can reach the change (%s%%)' % (
                b['screens_at_risk'], b['screens_total'], round((b['screen_risk_ratio'] or 0) * 100)))
        if b['packs_at_risk']:
            L.append('- pack: %s' % ', '.join(b['packs_at_risk']))
    L.append('')

    L.append('## %s' % t['order'])
    L.append('')
    rows = [[
        '`%s`' % r['file'], r['risk'] or '-', r['layer'] or '-',
        r['screens_reached'] if r['screens_reached'] is not None else '-',
        '+%s/-%s' % (r['lines_added'], r['lines_removed']) if r['lines_added'] is not None else '-',
        '%s / %s / %s' % (r['errors'], r['warnings'], r['infos']),
    ] for r in report['read_order']]
    L.append(md_table([t['file'], t['risk'], t['layer'], t['screens'], t['lines'], 'E / W / I'], rows)
             or t['none'])
    L.append('')

    L.append('## %s' % t['findings'])
    L.append('')
    if not report['findings']:
        L.append(t['none'])
        L.append('')
    for group, items in report['findings'].items():
        L.append('### %s (%s)' % (gl.get(group, group), len(items)))
        L.append('')
        rows = []
        for f in items:
            where = '`%s`' % f['file'] + (':%s' % f['line'] if f['line'] else '')
            v = f.get('verdict') or t['unconfirmed']
            if f.get('verdict') and f.get('note'):
                v = '%s — %s' % (v, f['note'])
            rows.append([
                f['severity'], '`%s`' % f['check'], where,
                f['message'].get(lang) or f['message'].get('en', ''),
                v,
            ])
        L.append(md_table([t['sev'], t['check'], t['where'], t['what'], t['verdict_col']], rows))
        L.append('')

    L.append('## %s' % t['limits'])
    L.append('')
    for line in report['limits']:
        L.append('- %s' % line)
    L.append('')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='KR2 step 4: gộp pr-diff / blast-radius / pr-check thành 1 báo cáo')
    ap.add_argument('--lang', choices=('vi', 'en'), default='vi')
    ap.add_argument('--verdicts', help='JSON {"<finding id>": {"verdict": "...", "note": "..."}}')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    lang = args.lang
    t = T[lang]

    diff = load(DIFF_PATH, True, '.claude/skills/pr-diff/scripts/scan-pr-diff.sh')
    tv = str(diff.get('meta', {}).get('tool_version', ''))
    if tv.split('.')[0] != PR_DIFF_MAJOR:
        die('pr-diff.json のバージョン %s はこのツール (期待: %s.x) と噛み合いません。step 1 を再実行してください。'
            % (tv, PR_DIFF_MAJOR))
    blast = load(BLAST_PATH, False, None)
    check = load(CHECK_PATH, False, None)

    verdicts = {}
    if args.verdicts:
        with open(args.verdicts, encoding='utf-8') as f:
            verdicts = json.load(f)

    warnings = []
    if (check or {}).get('meta', {}).get('diff_possibly_stale'):
        warnings.append(t['stale_diff'])
    if (blast or {}).get('meta', {}).get('map_possibly_stale'):
        warnings.append(t['stale_map'])
    if blast is None:
        warnings.append(t['no_blast'])
    if check is None:
        warnings.append(t['no_check'])

    level, reasons = verdict(blast, check, lang)

    findings = {}
    for f in (check or {}).get('findings', []):
        row = dict(f)
        v = verdicts.get(f['id'])
        if v:
            row['verdict'] = v.get('verdict')
            row['note'] = v.get('note')
        findings.setdefault(f['group'], []).append(row)
    for group in findings:
        findings[group].sort(key=lambda f: (SEVERITY_RANK.get(f['severity'], 9), f.get('file') or '', f.get('line') or 0))
    findings = dict(sorted(findings.items(), key=lambda kv: min(SEVERITY_RANK.get(f['severity'], 9) for f in kv[1])))

    dm = diff['meta']
    ds = diff['stats']
    bs = (blast or {}).get('stats', {})

    limits = [
        'blast-radius đếm màn hình bằng tham chiếu tên (static), không chạy code: dynamic dispatch bị bỏ sót.'
        if lang == 'vi' else
        'blast-radius counts screens from name-based static references; dynamic dispatch is missed.',
        'pr-check là grep + so sánh base/head, không phải type system: không có phát hiện ≠ an toàn.'
        if lang == 'vi' else
        'pr-check is grep plus a base/head comparison, not a type system: no finding does not mean safe.',
        'Không chạy lint / test / type-check — phần đó là việc của CI.'
        if lang == 'vi' else
        'No lint, test or type-check is run here — that is CI\'s job.',
        'Lỗi logic (`>` thành `>=`) nằm ngoài tầm của mọi bước máy; phải người đọc.'
        if lang == 'vi' else
        'Logic mistakes (`>` vs `>=`) are out of reach for every machine step; a human must read them.',
    ]

    report = {
        'meta': {
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'tool_version': TOOL_VERSION,
            'branch': dm.get('branch'),
            'base': dm.get('base'),
            'head_sha': dm.get('head_sha'),
            'pr': dm.get('pr'),
            'lang': lang,
            'sources': {
                'pr_diff': dm.get('generated_at'),
                'blast_radius': (blast or {}).get('meta', {}).get('generated_at'),
                'pr_check': (check or {}).get('meta', {}).get('generated_at'),
            },
        },
        'verdict': {'level': level, 'reasons': reasons},
        'warnings': warnings,
        'scope': {
            'files_changed': ds.get('files_changed'),
            'files_added': ds.get('files_added'),
            'files_modified': ds.get('files_modified'),
            'lines_added': ds.get('lines_added'),
            'lines_removed': ds.get('lines_removed'),
            'symbols_touched': ds.get('symbols_touched'),
        },
        'blast': {
            'screens_at_risk': bs.get('screens_at_risk'),
            'screens_total': bs.get('screens_total'),
            'screen_risk_ratio': bs.get('screen_risk_ratio'),
            'screens_rewritten': bs.get('screens_rewritten'),
            'hubs_touched': bs.get('hubs_touched'),
            'packs_at_risk': bs.get('packs_at_risk') or [],
            'file_risks': bs.get('file_risks') or {},
            'pr_risk': bs.get('pr_risk'),
        } if blast else None,
        'stats': (check or {}).get('stats'),
        'read_order': read_order(blast, check),
        'findings': findings,
        'limits': limits,
    }

    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    os.makedirs(os.path.dirname(MD_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open(MD_OUT, 'w', encoding='utf-8') as f:
        f.write(build_md(report, lang))

    if not args.quiet:
        st = report['stats'] or {}
        print('[report] %s — %s' % (level.upper(), '; '.join(reasons)))
        print('[report] %s file, +%s/-%s · error %s / warning %s / info %s' % (
            report['scope']['files_changed'], report['scope']['lines_added'],
            report['scope']['lines_removed'], st.get('errors', 0), st.get('warnings', 0), st.get('infos', 0)))
        for w in warnings:
            print('[report] ! %s' % w)
        print('[report] %s' % os.path.relpath(MD_OUT, ROOT))
        print('[report] %s' % os.path.relpath(JSON_OUT, ROOT))


if __name__ == '__main__':
    main()
