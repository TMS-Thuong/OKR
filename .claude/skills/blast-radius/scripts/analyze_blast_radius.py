#!/usr/bin/env python3
# analyze_blast_radius.py - 変更 × 影響マップ の突き合わせ（KR2 step 2）
#
# 入力は JSON 2 本だけ。ソースも git も一切読まない。
#   .ai/code-map/raw/pr-diff.json        … KR2 step 1: 何が変わったか
#   .ai/code-map/analysis/impact.json    … KR1 step 2: 画面 → ファイル
#
# KR1 は「画面 → ファイル」を答える。ここはそれを逆に辿って
# 「このファイルが変わった → どの画面が危ないか」を出す。
#
# 良し悪しの判断はしない。「重要な所に当たっているか」までが仕事で、
# 「壊れているか」は step 3 が判断する。
#
# 標準ライブラリのみ。Python 3.8 以降で動く記法に限る。

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

TOOL_VERSION = '3.0.0'

# ファイル単位の危険度。しきい値は「画面の何割に届くか」で決める。
# 実数ではなく割合にしているのは、リポジトリが大きくなっても意味が変わらないため。
TIER_RULES = [
    ('critical', 0.30),
    ('high', 0.05),
    ('medium', 0.01),
]

# レビューの読み物としては雑音になりやすい拡張子。
# 除外はしない（消えたら消えたで困る）が、印だけ付けて step 3 に判断させる。
NON_CODE_EXT = ['.md', '.txt', '.json', '.yml', '.yaml', '.lock']

# KR1 がそもそも走査しない場所。ここのファイルが地図に無いのは当たり前で、
# 「新規ファイル」でも「地図が古い」でもない。混ぜると check_manually が水増しされ、
# step 3 が本当に開くべきファイルを見失う。
OUT_OF_SCOPE = [
    re.compile(r'\Aspec/'), re.compile(r'/spec/'), re.compile(r'\Atest/'),
    re.compile(r'\Adoc/'), re.compile(r'\Adocs/'), re.compile(r'\Adb/'),
    re.compile(r'\A\.github/'), re.compile(r'\A\.claude/'), re.compile(r'\Abin/'),
    re.compile(r'\Anode_modules/'), re.compile(r'\Avendor/'), re.compile(r'\Apublic/'),
]

TIER_RANK = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1, 'none': 0, 'unknown': 0}


def line_count(ranges):
    """step 1 の from/to の並びが何行あるかを数える。両端を含む。"""
    return sum(r['to'] - r['from'] + 1 for r in (ranges or []))


def out_of_scope(path):
    return any(rx.search(path) for rx in OUT_OF_SCOPE)


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load_json(path, what, hint):
    if not os.path.exists(path):
        die('{} が見つかりません: {}\n  {}'.format(what, path, hint))
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except ValueError as e:
        die('{} が壊れています: {}\n  {}'.format(what, path, e))


def tier_for(ratio, hub):
    for name, threshold in TIER_RULES:
        if ratio >= threshold:
            return name
    if hub:  # 割合は低いが fan-in が異常なファイル
        return 'high'
    return 'low' if ratio > 0 else 'none'


def to_f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_args():
    p = argparse.ArgumentParser(
        prog='analyze_blast_radius.py',
        description='変更 × 影響マップ の突き合わせ（KR2 step 2）',
    )
    p.add_argument('--diff', help='pr-diff.json のパス')
    p.add_argument('--impact', help='impact.json のパス')
    p.add_argument('--out', help='出力先 JSON')
    p.add_argument('--root', default=os.getcwd(), help='リポジトリルート')
    return p.parse_args()


def main():
    options = parse_args()

    root = os.path.abspath(options.root)
    diff_path = os.path.abspath(
        options.diff or os.path.join(root, '.ai/code-map/raw/pr-diff.json'))
    impact_path = os.path.abspath(
        options.impact or os.path.join(root, '.ai/code-map/analysis/impact.json'))
    out_path = os.path.abspath(
        options.out or os.path.join(root, '.ai/code-map/analysis/blast-radius.json'))

    diff = load_json(
        diff_path, 'pr-diff.json',
        '先に .claude/skills/pr-diff/scripts/scan-pr-diff.sh を実行してください。')
    impact = load_json(
        impact_path, 'impact.json',
        '先に .claude/skills/impact-analysis/scripts/analyze-impact.sh を実行してください。')

    diff_meta = diff.get('meta') or {}
    impact_meta = impact.get('meta') or {}

    # step 1 の出力形式が変わったら、黙って誤読するより止める。
    # メジャー番号が上がるのは files[] のキーが変わったときだけ。
    diff_version = str(diff_meta.get('tool_version') or '0.0.0')
    if diff_version.split('.')[0] != TOOL_VERSION.split('.')[0]:
        die('pr-diff.json の形式が古すぎます (tool_version {} / 必要 {}.x)。\n'
            '  .claude/skills/pr-diff/scripts/scan-pr-diff.sh を実行し直してください。'
            .format(diff_version, TOOL_VERSION.split('.')[0]))
    total_screens = int((impact.get('stats') or {}).get('screens') or 0)

    # ------------------------------------------------------------ 索引を作る

    # ファイル → そのファイルに届く画面。
    #
    # shared_files[].screens は先頭 20 件で打ち切られている（KR1 が
    # screens_truncated で申告している）。screen_count だけは正しいが、
    # id の一覧は欠けるので、ここを逆引きに使ってはいけない。
    # 打ち切りのないのは screens[].code_files の方で、こちらは
    # code_file_count と件数が一致することを確認済み。
    reached = {}
    for f in impact.get('shared_files') or []:
        reached[f['file']] = f

    # 画面が持つファイルは code_files だけではない。erb は views、
    # Vue の入口は vue_page に入る。3 つ揃えると shared_files の
    # screen_count と全 2338 件で一致する（controller_file は
    # code_files の depth 0 として既に含まれる）。
    screens_by_file = defaultdict(list)
    for s in impact.get('screens') or []:
        files = [cf['file'] for cf in s.get('code_files') or []]
        files.extend(s.get('views') or [])
        if s.get('vue_page'):
            files.append(s['vue_page'])
        for path in files:
            screens_by_file[path].append(s['id'])

    unreached = {}
    for f in impact.get('unreached_files') or []:
        # 形式が文字列でもハッシュでも拾えるようにしておく
        path = f['file'] if isinstance(f, dict) else f
        unreached[path] = f

    screens_by_id = {}
    screen_by_qualified = {}
    for s in impact.get('screens') or []:
        screens_by_id[s['id']] = s
        # `Web::ItemsController#create` を画面 id に直す表。step 1 が出す qualified 名は
        # KR1 の controller_class と同じ語彙なので、そのまま突き合う。
        klass = s.get('controller_class')
        if klass:
            screen_by_qualified['{}#{}'.format(klass, s.get('action'))] = s['id']

    # ------------------------------------------------------------ 突き合わせ

    files_out = []
    # 画面 id → その画面に届いた変更ファイル
    screen_hits = defaultdict(list)
    # 画面 id → 直接書き換えられた action
    direct_hits = defaultdict(list)

    for f in diff.get('files') or []:
        path = f['path']
        # リネームされたファイルは、地図が作られた時点の名前で引く。
        # 新しい名前で引くと必ず「地図に無い」になり、影響が丸ごと消える。
        if f.get('status') == 'renamed' and f.get('old_path'):
            lookup = f['old_path']
        else:
            lookup = path

        hit = reached.get(lookup)
        ext = os.path.splitext(path)[1].lower()

        entry = {
            'path': path,
            'lookup_path': None if lookup == path else lookup,
            'status': f.get('status'),
            'lang': f.get('lang'),
            'lines_added': line_count(f.get('added')),
            'lines_removed': line_count(f.get('removed')),
            'symbols': dict(f.get('symbols') or {}),
            'non_code': ext in NON_CODE_EXT,
        }

        if hit:
            ratio = to_f(hit.get('screen_ratio'))
            ids = screens_by_file.get(lookup) or []
            entry['map_status'] = 'mapped'
            entry['layer'] = hit.get('bucket')
            entry['screens_reached'] = hit.get('screen_count')
            entry['screens_reached_ratio'] = ratio
            entry['hub'] = bool(hit.get('hub'))
            entry['referenced_by_files'] = hit.get('raw_fan_in')
            entry['risk'] = tier_for(ratio, hit.get('hub'))
            for sid in ids:
                screen_hits[sid].append(path)
        elif lookup in unreached:
            # 地図上どの画面からも辿り着かないファイル。デッドコードの疑いであって、
            # 断定ではない（KR1 は定数名で辿るので動的呼び出しは追えない）。
            entry['map_status'] = 'unused'
            entry['screens_reached'] = 0
            entry['screens_reached_ratio'] = 0.0
            entry['hub'] = False
            entry['risk'] = 'none'
        elif out_of_scope(lookup):
            # KR1 の走査対象外。地図に無くて正しいので、危険度は付けない。
            entry['map_status'] = 'not_scanned'
            entry['screens_reached'] = None
            entry['screens_reached_ratio'] = None
            entry['hub'] = False
            entry['risk'] = 'unknown'
        else:
            # 走査対象なのに地図に無い。この枝が新規追加したか、地図が古いか。
            entry['map_status'] = 'new_file' if f.get('status') == 'added' else 'check_manually'
            entry['screens_reached'] = None
            entry['screens_reached_ratio'] = None
            entry['hub'] = False
            entry['risk'] = 'unknown'

        # コントローラの action そのものが書き換わった場合は、
        # 到達経路を辿るまでもなくその画面が直接変わっている。
        # 照合するのは def と new_def。class / module の名前は画面の鍵
        # (Class#action) と形が違うので、引いても必ず外れる。
        # new_def も引く。新しい action を足せば、その画面は新設か作り替えになる。
        syms = f.get('symbols') or {}
        for qualified in (syms.get('def') or []) + (syms.get('new_def') or []):
            sid = screen_by_qualified.get(qualified)
            if not sid:
                continue
            direct_hits[sid].append(qualified)
            if path not in screen_hits[sid]:
                screen_hits[sid].append(path)

        files_out.append(entry)

    files_out.sort(key=lambda e: (
        -TIER_RANK.get(e['risk'], 0),
        -(e['screens_reached'] or 0),
        e['path'],
    ))

    # ------------------------------------------------------------ 危ない画面

    def uniq(seq):
        seen = set()
        out = []
        for item in seq:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    # 1 件あたりのキー数がそのまま出力サイズになる（画面は千件規模で並ぶ）。
    # 空の値はキーごと省く。読む側は既定値付きで読むこと。
    screens_out = []
    for sid, paths in screen_hits.items():
        s = screens_by_id.get(sid) or {}
        rewritten = sid in direct_hits
        entry = {'screen': sid}

        # 書き換わった画面は人が開いて確かめる先なので、素性を全部載せる。
        # ただ巻き込まれただけの画面は id だけで十分に指させる。
        if rewritten:
            entry['rewritten'] = True
            entry['rewritten_methods'] = uniq(direct_hits[sid])
            routes = s.get('routes') or []
            if routes:
                entry['route'] = '{} {}'.format(routes[0]['method'], routes[0]['path'])
        if s.get('pack'):
            entry['pack'] = s['pack']
        if s.get('vue_page'):
            entry['vue_page'] = s['vue_page']
        entry['changed_files'] = uniq(paths)
        screens_out.append(entry)

    # 直接書き換わった画面を先頭に。次に、多くの変更ファイルに触れている画面。
    screens_out.sort(
        key=lambda s: (0 if s.get('rewritten') else 1, -len(s['changed_files']), s['screen']))

    # ------------------------------------------------------------ 集計

    ranks = [TIER_RANK.get(e['risk'], 0) for e in files_out]
    top_tier = max(ranks) if ranks else 0
    tier_name = next((k for k, v in TIER_RANK.items() if v == top_tier), 'none')
    at_risk = len(screens_out)
    risk_ratio = round(at_risk / total_screens, 4) if total_screens > 0 else 0.0

    # unknown は「調べて安全だった」ではなく「そもそも点が付かない」。
    # 点数の分布に混ぜると critical 1 件の隣に 15 件並んで薄まる。
    # 内訳は files_not_scanned / files_check_manually / files_new が持つ。
    tier_counts = {}
    for e in files_out:
        if e['risk'] == 'unknown':
            continue
        tier_counts[e['risk']] = tier_counts.get(e['risk'], 0) + 1

    def count_status(name):
        return sum(1 for e in files_out if e['map_status'] == name)

    # 変更ファイルが属する pack ではなく、危ない画面が属する pack。
    # 共有ファイルを 1 行直しただけでも、その先の pack の画面は巻き込まれる。
    packs = sorted({s['pack'] for s in screens_out if s.get('pack')})

    stats = {
        'files_changed': len(files_out),
        'files_mapped': count_status('mapped'),
        'files_unused': count_status('unused'),
        'files_new': count_status('new_file'),
        'files_check_manually': count_status('check_manually'),
        'files_not_scanned': count_status('not_scanned'),
        'files_non_code': sum(1 for e in files_out if e['non_code']),
        'hubs_touched': sum(1 for e in files_out if e['hub']),
        'screens_at_risk': at_risk,
        'screens_rewritten': sum(1 for s in screens_out if s.get('rewritten')),
        'screens_total': total_screens,
        'screen_risk_ratio': risk_ratio,
        'packs_at_risk': packs,
        'file_risks': tier_counts,
        'pr_risk': tier_name,
    }

    # 地図が枝より古いと、この枝が触ったファイルが「地図に無い」側へ落ちる。
    # 判断材料として出すだけで、警告で止めはしない（古い地図でも大半は有効なため）。
    stale = bool(
        impact_meta.get('scan_commit')
        and diff_meta.get('head_sha')
        and impact_meta.get('scan_commit') != diff_meta.get('head_sha')
    )

    meta = {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tool_version': TOOL_VERSION,
        'diff_source': os.path.relpath(diff_path, root),
        'impact_source': os.path.relpath(impact_path, root),
        'diff_mode': diff_meta.get('mode'),
        'diff_base': diff_meta.get('base'),
        'diff_head_sha': diff_meta.get('head_sha'),
        'branch': diff_meta.get('branch'),
        'pr': diff_meta.get('pr'),
        'map_scan_commit': impact_meta.get('scan_commit'),
        'map_generated_at': impact_meta.get('generated_at'),
        'map_max_depth': impact_meta.get('max_depth'),
        'map_possibly_stale': stale,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(
            {'meta': meta, 'stats': stats, 'files': files_out, 'screens': screens_out},
            fh, ensure_ascii=False, indent=2)
        fh.write('\n')

    # ------------------------------------------------------------ 画面出力

    print('突き合わせ: {} × {}'.format(
        os.path.basename(diff_path), os.path.basename(impact_path)))
    print('出力: {}'.format(out_path))
    print()
    print('変更 {} ファイル / 影響画面 {} 件 (全 {} 画面の {}%) / 総合 {}'.format(
        stats['files_changed'], at_risk, total_screens,
        round(risk_ratio * 100, 1), tier_name))
    if stale:
        print('地図の基準コミットが差分の HEAD と違います（地図が古い可能性）: {}'.format(
            impact_meta.get('scan_commit')))
    print()

    shown = [e for e in files_out
             if e['map_status'] != 'not_scanned'
             and not (e['risk'] == 'unknown' and e['non_code'])]
    for e in shown[:15]:
        count = '-' if e['screens_reached'] is None else str(e['screens_reached'])
        mark = ' [hub]' if e['hub'] else ''
        print('  {:<9} {:<6} {:<58} {}{}'.format(
            e['risk'], count, e['path'][:58], e['map_status'], mark))
    if len(shown) > 15:
        print('  ... 他 {} ファイル'.format(len(shown) - 15))

    direct = [s for s in screens_out if s.get('rewritten')]
    if direct:
        print()
        print('直接書き換わった画面 {} 件:'.format(len(direct)))
        for s in direct[:10]:
            print('  {}  {}'.format(s['screen'], s.get('route') or ''))


if __name__ == '__main__':
    main()
