#!/usr/bin/env python3
# KR1 step 3 -- turn impact.json into one browsable HTML page.
#
# Reads only impact.json: no source files, no analysis. Every number on the page
# comes from step 2. If a fact is missing, extend step 1 or step 2 -- the moment
# this script starts computing, the page stops being reproducible.
#
# It draws nothing either. assets/app.html is a hand-written page with Cytoscape.js
# inlined; this script only names screens and files and injects the dataset into
# it, so nobody needs npm, a toolchain, network, or any installed app to get a page.
# The page source is readable -- edit assets/app.html directly.
#
# Python stdlib only, and only syntax that runs on Python 3.8.

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

EXPECTED_IMPACT_SCHEMA = 1
HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser(prog='build.py')
ap.add_argument('--impact', help='Input impact.json')
ap.add_argument('--out', help='Output HTML')
ap.add_argument('--root', help='Repository root')
ap.add_argument('--lang', choices=['vi', 'ja'], default='vi', help='UI language: vi (default) | ja')
ap.add_argument('--default-screen', dest='default_screen', help='Screen selected on load')
ap.add_argument('--config', help='Project config (default .claude/code-map.json)')
ap.add_argument('--refresh', action='store_true', help='Run steps 1 and 2 first, then build')
ap.add_argument('--open', action='store_true', help='Open the page when it is written')
ap.add_argument('--quiet', action='store_true', help='Suppress the stderr summary')
options = ap.parse_args()


def die(msg):
    sys.stderr.write(msg + '\n')
    sys.exit(1)


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def ruby_round(x, ndigits=0):
    """Ruby の Float#round。四捨五入（0から遠い方）。ndigits=0 なら整数を返す。"""
    if ndigits == 0:
        return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    s = 10.0 ** ndigits
    f = float(Decimal(x * s).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if x > 0:
        if (f + 0.5) / s <= x:
            f += 1
    elif (f - 0.5) / s >= x:
        f -= 1
    return f / s


def ruby_split(s, sep):
    """Ruby の String#split(str) は末尾の空要素を捨てる。"""
    parts = s.split(sep)
    while parts and parts[-1] == '':
        parts.pop()
    return parts


def stable_desc(items, key):
    """Ruby の sort_by { -x } の代わり。Ruby 版は不安定ソートで同点の順が実行ごとに
    定まらなかったため、同点は元の並びを保つ（Python の sort は安定）。"""
    return sorted(items, key=lambda x: -key(x))


# 設定ファイルの正規表現は Ruby の文法で書かれている。Python で同じ意味になるよう
# `\w` `\s` `\d` `\h` を ASCII に、`\z` `\Z` を Python の書き方に直す。
# `\b` は Ruby と同じく Unicode のまま（日本語も単語文字として境界を判定する）。
_ASCII_CLASSES = {
    'w': ('[a-zA-Z0-9_]', 'a-zA-Z0-9_'),
    'd': ('[0-9]', '0-9'),
    'h': ('[0-9a-fA-F]', '0-9a-fA-F'),
    's': ('[ \\t\\n\\x0b\\f\\r]', ' \\t\\n\\x0b\\f\\r'),
    'W': ('[^a-zA-Z0-9_]', None),
    'S': ('[^ \\t\\n\\x0b\\f\\r]', None),
}


def ruby_regex(pattern):
    out = []
    in_class = False
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == '\\' and i + 1 < n:
            e = pattern[i + 1]
            if e in _ASCII_CLASSES:
                outside, inside = _ASCII_CLASSES[e]
                if in_class:
                    if inside is None:
                        raise ValueError('\\%s inside a character class is not supported: %s' % (e, pattern))
                    out.append(inside)
                else:
                    out.append(outside)
            elif e == 'z' and not in_class:
                out.append('\\Z')
            elif e == 'Z' and not in_class:
                out.append('(?=\\n?\\Z)')
            else:
                out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == ']':
                in_class = False
        elif c == '[':
            in_class = True
            out.append(c)
            i += 1
            if i < n and pattern[i] == '^':
                out.append('^')
                i += 1
            if i < n and pattern[i] == ']':
                out.append(']')
                i += 1
            continue
        out.append(c)
        i += 1
    return re.compile(''.join(out), re.MULTILINE)


# ---------------------------------------------------------------------------
# Look and density
# ---------------------------------------------------------------------------
# The bundle reads all of this from the dataset, so changing the palette, the
# node sizes, the force strengths or how many files a screen draws is an edit
# here plus a re-run -- never a rebuild of assets/app.html.

UI = {
  'colors': {
    'module': '#7c6cf5', 'feature': '#38bdf8', 'route': '#fb923c', 'controller': '#34d399',
    'service': '#c084fc', 'model': '#f87171', 'view': '#fbbf24', 'lib': '#94a3b8'
  },
  'sizes': { 'centre': 130, 'peer': 46, 'module': 90, 'file': 22 },
  'labelSize': 12,
  # Sàn cỡ chữ tính theo toạ độ thế giới: zoom vào thì chữ to theo, không đứng yên.
  'labelMinWorld': 15,
  'hotspots': 6,
  'groupGrowth': 6,   # a feature node grows with the controllers it holds
  # Cây: cột theo độ sâu, dòng theo thứ tự lá. colWidth phải đủ rộng cho nhãn,
  # rowGap đủ thưa để chữ không chồng nhau khi zoom-to-fit.
  'tree': { 'colWidth': 380, 'rowGap': 46, 'groupGap': 0.6,
              'corner': 12 },   # bán kính bo góc của đường kẻ vuông góc
  # Screens and modules are drawn as cards: a known width means a label can never
  # land on top of its neighbour's.
  'box': { 'width': 190, 'moduleWidth': 150, 'height': 40, 'shortHeight': 30,
             'titleSize': 12, 'subSize': 9, 'subChars': 30 },
  'homeLabelChars': 24,   # names on the overview are trimmed to this
  # The overview is a laid-out web: modules on an outer circle, their screens
  # fanned on rings around each one.
  'grid': { 'columns': 4 },   # cards per row inside a module block   # below this only module names are drawn
  'maxFiles': 22,   # code files drawn per screen; hubs are never drawn
  'homePeers': 3,   # sibling links drawn between home screens
  'homeMinScore': 0.2,
  'maxViews': 6,
  'maxRoutes': 5
}

# Which controllers count as "the main areas" on the landing view. A controller
# is the unit a person recognises as a feature -- Tồn kho, Kiểm kê, Đơn bán -- and
# its actions are the screens inside it. The API and the admin console are
# reachable by drilling in, not by browsing.
ROOT = os.path.abspath(options.root or os.path.join(HERE, '../../../..'))
IMPACT = os.path.abspath(options.impact or os.path.join(ROOT, '.ai/code-map/analysis/impact.json'))
OUT = os.path.abspath(options.out or os.path.join(ROOT, '.ai/code-map/doc/index.html'))

# ---------------------------------------------------------------------------
# Per-project config
# ---------------------------------------------------------------------------
# The scripts are generic Rails. Everything that knows this repo is zaico -- the
# Vietnamese dictionary, the feature areas, which controllers are back-office
# noise, the screen to open on -- lives in `.claude/code-map.json`, so another
# project reuses these skills by writing that one file and nothing else. With no
# file at all the map still builds: screens keep their controller#action names
# and every controller lands in one unnamed area.
CONFIG_FILE = os.path.abspath(options.config or os.path.join(ROOT, '.claude/code-map.json'))
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, encoding='utf-8') as _f:
        CONFIG = json.load(_f)
else:
    CONFIG = {}
LABELS = CONFIG.get('labels') or {}
AREAS_EN = CONFIG.get('areas_en') or {}
OTHER_AREA = CONFIG.get('other_area') or 'Khác'

# Packwerk packs are a deployment boundary, not a mental one: 152 of zaico's 223
# controllers live in "(root)", which tells a reader nothing. Feature areas are
# what people actually call the parts of the product. First match wins, so keep
# the narrow patterns above the broad ones; a growing "other" area is the signal
# to add a row to the config.
FEATURES = [[f['name'], ruby_regex(f['match'])] for f in (CONFIG.get('features') or [])]

# A module namespace (a pack, or a feature like `stocktakings/`) owns everything
# under it. Without this, `purchase_orders/catalogs` lands in Sales because its
# last segment says "catalog", and one feature is scattered across five areas.
NAMESPACE_FEATURES = CONFIG.get('namespace_features') or {}


def feature_of(controller):
    segs = ruby_split(controller, '/')
    while len(segs) > 1 and segs[0] in ('api', 'v1', 'v2', 'v3'):
        segs.pop(0)
    if len(segs) > 1 and segs[0] in NAMESPACE_FEATURES:
        return NAMESPACE_FEATURES[segs[0]]

    parts = ruby_split(controller, '/')
    last = parts[-1] if parts else ''
    for name, rx in FEATURES:
        if rx.search(last):
            return name
    first = parts[0] if parts else ''
    for name, rx in FEATURES:
        if rx.search(first):
            return name
    return OTHER_AREA


# Back-office and API controllers are real screens but not the product, and on
# the overview they bury it. Which ones those are is a per-project question.
HIDDEN_CONTROLLERS = [ruby_regex(p) for p in (CONFIG.get('hidden_controllers') or [])]


def home_controller(screen):
    return not any(rx.search(screen['controller']) for rx in HIDDEN_CONTROLLERS)


# Steps 1 and 2 own their own refresh rules; this only spares the caller from
# remembering the order and from digging meta.commit out of scan.json.
# A first run on a fresh clone has no .ai/ at all (it is gitignored), so refresh
# implicitly rather than sending the user off to find another skill.
if options.refresh or not os.path.exists(IMPACT):
    skills = os.path.join(ROOT, '.claude/skills')
    scan_path = os.path.join(ROOT, '.ai/code-map/raw/scan.json')
    since = None
    if os.path.exists(scan_path):
        with open(scan_path, encoding='utf-8') as _f:
            commit = (json.load(_f).get('meta') or {}).get('commit')
        if commit and subprocess.call(['git', '-C', ROOT, 'cat-file', '-e', '%s^{commit}' % commit],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            since = commit
    scan_cmd = [os.path.join(skills, 'code-map/scripts/scan-code-map.sh'), '--root', ROOT]
    if since:
        scan_cmd += ['--since', since]
    # Both sibling scripts resolve their default paths against the working
    # directory, so they have to run from the repository root.
    subprocess.run(scan_cmd, cwd=ROOT, check=True)
    subprocess.run([os.path.join(skills, 'impact-analysis/scripts/analyze-impact.sh')], cwd=ROOT, check=True)

if not os.path.exists(IMPACT):
    die('[doc] missing %s - run the impact-analysis skill first' % IMPACT)

with open(IMPACT, encoding='utf-8') as _f:
    impact = json.load(_f)
meta = impact.get('meta') or {}
if meta.get('schema_version') != EXPECTED_IMPACT_SCHEMA:
    die('[doc] impact.json schema_version=%s, expected %d'
        % (json.dumps(meta.get('schema_version')), EXPECTED_IMPACT_SCHEMA))

screens = impact['screens']
if not (screens and 'related_screens' in screens[0]):
    die('[doc] impact.json has no related_screens - re-run step 2')

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
# `controller#action` is precise and unreadable. The rule is deliberately
# visible: {verb} {noun}, with `overrides` winning outright for names everyone
# already says. A segment with no entry keeps its original name - inventing a
# translation would be worse than showing the truth.
#
# Each table is optional: a project with no dictionary at all still builds a map,
# it just shows every screen as controller#action.
OVERRIDE = LABELS.get('overrides') or {}
NS = LABELS.get('namespaces') or {}
ACTION = LABELS.get('actions') or {}
NOUN = LABELS.get('nouns') or {}
VERB = LABELS.get('verbs') or {}
TERM = LABELS.get('terms') or {}
VERSION_SEGMENT = re.compile(r'\A(v[0-9]+|.*_v[0-9]+)\Z')
_V_TOKEN = re.compile(r'\Av[0-9]+\Z')


def humanize(token):
    return token.replace('_', ' ')


def upcase_first(s):
    return s if not s else s[0].upper() + s[1:]


def translate_action(action):
    """辞書に無いアクション名は、先頭の動詞 + 続く語の訳で組み立てる。
    477 種のアクションのうち 427 種は1〜2画面にしか出ないので、全部を辞書に書くのは無理。"""
    known = ACTION.get(action)
    if known is not None:
        return [known, True]

    parts = ruby_split(action, '_')
    verb = VERB.get(parts[0]) if parts else None
    if verb is None:
        return [upcase_first(humanize(action)), False]

    rest = parts[1:]
    if not rest:
        return [verb, True]

    words = [TERM.get(w) for w in rest]
    if any(w is None for w in words):
        return [upcase_first(humanize(action)), False]

    return [upcase_first('%s %s' % (verb, ' '.join(words))), True]


def join_phrase(verb, noun):
    """動詞句の末尾と名詞の先頭で重なる語を1回にする（"Tải xuống sao kê" + "sao kê"）。"""
    a = verb.split()
    b = noun.split()
    overlap = min(len(a), len(b))
    while overlap > 0 and [w.lower() for w in a[len(a) - overlap:]] != [w.lower() for w in b[:overlap]]:
        overlap -= 1
    return ' '.join(a + b[overlap:])


def _noun_segment(segments):
    i = len(segments) - 1
    while i > 0 and VERSION_SEGMENT.search(segments[i]) and NOUN.get(segments[i]) is None:
        i -= 1
    return i


def controller_label(screen):
    """コントローラー自身の名前（前に動詞を付けない）。"""
    segments = ruby_split(screen['controller'], '/')
    i = _noun_segment(segments)
    noun = NOUN.get(segments[i])
    if noun is None:
        noun = humanize(segments[i])
    qualifiers = [NS.get(seg) if NS.get(seg) is not None else humanize(seg)
                  for j, seg in enumerate(segments) if j != i]
    return [upcase_first(noun), ' · '.join(qualifiers) if qualifiers else None]


def label_for(screen):
    segments = ruby_split(screen['controller'], '/')
    if screen['id'] in OVERRIDE:
        return [OVERRIDE[screen['id']], None, True]

    i = _noun_segment(segments)
    noun = NOUN.get(segments[i])
    noun_known = noun is not None
    if noun is None:
        noun = humanize(segments[i])

    verb, verb_known = translate_action(screen['action'])
    qualifiers = [NS.get(seg) if NS.get(seg) is not None else humanize(seg)
                  for j, seg in enumerate(segments) if j != i]

    return [upcase_first(join_phrase(verb, noun)),
            ' - '.join(qualifiers) if qualifiers else None,
            noun_known and verb_known]


exact = 0
named = {}
for s in screens:
    name, qualifier, ok = label_for(s)
    if ok:
        exact += 1
    named[s['id']] = [name, qualifier, 1 if ok else 0]

# ---------------------------------------------------------------------------
# Payload - file paths are interned so they are stored once, not once per screen
# ---------------------------------------------------------------------------

facts = {}
for f in impact.get('shared_files') or []:
    facts[f['file']] = [f['bucket'], f['screen_count'], 1 if f['hub'] else 0]
for f in impact.get('unreached_files') or []:
    if f['file'] not in facts:
        facts[f['file']] = [f['bucket'], 0, 0]


def clean_path(path):
    """Rails ghi đoạn tuỳ chọn trong ngoặc: `/(/companies/:company_id)/items`.
    Bỏ ngoặc xong còn lại hai dấu gạch dính nhau, nên phải dọn luôn — nếu không,
    mọi đường dẫn đa tenant hiện ra thành `//items`."""
    if path is None:
        return path
    cleaned = re.sub(r'/{2,}', '/', re.sub(r'\(/[^)]*\)', '', path))
    return cleaned or '/'


_BUCKET_BY_DIR = [('/controllers/', 'controllers'), ('/models/', 'models'), ('/services/', 'services'),
                  ('/views/', 'views'), ('/jobs/', 'jobs'), ('/mailers/', 'mailers'),
                  ('/frontend/', 'frontend_pages')]


def bucket_for(path, given):
    if given:
        return given
    for needle, bucket in _BUCKET_BY_DIR:
        if needle in path:
            return bucket
    return 'lib'


def vi_words(s):
    """Tên tiếng Việt cho một file trong cây gọi. Tiếng Việt đặt bổ ngữ phía sau
    (inventory import → nhập tồn kho), nên khi ghép từng từ thì đảo thứ tự. Từ nào
    không có trong từ điển thì giữ nguyên tên gốc — đoán bừa còn tệ hơn để tên thật."""
    words = [w for w in s.split('_') if w and not _V_TOKEN.search(w)]
    if not words:
        return None
    vi = [TERM.get(w) if TERM.get(w) is not None else NOUN.get(w) for w in words]
    if any(v is None for v in vi):
        return None
    return ' '.join(reversed(vi))


GENERIC_BASENAMES = ['service', 'client', 'util', 'utils', 'base', 'version', 'response', 'request', 'parser',
                     'adapter', 'api', 'constants', 'config', 'error', 'errors', 'helper']


def vi_noun(token):
    for key in (token, token + 's', re.sub(r'y\Z', 'ies', token, count=1)):
        if NOUN.get(key) is not None:
            return NOUN[key]
    return vi_words(token)


def _first_owner(dirs):
    for d in reversed(dirs):
        v = vi_noun(d)
        if v is not None:
            return v
    return None


def _join_present(items, sep):
    return sep.join(x for x in items if x is not None)


_BASE_EXT_RE = re.compile(r'\..*\Z')
_APP_DIR_RE = re.compile(r'\A(?:packs/[^/]+/)?app/[^/]+/')
_ROLE_SUFFIX_RE = re.compile(r'_(service|util|utils|job|builder|processor|mailer)\Z')


def file_label(path, bucket):
    name = os.path.basename(path)
    partial = name.startswith('_')
    base = re.sub(r'\A_', '', _BASE_EXT_RE.sub('', name, count=1), count=1)
    dirs = ruby_split(_APP_DIR_RE.sub('', path, count=1), '/')[:-1]
    label = None
    if bucket == 'models':
        label = vi_noun(base)
    elif bucket in ('services', 'jobs', 'lib', 'mailers'):
        # service.rb / client.rb / version.rb nói lên thư mục chứ không nói lên
        # chính nó: gọi theo thư mục, kèm vai trò.
        if base in GENERIC_BASENAMES and dirs:
            d = dirs[-1]
            v = vi_noun(d)
            where = v if v is not None else d.replace('_', ' ').upper()
            role = TERM.get(base) if TERM.get(base) is not None else base
            return [upcase_first('%s · %s' % (role, where)), 1 if vi_noun(dirs[-1]) is not None else 0]
        core = _ROLE_SUFFIX_RE.sub('', base, count=1)
        tokens = ruby_split(core, '_')
        verb = VERB.get(tokens[0]) if tokens else None
        owner = _first_owner(dirs)
        rest = vi_words('_'.join(tokens[1:])) if len(tokens) > 1 else None
        own_words = vi_words(core)
        if verb is not None and (len(tokens) == 1 or rest is not None):
            label = _join_present([verb, rest, owner], ' ')
        elif own_words is not None:
            seen = []
            for x in (own_words, owner):
                if x is not None and x not in seen:
                    seen.append(x)
            label = ' · '.join(seen)
    elif bucket in ('views', 'frontend_pages'):
        owner = _first_owner(dirs)
        if path.endswith('.jbuilder'):
            return [upcase_first(_join_present(['Dữ liệu JSON', translate_action(base)[0].lower(), owner], ' · ')), 1]
        if partial:
            part = vi_words(base)
            label = _join_present(['Khối %s' % part, owner], ' · ') if part is not None else None
        else:
            act, known = translate_action(base)
            label = _join_present(['Trang %s' % act.lower(), owner], ' · ') if known else None
    elif bucket == 'controllers':
        label = vi_words(re.sub(r'_controller\Z', '', base, count=1))
    if label is not None:
        return [upcase_first(label), 1]
    return [humanize(base), 0]


index = {}
files = []


def fidx(path):
    if not path:
        return -1
    if path not in index:
        f = facts.get(path) or [None, 0, 0]
        b = bucket_for(path, f[0])
        files.append([path, b, f[1], f[2]] + file_label(path, b))
        index[path] = len(files) - 1
    return index[path]


at = {}
for i, s in enumerate(screens):
    at[s['id']] = i


def view_payload(nodes):
    out = []
    for n in nodes:
        i = fidx(n['file'])
        kids = view_payload(n.get('renders') or [])
        out.append([i, kids] if kids else [i])
    return out


def flow_payload(nodes):
    out = []
    for n in nodes:
        i = fidx(n['file'])
        kids = flow_payload(n.get('calls') or [])
        out.append([i, kids] if kids else [i])
    return out


def called_by(s):
    rows = []
    for c in s.get('called_from') or []:
        if c['screen'] in at:
            rows.append([at[c['screen']], c['fn'], [fidx(v) for v in (c.get('via') or [])]])
    out = []
    seen = set()
    for x in rows:
        k = (x[0], x[1])
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


# 各キーの評価順は Ruby 版と同じにすること。fidx が初めて見たファイルに番号を振るので、
# 順が変わるとファイル番号が全部ずれる。
rows = []
for s in screens:
    name = named[s['id']]
    row = OrderedDict()
    row['id'] = s['id']
    row['lb'] = name[0]
    row['lq'] = name[1]
    row['lx'] = name[2]
    row['cf'] = fidx(s.get('controller_file'))
    row['vue'] = fidx(s.get('vue_page'))
    row['ad'] = 1 if s.get('action_defined') else 0
    row['nf'] = s.get('code_file_count') or 0
    row['ro'] = [[r['method'], clean_path(r['path'])] for r in (s.get('routes') or [])]
    row['vw'] = [i for i in (fidx(v) for v in (s.get('views') or [])) if i >= 0]
    row['cfs'] = [e for e in ([fidx(c['file']), c.get('depth') or 0, 1 if c.get('hub') else 0]
                              for c in (s.get('code_files') or [])) if e[0] >= 0]
    row['rel'] = [[at[r['id']], r['score']] for r in (s.get('related_screens') or []) if r['id'] in at]
    # Cây gọi của riêng action này: [file, [con...]] lồng nhau.
    row['fl'] = flow_payload(s.get('flow') or [])
    # Tiêu đề tiếng Nhật trên màn hình thật (在庫登録)
    row['tj'] = s.get('title_ja')
    # View của action kèm partial nó render: [file, [con...]] lồng nhau.
    row['vt'] = view_payload(s.get('view_tree') or [])
    # Trang Vue gọi API nào: [method, path, màn đích, hàm API, [file đi qua]]
    row['ac'] = [[c['method'], c['path'],
                  at[c['screen']] if (c.get('screen') and c['screen'] in at) else -1,
                  c['fn'], [fidx(v) for v in (c.get('via') or [])]] for c in (s.get('api_calls') or [])]
    # API action này được trang nào gọi: [màn trang, hàm API, [file đi qua]]
    row['cb'] = called_by(s)
    rows.append(row)

STRINGS = {
  'vi': {
    'title': 'Bản đồ ảnh hưởng',
    'sub': 'Cây phụ thuộc: Module → Màn hình → Controller → Model → Vue',
    'langBtn': 'VI', 'langAlt': 'EN',
    'tabs': { 'spider': 'Sơ đồ cây', 'modules': 'Danh sách module' },
    'iUnresolved': 'Đường dẫn trỏ tới controller không tồn tại',
    'iHubs': 'File hub — nơi dừng việc lần theo tham chiếu',
    'iUnreached': 'File không màn hình nào chạm tới',
    'iUnreachedNote': 'Đây là ứng viên, không phải bằng chứng code chết: việc lần theo dừng ở hub và ở độ sâu tối đa.',
    'fanIn': 'được gọi {n} lần',
    'noRoute': 'không có đường dẫn riêng · no URL of its own',
    'search': 'Tìm màn hình, đường dẫn, file code…',
    'filter': 'Lọc theo module', 'showAll': 'Hiện tất cả',
    'panel': 'Bảng điều khiển',
    'filterKinds': 'Lọc loại node', 'hotspots': 'File tác động lớn',
    'inGroup': 'Controller trong nhóm',
    'kindsShort': { 'module': 'Nhóm', 'feature': 'Màn hình', 'route': 'Route', 'controller': 'Ctrl',
                      'service': 'Srv', 'model': 'Model', 'view': 'Vue', 'lib': 'Lib' },
    'tip': 'Gốc cây nằm bên trái, mỗi nhánh là một loại node, lá là file cụ thể. Bấm một node để soi liên kết, bấm node nhóm để mở khu vực. Lăn chuột thu phóng, kéo để di chuyển.',
    'legend': 'Chú giải', 'sideTitle': 'Chi tiết node & liên kết',
    'searchResults': 'Kết quả tìm', 'noResults': 'Không có kết quả',
    'linked': 'Liên kết', 'usedBy': 'Màn hình dùng file này',
    'related': 'Màn hình liên quan', 'noRelated': 'Không có màn hình liên quan',
    'screensIn': 'Màn hình bên trong',
    'home': 'Trang chính', 'mainScreens': 'Các trang chính',
    'homeLede': 'Mỗi node lớn là một nhóm tính năng. Bấm vào để mở các controller bên trong.',
    'kinds': { 'module': 'Nhóm tính năng · Module', 'feature': 'Màn hình · Screen',
                 'route': 'Đường dẫn · Route', 'controller': 'Controller · Bộ điều khiển',
                 'service': 'Service · Nghiệp vụ', 'model': 'Model · Bảng dữ liệu',
                 'view': 'Giao diện · View / Vue', 'lib': 'Thư viện · Lib / Job' },
    # Nhãn trên node nhánh của sơ đồ cây — song ngữ, vì tên kỹ thuật là tiếng Anh
    # còn người đọc bản đồ thì đọc tiếng Việt.
    # Nhánh của sơ đồ cây được đặt tên theo CÂU HỎI người đọc mang tới bản đồ,
    # không theo loại node. "Model" không trả lời được câu nào cả.
    # Nhãn nhánh nằm ngay cạnh node nên phải ngắn; câu hỏi đầy đủ đã có trong
    # phần "Đọc bản đồ này thế nào" ở bảng bên phải.
    'roles': {
      'area'   : 'Thuộc nhóm',
      'areas'  : 'Nhóm tính năng',
      'entry'  : 'Đường dẫn',
      'runs'   : 'Code chạy',
      'data'   : 'Dữ liệu & nghiệp vụ',
      'screens': 'Màn hình',
      'ctrls'  : 'Controller',
      'peers'  : 'Controller liên quan',
      'affects': 'Ảnh hưởng theo'
    },
    'rolesEn': {
      'area'   : 'module',
      'areas'  : 'feature areas',
      'entry'  : 'routes',
      'runs'   : 'code that runs',
      'data'   : 'data & logic',
      'screens': 'screens',
      'ctrls'  : 'controllers',
      'peers'  : 'related controllers',
      'affects': 'also affected'
    },
    'kindsBi': { 'module': 'Nhóm · Module', 'feature': 'Màn hình · Screen',
                   'route': 'Đường dẫn · Route', 'controller': 'Controller',
                   'service': 'Nghiệp vụ · Service', 'model': 'Dữ liệu · Model',
                   'view': 'Giao diện · View', 'lib': 'Thư viện · Lib' },
    'modulesLede': 'Mỗi module là một Packwerk pack. Số liệu lấy từ impact.json.',
    'sharedLede': 'File được nhiều màn hình chạm tới nhất. Hub bị loại vì gần như màn nào cũng chạm.',
    'colScreens': 'Màn hình', 'colFile': 'File', 'colKind': 'Loại',
    'mScreens': 'màn hình', 'mFiles': 'file',
    'tagUndefined': 'action không định nghĩa', 'tagHub': 'hub', 'tagRaw': 'tên tự sinh',
    'reaches': '{n} file được chạm', 'nScreens': '{n} màn hình',
    'hubNote': 'File hub — không tham gia tính độ liên quan, việc lần theo tham chiếu dừng ở đây.',
    'howTitle': 'Đọc bản đồ này thế nào',
    'how': [
      'Màn hình = controller#action. Nhiều đường dẫn trỏ cùng một action thì gộp làm một.',
      'Liên quan = mức độ cùng chạm tới file không phải hub (cosine). Hub không tính.',
      'Tham chiếu lấy bằng phân tích tĩnh theo tên: dispatch động bỏ sót, tên chung chung bắt thừa.',
      'Đường dẫn đọc từ DSL chứ không nạp Rails.'
    ],
    'statLabels': ['màn hình', 'file được chạm', 'file/màn hình', 'không ai chạm', 'controller không tồn tại']
  },
  'ja': {
    'title': 'Impact Map',
    'sub': 'Dependency tree: Module -> Screen -> Controller -> Model -> Vue',
    'langBtn': 'EN', 'langAlt': 'VI',
    'tabs': { 'spider': 'Tree', 'modules': 'Modules' },
    'iUnresolved': 'Routes pointing at a controller that does not exist',
    'iHubs': 'Hub files — where the reference walk stops',
    'iUnreached': 'Files no screen reaches',
    'iUnreachedNote': 'Candidates, not proof of dead code: the walk stops at hubs and at max depth.',
    'fanIn': 'referenced {n} times',
    'noRoute': 'no URL of its own',
    'search': 'Search screens, routes, files...',
    'filter': 'Filter by module', 'showAll': 'Show all',
    'panel': 'Panel',
    'filterKinds': 'Node kinds', 'hotspots': 'High-impact files',
    'inGroup': 'Controllers in module',
    'kindsShort': { 'module': 'Module', 'feature': 'Screen', 'route': 'Route', 'controller': 'Ctrl',
                      'service': 'Srv', 'model': 'Model', 'view': 'Vue', 'lib': 'Lib' },
    'tip': 'Root on the left, one branch per node kind, leaves are files. Click a node to highlight its network. Scroll to zoom, drag to pan.',
    'legend': 'Legend', 'sideTitle': 'Node & links',
    'searchResults': 'Results', 'noResults': 'No results',
    'linked': 'Linked', 'usedBy': 'Screens using this file',
    'related': 'Related screens', 'noRelated': 'No related screens',
    'screensIn': 'Screens in module',
    'home': 'Home', 'mainScreens': 'Main screens',
    'homeLede': 'Each node is a main screen. Click one to see its related screens and the code it reaches.',
    'kinds': { 'module': 'Module', 'feature': 'Screen', 'route': 'Route', 'controller': 'Controller',
                 'service': 'Service', 'model': 'Model', 'view': 'View / Vue', 'lib': 'Lib / Job' },
    'roles': {
      'area': 'Module', 'areas': 'Feature areas', 'entry': 'Routes',
      'runs': 'Code that runs', 'data': 'Data & logic', 'screens': 'Screens',
      'ctrls': 'Controllers', 'peers': 'Related controllers', 'affects': 'Also affected'
    },
    'kindsBi': { 'module': 'Module', 'feature': 'Screen', 'route': 'Route', 'controller': 'Controller',
                   'service': 'Service', 'model': 'Model', 'view': 'View', 'lib': 'Lib' },
    'modulesLede': 'Each module is a Packwerk pack. Numbers come from impact.json.',
    'sharedLede': 'Files reached by the most screens. Hubs are excluded.',
    'colScreens': 'Screens', 'colFile': 'File', 'colKind': 'Kind',
    'mScreens': 'screens', 'mFiles': 'files',
    'tagUndefined': 'action undefined', 'tagHub': 'hub', 'tagRaw': 'generated name',
    'reaches': 'reaches {n} files', 'nScreens': '{n} screens',
    'hubNote': 'Hub file - excluded from relatedness; the reference walk stops here.',
    'howTitle': 'How to read this map',
    'how': [
      'A screen is controller#action; routes sharing an action are collapsed.',
      'Relatedness is shared non-hub files (cosine). Hubs do not contribute.',
      'References are name-based static analysis: dynamic dispatch is missed.',
      'Routes are parsed from the DSL, not loaded from Rails.'
    ],
    'statLabels': ['screens', 'files reached', 'files/screen', 'unreached', 'missing controllers']
  }
}


# Both tables ship in the page. The Vietnamese names are the point of the map,
# but "Tồn kho" is a guess a reader cannot check, so the EN button swaps every
# label back to what the code actually calls it -- controller#action, the class,
# the pack. Two tables in the payload cost a few KB; a second build does not.
def prep(t):
    out = dict(t)
    out['stats'] = [[k, out['statLabels'][i], i >= 3] for i, k in enumerate(
        ['screens', 'files_reached', 'avg_files_per_screen', 'files_unreached', 'unresolved_controllers'])]
    del out['statLabels']
    return out


strings = prep(STRINGS[options.lang])
strings_alt = prep(STRINGS['ja' if options.lang == 'vi' else 'vi'])

# None means "open on the overview"; --default-screen pins a single screen instead
default_screen = options.default_screen or CONFIG.get('default_screen')

# Controller rollup: label, its screens, and how strongly it relates to other
# controllers. The score between two controllers is the strongest link between
# any of their screens -- a weaker rule would bury real coupling in averages.
# Every controller gets a function page -- API controllers too, since a Vue page's
# button lands there -- but only the product ones are listed on the overview.
controller_of = OrderedDict()
for i, s in enumerate(screens):
    controller_of.setdefault(s['controller'], []).append(i)

ctrl_index = {}
for i, c in enumerate(sorted(controller_of)):
    ctrl_index[c] = i

pairs = OrderedDict()
for ctrl, idxs in controller_of.items():
    a = ctrl_index[ctrl]
    for i in idxs:
        for r in screens[i].get('related_screens') or []:
            peer = screens[at[r['id']]] if r['id'] in at else None
            if not peer:
                continue
            b = ctrl_index.get(peer['controller'])
            if b is None or a == b:
                continue
            key = (a, b) if a < b else (b, a)
            if r['score'] > pairs.get(key, 0.0):
                pairs[key] = r['score']

ctrl_rel = {}
for (a, b), score in pairs.items():
    ctrl_rel.setdefault(a, []).append([b, score])
    ctrl_rel.setdefault(b, []).append([a, score])

# Chức năng theo cụm tên, không theo controller: Kiểm kê là `stocktakings`,
# `stocktakings/inventories`, `stocktakings/stocktaking_item_imports/preview`,
# `api/stocktakings/video_analysis`… — bỏ namespace kỹ thuật (api, v1, v2) phía trước
# rồi lấy đoạn đầu tiên làm khoá.
TECH_NS = ['api', 'v1', 'v2', 'v3', 'web', 'internal']


def feature_key(ctrl):
    segs = ruby_split(ctrl, '/')
    while len(segs) > 1 and (segs[0] in TECH_NS or _V_TOKEN.search(segs[0])):
        segs.pop(0)
    return re.sub(r'_v[0-9]+\Z', '', segs[0], count=1)


controllers = []
for ctrl in sorted(controller_of):
    idxs = controller_of[ctrl]
    sample = screens[idxs[0]]
    noun, qualifier = controller_label(sample)
    controllers.append(OrderedDict([
        ('c', ctrl),
        ('f', feature_of(ctrl)),
        ('lb', noun),
        ('lq', qualifier),
        ('sc', idxs),
        ('nf', max(screens[i].get('code_file_count') or 0 for i in idxs)),
        ('rel', stable_desc(ctrl_rel.get(ctrl_index[ctrl], []), lambda r: r[1])[:6]),
        ('hid', 0 if home_controller(sample) else 1),
        ('fk', feature_key(ctrl)),
        # Màn cũ: đã có controller cùng tên đuôi _v2 thay thế
        ('old', 1 if (ctrl + '_v2') in controller_of else 0),
    ]))

payload_meta = OrderedDict(meta)
payload_meta['generated_at'] = now_iso()
payload_meta['lang'] = options.lang
payload_meta['default_screen'] = default_screen
payload_meta['staging_url'] = CONFIG.get('staging_url')

payload = OrderedDict([
    ('meta', payload_meta),
    ('stats', impact.get('stats') or {}),
    ('strings', strings),
    ('stringsAlt', strings_alt),
    ('areasEn', AREAS_EN),
    ('ui', UI),
    ('files', files),
    ('screens', rows),
    ('controllers', controllers),
])

# Step 2's findings do not go into the page: three screens of file lists that
# nobody browses only pushed the map itself further away. They belong in
# summary.md, which is where they are read -- in a terminal, in a review.
issues = {
    'unresolved': impact.get('unresolved_controllers') or [],
    'hubs': [[h['file'], h['raw_fan_in']] for h in (impact.get('hubs') or [])],
    'unreached': [[u['file'], bucket_for(u['file'], u['bucket']), u.get('pack')]
                  for u in (impact.get('unreached_files') or [])],
}

# `</` inside the JSON would close the inline <script> early.
data_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
with open(os.path.join(HERE, '../assets/app.html'), encoding='utf-8') as _f:
    bundle = _f.read()
if '/*__DATA__*/' not in bundle:
    die('[doc] assets/app.html has no /*__DATA__*/ placeholder')

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as _f:
    _f.write(bundle.replace('/*__DATA__*/ null', data_json, 1))


def write_summary(path, meta, stats, controllers, screens, issues, strings, shared, files):
    """The page is for browsing; this file is for reading in a terminal, quoting in a
    review, and diffing between two scans. It carries the shape of the codebase and
    the findings -- not the per-screen file lists, which only a graph makes usable."""
    grouped = OrderedDict()
    for c in controllers:
        grouped.setdefault(c['f'], []).append(c)
    groups = stable_desc(list(grouped.items()), lambda kv: len(kv[1]))

    def label_of(x):
        return ' · '.join(p for p in (x['lb'], x['lq']) if p is not None and p != '')

    lines = []
    add = lines.append
    add('# %s' % strings['title'])
    add('')
    add('Sinh từ commit `%s` lúc %s.' % (meta.get('scan_commit'), meta.get('generated_at')))
    add('Bản đồ xem được: `.ai/code-map/doc/index.html`.')
    add('')
    add('## Tổng quan')
    add('')
    add('| Số | Nghĩa |')
    add('|---:|---|')
    add('| %s | route |' % (stats.get('routes') if stats.get('routes') is not None else '-'))
    add('| %d | màn hình (controller#action) |' % len(screens))
    add('| %d | controller có màn hình |' % len(controllers))
    add('| %d | nhóm tính năng |' % len(groups))
    add('| %s | file được ít nhất một màn dùng tới |'
        % (stats.get('files_reached') if stats.get('files_reached') is not None else '-'))
    add('| %d | file hub (bị dùng quá rộng, không đi tiếp khi lần vết) |' % len(issues['hubs']))
    add('')
    add('## Nhóm tính năng')
    add('')
    add('| Nhóm | Controller | Màn hình |')
    add('|---|---:|---:|')
    for name, lst in groups:
        add('| %s | %d | %d |' % (name, len(lst), sum(len(c['sc']) for c in lst)))
    add('')
    add('## Controller theo từng nhóm')
    add('')
    add('Số trong ngoặc là số file mà màn hình nặng nhất của controller đó chạm tới.')
    add('')
    for name, lst in groups:
        add('### %s' % name)
        add('')
        for c in stable_desc(lst, lambda c: c['nf']):
            add('**%s** — `%s`, %d màn' % (label_of(c), c['c'], len(c['sc'])))
            add('')
            for sc in stable_desc([screens[i] for i in c['sc']], lambda sc: sc['nf']):
                route = sc['ro'][0] if sc['ro'] else None
                where = '`%s %s`' % (route[0], route[1]) if route else '_không có route_'
                vue = ' · Vue `%s`' % files[sc['vue']][0] if sc['vue'] >= 0 else ''
                missing = '' if sc['ad'] == 1 else ' · **action không tồn tại**'
                lq = ' · %s' % sc['lq'] if sc['lq'] is not None else ''
                add('- %s%s — %s · %s file%s%s' % (sc['lb'], lq, where, sc['nf'], vue, missing))
            add('')
    add('## Nhóm nào dùng chung file với nhóm nào')
    add('')
    add('Đếm số file non-hub mà hai nhóm cùng chạm tới. Cặp ở đầu bảng là chỗ')
    add('sửa cho nhóm này dễ làm vỡ nhóm kia nhất.')
    add('')
    # Counted from each screen's own code_files, not from shared_files[].screens:
    # step 2 truncates that list at 20 entries, which would silently undercount
    # exactly the widest-reaching files.
    area_of = {}
    for c in controllers:
        for i in c['sc']:
            area_of[i] = c['f']
    areas_by_file = OrderedDict()
    for i, sc in enumerate(screens):
        area = area_of.get(i)
        if area is None:
            continue
        for fi, _depth, hub in sc['cfs']:
            if hub == 0:
                areas_by_file.setdefault(fi, OrderedDict())[area] = True
    pair = OrderedDict()
    for areas in areas_by_file.values():
        for a, b in itertools.combinations(sorted(areas), 2):
            pair[(a, b)] = pair.get((a, b), 0) + 1
    add('| Nhóm A | Nhóm B | File chung |')
    add('|---|---|---:|')
    for (a, b), n in stable_desc(list(pair.items()), lambda kv: kv[1])[:20]:
        add('| %s | %s | %d |' % (a, b, n))
    add('')
    add('## File rủi ro nhất khi sửa')
    add('')
    add('Xếp theo số màn hình chạm tới. Hub bị loại vì chúng ở khắp nơi nên')
    add('không phân biệt được gì; những file dưới đây mới là chỗ sửa một nơi')
    add('mà ảnh hưởng rộng.')
    add('')
    add('| File | Số màn chạm tới |')
    add('|---|---:|')
    for f in stable_desc([f for f in shared if not f['hub']], lambda f: f['screen_count'])[:25]:
        add('| `%s` | %d |' % (f['file'], f['screen_count']))
    add('')
    add('## Màn hình chạm tới nhiều file nhất')
    add('')
    add('| Màn hình | controller#action | File |')
    add('|---|---|---:|')
    for s in stable_desc(screens, lambda s: s['nf'] or 0)[:20]:
        add('| %s | `%s` | %s |' % (label_of(s), s['id'], s['nf']))
    add('')
    add('## Chỗ cần xem lại')
    add('')
    add('### Route trỏ vào controller không tồn tại (%d)' % len(issues['unresolved']))
    add('')
    for c in issues['unresolved']:
        add('- `%s`' % c)
    add('')
    add('### File bị dùng chung rộng nhất (%d hub)' % len(issues['hubs']))
    add('')
    for f, n in stable_desc(issues['hubs'], lambda h: h[1])[:15]:
        add('- `%s` — %d nơi gọi' % (f, n))
    add('')
    add('### File không màn hình nào chạm tới (%d)' % len(issues['unreached']))
    add('')
    add('Đây là ứng viên để rà, không phải bằng chứng code chết: job, rake task,')
    add('console và metaprogramming đều không nằm trong đường đi từ route.')
    add('')
    by_dir = OrderedDict()
    for u in issues['unreached']:
        by_dir.setdefault('/'.join(ruby_split(u[0], '/')[:3]), []).append(u)
    for d, lst in sorted(by_dir.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        add('**%s/** — %d file' % (d, len(lst)))
        add('')
        for u in sorted(lst, key=lambda u: u[0]):
            add('- `%s`' % u[0])
        add('')
    add('## Dựng lại tài liệu này')
    add('')
    add('```')
    add('/map --refresh      quét lại code rồi dựng (khoảng 10 giây)')
    add('/map                chỉ dựng lại từ dữ liệu cũ (khoảng 1 giây)')
    add('```')
    add('')
    add('Thay đổi chưa commit sẽ không thấy: bước quét so với commit ghi trong meta.')
    add('')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


summary = os.path.join(os.path.dirname(OUT), 'summary.md')
write_summary(summary, payload['meta'], payload['stats'], controllers, rows, issues, strings,
              impact.get('shared_files') or [], files)

if options.open:
    subprocess.call(['open', OUT])

if not options.quiet:
    cwd = os.getcwd() + '/'
    err = sys.stderr
    err.write('[doc] page -> %s  (%s MB)\n' % (OUT.replace(cwd, '', 1),
                                               ruby_round(os.path.getsize(OUT) / 1024.0 / 1024, 2)))
    err.write('[doc] scan_commit=%s screens=%d files=%d lang=%s\n'
              % (meta.get('scan_commit'), len(rows), len(files), options.lang))
    grouped = OrderedDict()
    for c in controllers:
        grouped.setdefault(c['f'], []).append(c)
    err.write('[doc] areas: %d features, %d controllers, %d screens\n'
              % (len(grouped), len(controllers), sum(len(c['sc']) for c in controllers)))
    err.write('[doc] largest: %s\n' % ' '.join(
        '%s(%d)' % (k, len(v)) for k, v in stable_desc(list(grouped.items()), lambda kv: len(kv[1]))[:4]))
    err.write('[doc] names: %d/%d from the dictionary (%d%%)\n'
              % (exact, len(rows), ruby_round(exact * 100.0 / len(rows))))
    err.write('[doc] summary -> %s\n' % summary.replace(cwd, '', 1))
