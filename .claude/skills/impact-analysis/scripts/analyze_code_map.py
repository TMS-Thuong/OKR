#!/usr/bin/env python3
# Impact analyser (KR1 step 2: compare and correlate).
#
# Input:  .ai/code-map/raw/scan.json      (produced by the code-map skill)
# Output: .ai/code-map/analysis/impact.json
#
# Turns the raw structural facts into the two answers KR1 asks for:
#   1. which code files each screen (controller#action) actually reaches
#   2. which files are shared across many screens
#
# It reads NO source files -- everything comes from scan.json. That is what
# makes it cheap enough to regenerate in full every time.
#
# Python stdlib only, and only syntax that runs on Python 3.8.

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

SCHEMA_VERSION = 1
EXPECTED_SCAN_SCHEMA = 1

ap = argparse.ArgumentParser(prog='analyze_code_map.py')
ap.add_argument('--scan', help='Input scan.json (default: .ai/code-map/raw/scan.json)')
ap.add_argument('--out', help='Output impact.json (default: .ai/code-map/analysis/impact.json)')
ap.add_argument('--max-depth', dest='max_depth', type=int, default=3,
                help='Reference hops to follow from a controller (default: 3)')
ap.add_argument('--hub-threshold', dest='hub_threshold', type=int, default=40,
                help='Fan-in above which a file is a hub and is not traversed through (default: 40)')
ap.add_argument('--quiet', action='store_true', help='Suppress the stderr summary')
options = ap.parse_args()

SCAN = os.path.abspath(options.scan or '.ai/code-map/raw/scan.json')
OUT = os.path.abspath(options.out or '.ai/code-map/analysis/impact.json')

if not os.path.exists(SCAN):
    sys.exit('scan not found: %s\nRun the code-map skill first.' % SCAN)

with open(SCAN, encoding='utf-8') as _f:
    scan = json.load(_f)
scan_meta = scan.get('meta') or {}

if scan_meta.get('schema_version') != EXPECTED_SCAN_SCHEMA:
    sys.exit('scan.json schema_version=%s, expected %d. Re-run a full code-map scan.'
             % (json.dumps(scan_meta.get('schema_version')), EXPECTED_SCAN_SCHEMA))

RUBY_BUCKETS = ['controllers', 'models', 'services', 'jobs', 'mailers', 'lib']


def ruby_round(x, ndigits):
    """Ruby の Float#round(n)。四捨五入（0から遠い方）で、Python の round() の銀行丸めとは違う。"""
    s = 10.0 ** ndigits
    xs = x * s
    f = float(Decimal(xs).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if x > 0:
        if (f + 0.5) / s <= x:
            f += 1
    else:
        if (f - 0.5) / s >= x:
            f -= 1
    return f / s


def uniq(items):
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

entry_by_file = {}
bucket_by_file = {}
for bucket in RUBY_BUCKETS:
    for e in scan.get(bucket) or []:
        entry_by_file[e['file']] = e
        bucket_by_file[e['file']] = bucket

_CTRL_PATH_RE = re.compile(r'app/controllers/(.+)_controller\.rb\Z')
controller_file_by_class = {}
controller_file_by_path = {}
for c in scan.get('controllers') or []:
    if c.get('class'):
        controller_file_by_class[c['class']] = c['file']
    # Path-based fallback. Custom inflections (PDFTemplatesController for
    # pdf_templates) mean the camelised class name from a route does not always
    # match, but the file path convention still holds.
    m = _CTRL_PATH_RE.search(c['file'])
    if m:
        controller_file_by_path[m.group(1)] = c['file']


def resolve_controller_file(route):
    """あいまいな後方一致はしない。`orders` は sales_orders にも purchase_orders にも合ってしまう。"""
    return controller_file_by_class.get(route.get('controller_class')) or \
        controller_file_by_path.get(route.get('controller'))


# Raw fan-in: how many scanned files name this file. Used to spot hubs.
fan_in = defaultdict(int)
for bucket in RUBY_BUCKETS:
    for e in scan.get(bucket) or []:
        for ref in e.get('references') or []:
            fan_in[ref] += 1

hub_files = set(f for f, n in fan_in.items() if n >= options.hub_threshold)

# Vue pages, keyed by the convention app.ts uses: pages/<controller path>/<action>.vue.
vue_page_by_key = {}
for f in scan.get('frontend_pages') or []:
    if f.get('page_key'):
        vue_page_by_key[f['page_key']] = f['file']

# Views, grouped by their controller hint so a screen can claim its templates.
views_by_controller = defaultdict(list)
view_by_file = {}
for v in scan.get('views') or []:
    view_by_file[v['file']] = v
    if v.get('controller_hint'):
        views_by_controller[v['controller_hint']].append(v)

# dir -> template basename -> files, so partial lookup is a hash hit.
view_lookup = defaultdict(lambda: defaultdict(list))
for v in scan.get('views') or []:
    if not v.get('controller_hint'):
        continue
    name = re.sub(r'\..*\Z', '', os.path.basename(v['file']), count=1, flags=re.S)
    view_lookup[v['controller_hint']][re.sub(r'\A_', '', name, count=1)].append(v['file'])


def resolve_partial(target, controller_hint):
    """`render 'shared/foo'` -> the partial file, so a screen also claims its partials."""
    if '/' in target:
        d = os.path.dirname(target)
        base = os.path.basename(target)
    else:
        d = controller_hint
        base = target
    if d is None or d not in view_lookup:
        return []
    return view_lookup[d][base]


def collect_views(controller_hint, action):
    direct = [v for v in views_by_controller[controller_hint] if v.get('action_hint') == action]
    seen = dict((v['file'], True) for v in direct)
    queue = list(direct)
    while queue:
        v = queue.pop(0)
        for target in v.get('renders') or []:
            for file in resolve_partial(target, controller_hint):
                if file in seen:
                    continue
                seen[file] = True
                if file in view_by_file:
                    queue.append(view_by_file[file])
    return sorted(seen)


def view_tree(controller_hint, action):
    """collect_views と同じ辿り方を木のまま残す。各 view の下に、それが描く partial が並ぶ。"""
    seen = set()

    def build(v, depth):
        kids = []
        if depth < 4:
            for target in v.get('renders') or []:
                for file in resolve_partial(target, controller_hint):
                    if file in seen:
                        continue
                    seen.add(file)
                    child = view_by_file.get(file)
                    node = {'file': file}
                    sub = build(child, depth + 1) if child else []
                    if sub:
                        node['renders'] = sub
                    kids.append(node)
        return kids

    out = []
    for v in [v for v in views_by_controller[controller_hint] if v.get('action_hint') == action]:
        seen.add(v['file'])
        node = {'file': v['file']}
        sub = build(v, 1)
        if sub:
            node['renders'] = sub
        out.append(node)
    return out


# ---------------------------------------------------------------------------
# Reachability from one controller file
# ---------------------------------------------------------------------------

def reachable_files(start_file, max_depth):
    """`references` を幅優先で辿る。ハブは「到達した」と記録するが先へは広げない。
    広げると、どの画面も推移的にアプリ全体に届いてしまう。"""
    depths = {start_file: 0}
    queue = [start_file]
    while queue:
        file = queue.pop(0)
        depth = depths[file]
        if depth >= max_depth:
            continue
        if depth > 0 and file in hub_files:
            continue
        entry = entry_by_file.get(file)
        if not entry:
            continue
        for ref in entry.get('references') or []:
            if ref in depths:
                continue
            depths[ref] = depth + 1
            queue.append(ref)
    return depths


# ---------------------------------------------------------------------------
# Call flow of one screen (who calls whom)
# ---------------------------------------------------------------------------

class_to_file = {}
for e in entry_by_file.values():
    if e.get('class'):
        class_to_file[e['class']] = e['file']


def superclass_file(entry):
    """継承元でしかないファイル（ApplicationRecord, BaseService…）は配管であって、流れの一段ではない。"""
    sup = entry.get('superclass') if entry else None
    if not sup:
        return None
    return class_to_file.get(sup) or class_to_file.get(sup.split('::')[-1])


# Kinds that are expanded further. A model is where the story ends ("which data
# does it touch"); following a model's own references would wander into every
# association in the schema.
FLOW_EXPAND = ['services', 'jobs', 'lib', 'controllers']
FLOW_MAX_DEPTH = 4
FLOW_MAX_CHILDREN = 40
_ERRORS_FILE_RE = re.compile(r'(\A|/)errors?\.rb\Z')


def call_flow(ctrl_entry, action):
    """1アクションを根にした木。各ファイルは一番浅い位置に一度だけ出る。"""
    roots = (ctrl_entry.get('action_references') or {}).get(action)
    if roots is None:
        # The action is inherited: nothing in this file runs for it except the filters
        # that apply to it. Falling back to the whole file would hand the screen every
        # sibling action's work.
        refs = []
        for f in ctrl_entry.get('filter_references') or []:
            if (not f['only'] or action in f['only']) and action not in f['except']:
                refs.extend(f['references'])
        roots = uniq(refs)
    own_sup = superclass_file(ctrl_entry)
    placed = set([ctrl_entry['file']])

    def build(refs, parent_entry, depth):
        sup = superclass_file(parent_entry)
        # 兄弟の絞り込みは先に全部済ませる。子を辿る途中で placed に入ったファイルが
        # 後の兄弟に居ても、Ruby 版と同じくそのまま出す。
        kept = [r for r in refs if not (r == sup or r == own_sup or r in placed
                                        or not bucket_by_file.get(r) or _ERRORS_FILE_RE.search(r))]
        nodes = []
        for r in kept[:FLOW_MAX_CHILDREN]:
            placed.add(r)
            node = {'file': r, 'bucket': bucket_by_file[r]}
            if r in hub_files:
                node['hub'] = True
            entry = entry_by_file.get(r)
            if entry and depth < FLOW_MAX_DEPTH and not node.get('hub') and bucket_by_file[r] in FLOW_EXPAND:
                kids = build(entry.get('references') or [], entry, depth + 1)
                if kids:
                    node['calls'] = kids
            nodes.append(node)
        return nodes

    return build(roots, ctrl_entry, 1)


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

routes_by_screen = defaultdict(list)
for r in scan.get('routes') or []:
    if not (r.get('controller') and r.get('action')):
        continue
    routes_by_screen['%s#%s' % (r['controller'], r['action'])].append(r)

# Controllers provided by Rails itself or by a gem's engine. A route pointing at
# one of these is normal, so keeping them in `unresolved_controllers` would bury
# the real finding (routes pointing at controllers that do not exist).
FRAMEWORK_CONTROLLER_PREFIXES = ['rails/', 'devise/', 'doorkeeper/', 'active_storage/', 'action_mailbox/', 'turbo/']


def framework_controller(controller):
    return any((controller or '').startswith(p) for p in FRAMEWORK_CONTROLLER_PREFIXES)


screens = []
unresolved_controllers = set()
framework_route_count = 0

for screen_id, routes in routes_by_screen.items():
    first = routes[0]
    ctrl_class = first.get('controller_class')
    ctrl_file = resolve_controller_file(first)

    if ctrl_file is None:
        if framework_controller(first.get('controller')):
            framework_route_count += 1
        else:
            unresolved_controllers.add(ctrl_class)
        continue

    ctrl_entry = entry_by_file.get(ctrl_file)
    action = first['action']
    depths = reachable_files(ctrl_file, options.max_depth)
    views = collect_views(first['controller'], action)

    code_files = [{
        'file': f,
        'bucket': bucket_by_file.get(f),
        'depth': depths[f],
        'hub': f in hub_files,
    } for f in sorted(depths)]

    screens.append({
        'id': screen_id,
        'controller': first['controller'],
        'action': action,
        'controller_class': ctrl_class,
        'controller_file': ctrl_file,
        'pack': ctrl_entry.get('pack') if ctrl_entry else None,
        'action_defined': bool(ctrl_entry and action in (ctrl_entry.get('actions') or [])),
        'routes': [{'method': r.get('method'), 'path': r.get('path'), 'source_file': r.get('source_file'),
                    'source_line': r.get('source_line')} for r in routes],
        'views': views,
        'vue_page': vue_page_by_key.get('%s/%s' % (first['controller'], action)),
        'code_files': code_files,
        'code_file_count': len(code_files),
        # Who calls whom, starting from this action alone. code_files above stays as
        # the flat, whole-controller reach that KR2's blast radius is built on.
        'view_tree': view_tree(first['controller'], action),
        'flow': call_flow(ctrl_entry, action) if ctrl_entry else [],
    })

screens.sort(key=lambda s: s['id'])

# ---------------------------------------------------------------------------
# Frontend -> API: page -> composable/component -> api function -> route -> action
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r':[A-Za-z0-9_]+')


def route_regex(path):
    p = re.sub(r'\(/[^)]*\)', '', path or '')
    p = re.sub(r'\(\.:format\)', '', p)
    p = re.sub(r'/{2,}', '/', p)
    p = _PARAM_RE.sub('[^/]+', re.escape(p))
    return re.compile(r'\A' + p + r'\Z')


def normalize_api_path(path):
    p = re.sub(r'[?#].*\Z', '', path or '', count=1)
    p = re.sub(r'\$\{[^}]*\}', 'X', p)
    p = re.sub(r'\.json\Z', '', p, count=1)
    return re.sub(r'/{2,}', '/', p)


route_matchers = [[(r.get('method') or '').upper().split('|'), route_regex(r.get('path')),
                   '%s#%s' % (r['controller'], r['action'])]
                  for r in (scan.get('routes') or []) if r.get('controller') and r.get('action')]
screen_ids = set(s['id'] for s in screens)


def match_route(method, path):
    np = normalize_api_path(path)
    m = method.upper()
    for ms, rx, sid in route_matchers:
        if (m in ms or (m == 'PUT' and 'PATCH' in ms) or (m == 'PATCH' and 'PUT' in ms)) \
                and rx.search(np) and sid in screen_ids:
            return sid
    return None


api_fn = {}
for a in scan.get('frontend_api') or []:
    api_fn['%s.%s' % (a['object'], a['fn'])] = a
modules = {}
for m in scan.get('frontend_modules') or []:
    modules[m['file']] = m

FRONT_MAX_DEPTH = 4
# Shared layout / UI kit files call their own APIs (notifications, sidebar); following
# them would attach the same calls to every page.
FRONT_SHARED = re.compile(r'/components/(layouts|parts|base|common)/|/stores/|/utils/|/api/')

by_id = dict((s['id'], s) for s in screens)
for s in screens:
    page = s['vue_page']
    if not (page and page in modules):
        continue
    if modules[page].get('page_title_ja'):
        s['title_ja'] = modules[page]['page_title_ja']

    calls = {}
    # queue item: [file, via, names] -- names = the functions of `file` the caller uses
    # (None = the whole file: the page itself, or a module with no function table)
    queue = [[page, [page], None]]
    seen = set([page])
    while queue:
        file, via, names = queue.pop(0)
        m = modules.get(file)
        if not m:
            continue
        api_list = m.get('api_calls') or []
        if names and m.get('functions'):
            reach = {}  # 挿入順を保つ集合（Ruby の Set と同じ）
            stack = list(names)
            while stack:
                n = stack.pop()
                if n in reach or not m['functions'].get(n):
                    continue
                reach[n] = True
                stack.extend(m['functions'][n].get('calls') or [])
            pairs = []
            for n in reach:
                pairs.extend(m['functions'][n].get('api_calls') or [])
            api_list = [list(t) for t in uniq(tuple(p) for p in pairs)]
        for obj, fn in api_list:
            a = api_fn.get('%s.%s' % (obj, fn))
            if not a:
                continue
            target = match_route(a['method'], a['path'])
            key = '%s.%s' % (obj, fn)
            if key not in calls:
                calls[key] = {'method': a['method'], 'path': normalize_api_path(a['path']), 'fn': key,
                              'screen': target, 'via': via}
        for meth, path in m.get('urls') or []:
            target = match_route(meth, path)
            key = '%s %s' % (meth, normalize_api_path(path))
            if key not in calls:
                calls[key] = {'method': meth, 'path': normalize_api_path(path), 'fn': None,
                              'screen': target, 'via': via}
        if len(via) > FRONT_MAX_DEPTH:
            continue
        for imp in m.get('imports') or []:
            if imp in seen or FRONT_SHARED.search(imp):
                continue
            seen.add(imp)
            used = (m.get('import_uses') or {}).get(imp)
            queue.append([imp, via + [imp], used])
    s['api_calls'] = list(calls.values())
    for c in calls.values():
        t = by_id.get(c['screen']) if c['screen'] else None
        if not t:
            continue
        t.setdefault('called_from', []).append({'screen': s['id'], 'vue_page': page, 'fn': c['fn'], 'via': c['via']})

# ---------------------------------------------------------------------------
# Reverse index: file -> screens that reach it
# ---------------------------------------------------------------------------

screens_by_file = defaultdict(list)
for s in screens:
    for cf in s['code_files']:
        screens_by_file[cf['file']].append(s['id'])
    for v in s['views']:
        screens_by_file[v].append(s['id'])
    if s['vue_page']:
        screens_by_file[s['vue_page']].append(s['id'])

total_screens = len(screens)

shared_files = []
for file, ids in screens_by_file.items():
    u = uniq(ids)
    shared_files.append({
        'file': file,
        'bucket': bucket_by_file.get(file) or ('views' if file in view_by_file else None),
        'screen_count': len(u),
        'screen_ratio': 0.0 if total_screens == 0 else ruby_round(float(len(u)) / total_screens, 4),
        'hub': file in hub_files,
        'raw_fan_in': fan_in.get(file, 0),
        'screens': sorted(u)[:20],
        'screens_truncated': len(u) > 20,
    })
shared_files.sort(key=lambda f: (-f['screen_count'], f['file']))

# ---------------------------------------------------------------------------
# Screen <-> screen relatedness
# ---------------------------------------------------------------------------
# 2つの画面が「関連している」とは、同じ *具体的な* ファイルに到達していること。
# ハブはこの判定に何も寄与しない（ApplicationRecord は 1180 画面が参照する）ため、
# RELATED_FILE_CAP 画面を超えるファイルはペアを生成せず、重みは到達画面数で減衰させる。
# 正規化はコサイン相当。スコアは 0.0〜1.0。
# 浮動小数の足し算は順序で結果が変わるので、Ruby 版と同じ順で足すこと。

RELATED_FILE_CAP = 150
RELATED_PER_SCREEN = 12
RELATED_MIN_SCORE = 0.05

file_idf = {}
for file, ids in screens_by_file.items():
    if file in hub_files:
        continue
    n = len(uniq(ids))
    if n > RELATED_FILE_CAP:
        continue
    file_idf[file] = 1.0 / math.log(2.0 + n)

screen_norm = defaultdict(float)
for file, ids in screens_by_file.items():
    w = file_idf.get(file)
    if w is None:
        continue
    for sid in uniq(ids):
        screen_norm[sid] += w * w

pair_score = defaultdict(lambda: defaultdict(float))
for file, ids in screens_by_file.items():
    w = file_idf.get(file)
    if w is None:
        continue
    u = uniq(ids)
    if len(u) < 2:
        continue
    contrib = w * w
    for i, a in enumerate(u):
        for j in range(i + 1, len(u)):
            b = u[j]
            pair_score[a][b] += contrib
            pair_score[b][a] += contrib

for s in screens:
    norm_a = screen_norm.get(s['id'], 0.0)
    peers = pair_score.get(s['id'], {})
    related = []
    if norm_a > 0 and peers:
        for other, raw in peers.items():
            norm_b = screen_norm.get(other, 0.0)
            if norm_b <= 0:
                continue
            score = raw / math.sqrt(norm_a * norm_b)
            if score < RELATED_MIN_SCORE:
                continue
            related.append({'id': other, 'score': ruby_round(score, 4)})
        related.sort(key=lambda r: (-r['score'], r['id']))
    s['related_screens'] = related[:RELATED_PER_SCREEN]

# ---------------------------------------------------------------------------
# Files no screen reaches -- candidates for step 3 to flag, not proof of dead code
# ---------------------------------------------------------------------------

reached = set(screens_by_file)
unreached = []
for bucket in RUBY_BUCKETS:
    for e in scan.get(bucket) or []:
        if e['file'] in reached:
            continue
        unreached.append({'file': e['file'], 'bucket': bucket, 'class': e.get('class'), 'pack': e.get('pack')})
unreached.sort(key=lambda e: e['file'])

# ---------------------------------------------------------------------------
# Per-pack rollup
# ---------------------------------------------------------------------------

pack_rollup = {}


def _pack_row(name):
    if name not in pack_rollup:
        pack_rollup[name] = {'screens': 0, 'files': 0}
    return pack_rollup[name]


for s in screens:
    _pack_row(s.get('pack') or '(root)')['screens'] += 1
for bucket in RUBY_BUCKETS:
    for e in scan.get(bucket) or []:
        _pack_row(e.get('pack') or '(root)')['files'] += 1

# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

avg = 0 if total_screens == 0 else ruby_round(float(sum(s['code_file_count'] for s in screens)) / total_screens, 1)

out = {
    'meta': {
        'schema_version': SCHEMA_VERSION,
        'generated_at': now_iso(),
        'scan_commit': scan_meta.get('commit'),
        'scan_generated_at': scan_meta.get('generated_at'),
        'scan_mode': scan_meta.get('mode'),
        'routes_source': scan_meta.get('routes_source'),
        'max_depth': options.max_depth,
        'hub_threshold': options.hub_threshold,
        'screen_unit': 'controller#action',
        'related_file_cap': RELATED_FILE_CAP,
        'related_per_screen': RELATED_PER_SCREEN,
        'related_min_score': RELATED_MIN_SCORE,
    },
    'stats': {
        'screens': total_screens,
        'routes': len(scan.get('routes') or []),
        'hub_files': len(hub_files),
        'screens_with_vue_page': sum(1 for s in screens if s['vue_page']),
        'files_reached': len(reached),
        'files_unreached': len(unreached),
        'unresolved_controllers': len(unresolved_controllers),
        'framework_routes': framework_route_count,
        'avg_files_per_screen': avg,
        'screens_with_related': sum(1 for s in screens if s['related_screens']),
    },
    'packs': pack_rollup,
    # 被参照数の多い順。同数はファイル名順（Ruby 版の sort_by は不安定で同数の順が定まらなかった）
    'hubs': sorted([{'file': f, 'raw_fan_in': fan_in[f]} for f in sorted(hub_files)],
                   key=lambda h: -h['raw_fan_in']),
    'screens': screens,
    'shared_files': shared_files,
    'unreached_files': unreached,
    'unresolved_controllers': sorted(x for x in unresolved_controllers if x is not None),
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

if not options.quiet:
    rel = OUT.replace(os.getcwd() + '/', '', 1)
    st = out['stats']
    err = sys.stderr
    err.write('[impact] analysis -> %s\n' % rel)
    err.write('[impact] scan_commit=%s screens=%d avg_files_per_screen=%s\n'
              % (scan_meta.get('commit'), st['screens'], st['avg_files_per_screen']))
    err.write('[impact] related: %d/%d screens have peers (cap=%d min_score=%s)\n'
              % (st['screens_with_related'], st['screens'], RELATED_FILE_CAP, RELATED_MIN_SCORE))
    err.write('[impact] hubs=%d reached=%d unreached=%d unresolved_controllers=%d framework_routes=%d\n'
              % (st['hub_files'], st['files_reached'], st['files_unreached'],
                 st['unresolved_controllers'], st['framework_routes']))
    top = [f for f in shared_files if not f['hub']][:3]
    if top:
        err.write('[impact] most shared (non-hub): %s\n'
                  % ' '.join('%s(%d)' % (f['file'], f['screen_count']) for f in top))
