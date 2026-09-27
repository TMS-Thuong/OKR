#!/usr/bin/env python3
# Code-map scanner for any language (KR1 step 1, generic mode).
#
# The Rails scanner (scan_code_map.py) understands Rails deeply. This one knows no
# framework in particular: it reads source files in any of the languages in LANGS,
# links them by imports and by the class names they mention, and finds the entry
# points ("screens") from ROUTE_RULES -- regexes for how a framework declares a
# route. It writes the SAME scan.json schema, so steps 2 and 3 need no change.
#
# Adding a language is a row in LANGS. Adding a framework is a row in ROUTE_RULES,
# or, for one project only, `routes.rules` in .claude/code-map.json.
#
# Python stdlib only, and only syntax that runs on Python 3.8.
#
# Usage:
#   python3 scan_generic.py [--root DIR] [--out FILE] [--config FILE] [--quiet]

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = 1

ap = argparse.ArgumentParser(prog='scan_generic.py')
ap.add_argument('--root', default='.', help='Repository root (default: .)')
ap.add_argument('--out', help='Output JSON path (default: <root>/.ai/code-map/raw/scan.json)')
ap.add_argument('--config', help='Project config (default: <root>/.claude/code-map.json)')
ap.add_argument('--since', help='Accepted for compatibility; generic mode always scans in full')
ap.add_argument('--routes-table', dest='routes_table', help='Ignored in generic mode')
ap.add_argument('--quiet', action='store_true')
options = ap.parse_args()

ROOT = os.path.abspath(options.root)
OUT = os.path.abspath(options.out or os.path.join(ROOT, '.ai', 'code-map', 'raw', 'scan.json'))
CONFIG_FILE = os.path.abspath(options.config or os.path.join(ROOT, '.claude', 'code-map.json'))
CONFIG = {}
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, encoding='utf-8') as _f:
        CONFIG = json.load(_f)
SCAN_CONFIG = CONFIG.get('scan') or {}


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def read_file(path):
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return ''
    if b'\x00' in data[:4096]:
        return ''
    return data.decode('utf-8', errors='replace')


def git(*args):
    try:
        p = subprocess.run(['git', '-C', ROOT] + list(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    return p.stdout.decode('utf-8', errors='replace') if p.returncode == 0 else None


def uniq(items):
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------
# One row per language. Everything the scanner knows about a language is here:
#   ext        file extensions
#   syntax     how to skip comments and strings (see strip_code)
#   body       how a function body ends: 'brace', 'indent' (Python) or 'end' (Ruby)
#   namespace  the package / namespace declaration, used to build full class names
#   types      a type declaration: group 1 = name, group 2 = parent (optional)
#   funcs      a function / method declaration: group 1 = name
#   imports    import statements: group 1 = the module / path / class imported
#   import_kind how an import string maps to a file:
#              'relative' ('./a', '../b'), 'python', 'fqn' (a.b.C -> the file declaring C
#              in package a.b), 'go' (module path -> directory), 'none' (names only)

C_LIKE = {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"', "'"]}

LANGS = OrderedDict([
    ('ruby', {
        'ext': ['.rb', '.rake'],
        'syntax': {'line': ['#'], 'block': [('=begin', '=end')], 'quotes': ['"', "'"]},
        'body': 'end',
        'types': r'^\s*(?:class|module)\s+([A-Z]\w*(?:::[A-Z]\w*)*)(?:\s*<\s*([A-Z][\w:]*))?',
        'funcs': r'^\s*def\s+(?:self\.)?([a-zA-Z_]\w*[?!=]?)',
        'imports': [r'''\brequire_relative\s*\(?\s*['"]([^'"]+)['"]'''],
        'import_kind': 'relative',
    }),
    ('python', {
        'ext': ['.py'],
        'syntax': {'line': ['#'], 'block': [], 'quotes': ['"', "'"], 'triple': True},
        'body': 'indent',
        'types': r'^\s*class\s+([A-Za-z_]\w*)\s*(?:\(\s*([\w.]+))?',
        'funcs': r'^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)',
        'imports': [r'^\s*from\s+(\.*[\w.]*)\s+import\b', r'^\s*import\s+([\w.]+)'],
        'import_kind': 'python',
    }),
    ('javascript', {
        'ext': ['.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts', '.vue', '.svelte'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"', "'", '`']},
        'body': 'brace',
        'types': r'^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+([A-Za-z_$][\w$]*)'
                 r'(?:\s*<[^>{]*>)?(?:\s+extends\s+([\w$.]+))?',
        'funcs': r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)'
                 r'|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?'
                 r'(?:function\b|\([^)]*\)\s*(?::\s*[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)'
                 r'|^\s+(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*'
                 r'(?!(?:if|for|while|switch|catch|return|function|constructor|super)\b)([A-Za-z_$][\w$]*)\s*'
                 r'(?:<[^>()]*>)?\s*\([^()]*\)\s*(?::\s*[^{;=]+)?\{',
        'imports': [r'''\bimport\s+(?:type\s+)?(?:[\w$*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]''',
                    r'''\bexport\s+(?:type\s+)?[\w$*{}\s,]+\s+from\s+['"]([^'"]+)['"]''',
                    r'''\brequire\(\s*['"]([^'"]+)['"]\s*\)''',
                    r'''\bimport\(\s*['"]([^'"]+)['"]\s*\)'''],
        'import_kind': 'relative',
    }),
    ('php', {
        'ext': ['.php'],
        'syntax': {'line': ['//', '#'], 'block': [('/*', '*/')], 'quotes': ['"', "'"], 'hash_attr': True},
        'body': 'brace',
        'namespace': r'^\s*namespace\s+([\w\\]+)\s*[;{]',
        'types': r'^\s*(?:final\s+|abstract\s+|readonly\s+)*(?:class|interface|trait|enum)\s+([A-Za-z_]\w*)'
                 r'(?:\s+extends\s+([\w\\]+))?',
        'funcs': r'^\s*(?:(?:public|protected|private|static|final|abstract)\s+)*function\s+&?\s*([A-Za-z_]\w*)',
        'imports': [r'^\s*use\s+([\w\\]+)(?:\s+as\s+\w+)?\s*;',
                    r'''\b(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]([^'"]+)['"]'''],
        'import_kind': 'fqn',
        'ns_sep': '\\',
    }),
    ('java', {
        'ext': ['.java'],
        'syntax': C_LIKE,
        'body': 'brace',
        'namespace': r'^\s*package\s+([\w.]+)\s*;',
        'types': r'^\s*(?:(?:public|protected|private|static|final|abstract|sealed|non-sealed|strictfp)\s+)*'
                 r'(?:class|interface|enum|record|@interface)\s+([A-Za-z_]\w*)(?:\s*<[^>{]*>)?'
                 r'(?:\s*\([^)]*\))?(?:\s+extends\s+([\w.]+))?',
        'funcs': r'^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|protected|private|static|final|abstract|'
                 r'synchronized|native|default)\s+)*(?:<[^>]+>\s+)?[\w<>\[\],.?\s]+?\s+([a-zA-Z_]\w*)\s*\(',
        'imports': [r'^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;'],
        'import_kind': 'fqn',
        'ns_sep': '.',
    }),
    ('kotlin', {
        'ext': ['.kt', '.kts'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"'], 'triple': True},
        'body': 'brace',
        'namespace': r'^\s*package\s+([\w.]+)',
        'types': r'^\s*(?:(?:public|internal|private|protected|open|abstract|sealed|data|enum|inner|value|'
                 r'annotation)\s+)*(?:class|interface|object)\s+([A-Za-z_]\w*)(?:[^:{]*:\s*([\w.]+))?',
        'funcs': r'^\s*(?:(?:public|internal|private|protected|open|override|suspend|inline|operator|'
                 r'infix|abstract|final)\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(',
        'imports': [r'^\s*import\s+([\w.]+)(?:\.\*)?'],
        'import_kind': 'fqn',
        'ns_sep': '.',
    }),
    ('csharp', {
        'ext': ['.cs'],
        'syntax': C_LIKE,
        'body': 'brace',
        'namespace': r'^\s*namespace\s+([\w.]+)',
        'types': r'^\s*(?:\[[^\]]*\]\s*)*(?:(?:public|internal|private|protected|static|sealed|abstract|partial|'
                 r'readonly|file)\s+)*(?:class|interface|struct|record|enum)\s+([A-Za-z_]\w*)'
                 r'(?:\s*<[^>{]*>)?(?:\s*\([^)]*\))?(?:\s*:\s*([\w.]+))?',
        'funcs': r'^\s*(?:(?:public|internal|private|protected|static|virtual|override|abstract|async|sealed|'
                 r'new|extern|unsafe|partial)\s+)+[\w<>\[\],.?\s]+?\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(',
        'imports': [r'^\s*using\s+(?:static\s+)?([\w.]+)\s*;'],
        'import_kind': 'namespace',
        'ns_sep': '.',
    }),
    ('go', {
        'ext': ['.go'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"', "'", '`']},
        'body': 'brace',
        'types': r'^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b',
        'funcs': r'^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(',
        'imports': [r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', r'^\s+(?:[\w.]+\s+)?"([^"]+)"\s*$'],
        'import_kind': 'go',
    }),
    ('rust', {
        'ext': ['.rs'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"']},
        'body': 'brace',
        'types': r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union|type)\s+([A-Za-z_]\w*)',
        'funcs': r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:(?:async|const|unsafe|extern\s+"[^"]*")\s+)*fn\s+([A-Za-z_]\w*)',
        'imports': [r'^\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)\s*;'],
        'import_kind': 'rust_mod',
    }),
    ('swift', {
        'ext': ['.swift'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"'], 'triple': True},
        'body': 'brace',
        'types': r'^\s*(?:(?:public|internal|private|fileprivate|open|final)\s+)*'
                 r'(?:class|struct|enum|protocol|actor|extension)\s+([A-Za-z_]\w*)(?:\s*:\s*([\w.]+))?',
        'funcs': r'^\s*(?:(?:public|internal|private|fileprivate|open|static|class|final|override|mutating|'
                 r'@\w+)\s+)*func\s+([A-Za-z_]\w*)',
        'imports': [],
        'import_kind': 'none',
    }),
    ('dart', {
        'ext': ['.dart'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"', "'"], 'triple': True},
        'body': 'brace',
        'types': r'^\s*(?:abstract\s+)?(?:class|mixin|enum|extension)\s+([A-Za-z_]\w*)(?:[^{]*?extends\s+([\w.]+))?',
        'funcs': r'^\s*(?:static\s+)?(?:Future<[^>]*>|void|[\w<>?]+)\s+([a-zA-Z_]\w*)\s*\(',
        'imports': [r'''^\s*(?:import|export|part)\s+['"]([^'"]+)['"]'''],
        'import_kind': 'relative',
    }),
    ('scala', {
        'ext': ['.scala'],
        'syntax': {'line': ['//'], 'block': [('/*', '*/')], 'quotes': ['"'], 'triple': True},
        'body': 'brace',
        'namespace': r'^\s*package\s+([\w.]+)',
        'types': r'^\s*(?:(?:case|abstract|sealed|final|implicit)\s+)*(?:class|trait|object)\s+([A-Za-z_]\w*)'
                 r'(?:[^{]*?extends\s+([\w.]+))?',
        'funcs': r'^\s*(?:(?:override|private|protected|final|implicit)\s+)*def\s+([A-Za-z_]\w*)',
        'imports': [r'^\s*import\s+([\w.]+)'],
        'import_kind': 'fqn',
        'ns_sep': '.',
    }),
])

EXT_LANG = {}
for _name, _spec in LANGS.items():
    for _e in _spec['ext']:
        EXT_LANG[_e] = _name
    _spec['types_re'] = re.compile(_spec['types'], re.M)
    _spec['funcs_re'] = re.compile(_spec['funcs'], re.M)
    _spec['imports_re'] = [re.compile(p, re.M) for p in _spec['imports']]
    _spec['namespace_re'] = re.compile(_spec['namespace'], re.M) if _spec.get('namespace') else None

KEYWORDS = set('''if for while switch catch return function new else do try case when with elif except
match loop select defer go func class def fn let var const sizeof typeof await yield throw'''.split())

# ---------------------------------------------------------------------------
# Comment / string stripping
# ---------------------------------------------------------------------------
# One pass over the source for every language. Returns two copies with every
# character at its original offset, so a position found in one is valid in all:
#   code  comments blanked, strings kept   -> routes, imports, declarations
#   bare  comments AND string bodies blanked -> identifiers, brace matching
# Newlines are always kept, so line numbers never shift.


def strip_code(src, syntax):
    n = len(src)
    code = list(src)
    bare = list(src)
    line_marks = syntax.get('line', [])
    blocks = syntax.get('block', [])
    quotes = syntax.get('quotes', [])
    triple = syntax.get('triple', False)
    hash_attr = syntax.get('hash_attr', False)

    def blank(buf, a, b):
        for k in range(a, b):
            if buf[k] != '\n':
                buf[k] = ' '

    i = 0
    while i < n:
        c = src[i]
        # block comment
        matched = False
        for open_, close in blocks:
            if src.startswith(open_, i) and (open_ != '=begin' or i == 0 or src[i - 1] == '\n'):
                j = src.find(close, i + len(open_))
                j = n if j < 0 else j + len(close)
                blank(code, i, j)
                blank(bare, i, j)
                i = j
                matched = True
                break
        if matched:
            continue
        # line comment
        for mark in line_marks:
            if src.startswith(mark, i):
                if mark == '#' and hash_attr and src.startswith('#[', i):
                    break
                j = src.find('\n', i)
                j = n if j < 0 else j
                blank(code, i, j)
                blank(bare, i, j)
                i = j
                matched = True
                break
        if matched:
            continue
        # strings
        if c in quotes:
            if triple and src.startswith(c * 3, i):
                j = src.find(c * 3, i + 3)
                j = n if j < 0 else j + 3
                blank(bare, i + 3, max(i + 3, j - 3))
                # docstring の中の `url(r'^$', views.home)` のような例をルートと読まないよう、
                # 三重引用符の中身は code からも消す
                blank(code, i + 3, max(i + 3, j - 3))
                i = j
                continue
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == c:
                    break
                if src[j] == '\n' and c != '`':
                    break
                j += 1
            end = min(j, n)
            blank(bare, i + 1, end)
            i = end + 1
            continue
        i += 1
    return ''.join(code), ''.join(bare)


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def match_brace(bare, open_pos):
    """bare[open_pos] は '{' / '(' / '['。対応する閉じ括弧の直後の位置を返す。"""
    pairs = {'{': '}', '(': ')', '[': ']'}
    opener = bare[open_pos]
    closer = pairs[opener]
    depth = 0
    i = open_pos
    n = len(bare)
    while i < n:
        ch = bare[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def split_args(text):
    """最上位のカンマで引数を分ける（括弧・波括弧・角括弧の中のカンマでは分けない）。"""
    out = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ',' and depth == 0:
            out.append((start, i))
            start = i + 1
    out.append((start, len(text)))
    # 末尾カンマ（`},\n);`）が作る空の引数は捨てる。残すと「最後の引数」が空になる
    while out and not text[out[-1][0]:out[-1][1]].strip():
        out.pop()
    return out


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

SKIP_DIRS = set('''.git .hg .svn node_modules vendor bower_components dist build out target bin obj coverage
tmp log logs public .next .nuxt .svelte-kit .venv venv env __pycache__ .mypy_cache .pytest_cache .tox
.gradle .idea .vscode .ai .claude Pods DerivedData .dart_tool migrations'''.split())
TEST_DIRS = set('test tests spec specs __tests__ __mocks__ testdata fixtures e2e cypress'.split())
TEST_FILE_RE = re.compile(r'(_test\.go|\.(test|spec)\.[jt]sx?|\.e2e-spec\.ts|_spec\.rb|(^|/)test_[^/]*\.py|'
                          r'_test\.py|Tests?\.(java|kt|cs|swift|scala)|Spec\.(kt|scala))$')


def discover():
    include = SCAN_CONFIG.get('include')
    exclude = SCAN_CONFIG.get('exclude') or []
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and d not in TEST_DIRS
                             and not d.startswith('.'))
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1]
            if ext not in EXT_LANG or name.endswith('.d.ts') or name.endswith('.min.js'):
                continue
            path = os.path.join(dirpath, name)
            r = rel(path)
            if TEST_FILE_RE.search(r):
                continue
            if include and not any(fnmatch.fnmatch(r, g) for g in include):
                continue
            if any(fnmatch.fnmatch(r, g) for g in exclude):
                continue
            files.append(path)
    return files


MONOREPO_DIRS = ('packages', 'apps', 'modules', 'libs', 'packs', 'services', 'src/modules')


def pack_of(r):
    for d in MONOREPO_DIRS:
        prefix = d + '/'
        if r.startswith(prefix):
            rest = r[len(prefix):]
            if '/' in rest:
                return rest.split('/', 1)[0]
    return None


BUCKET_DIRS = [
    ('models', {'models', 'model', 'entities', 'entity', 'domain', 'schemas', 'repositories', 'repository',
                'dao', 'orm', 'db'}),
    ('services', {'services', 'service', 'usecases', 'use_cases', 'usecase', 'application', 'interactors',
                  'features', 'logic', 'core'}),
    ('jobs', {'jobs', 'workers', 'tasks', 'queues', 'consumers', 'cron'}),
    ('mailers', {'mailers', 'mail', 'mails', 'notifications'}),
]
BUCKET_SUFFIX = [
    ('models', re.compile(r'(Entity|Model|Repository|Repo|Dao|Schema|Record)$|[._-](entity|model|repository|schema)$',
                          re.I)),
    ('services', re.compile(r'(Service|UseCase|Interactor|Manager)$|[._-](service|usecase)$', re.I)),
    ('jobs', re.compile(r'(Job|Worker|Task|Consumer)$|[._-](job|worker|task|consumer)$', re.I)),
    ('mailers', re.compile(r'(Mailer|Mail|Notification)$|[._-](mailer|mail)$', re.I)),
]


def bucket_of(r):
    stem = os.path.splitext(os.path.basename(r))[0]
    for bucket, rx in BUCKET_SUFFIX:
        if rx.search(stem):
            return bucket
    parts = set(p.lower() for p in r.split('/')[:-1])
    for bucket, names in BUCKET_DIRS:
        if parts & names:
            return bucket
    return 'lib'


# ---------------------------------------------------------------------------
# Per-file facts
# ---------------------------------------------------------------------------

class Source(object):
    def __init__(self, path):
        self.path = path
        self.rel = rel(path)
        self.lang = EXT_LANG[os.path.splitext(path)[1]]
        self.spec = LANGS[self.lang]
        self.src = read_file(path)
        self.code, self.bare = strip_code(self.src, self.spec['syntax'])
        ns = self.spec['namespace_re'].search(self.code) if self.spec['namespace_re'] else None
        self.namespace = ns.group(1) if ns else None
        self.types = []       # [(name, parent, pos)]
        for m in self.spec['types_re'].finditer(self.code):
            name = m.group(1)
            parent = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            self.types.append((name, parent, m.start(1)))
        self.funcs = []       # [(name, pos)]
        for m in self.spec['funcs_re'].finditer(self.code):
            idx = next((k for k in range(1, (m.lastindex or 0) + 1) if m.group(k)), None)
            if idx is None:
                continue
            name = m.group(idx)
            # 位置は名前の位置にする。m.start() だと `^\s*` が空白化したコメントを越えて
            # 何行も上から始まり、本文の切り出しを誤らせる
            if name not in KEYWORDS:
                self.funcs.append((name, m.start(idx)))
        self.imports = []     # raw import strings
        for rx in self.spec['imports_re']:
            for m in rx.finditer(self.code):
                self.imports.append((m.group(1), m.start()))

    def full_name(self, name):
        if self.namespace:
            sep = self.spec.get('ns_sep', '.')
            return (self.namespace + sep + name).replace('\\', '::').replace('.', '::')
        return name


# ---------------------------------------------------------------------------
# Resolving imports to files
# ---------------------------------------------------------------------------

class Index(object):
    def __init__(self, sources):
        self.sources = sources
        self.by_rel = dict((s.rel, s) for s in sources)
        self.type_files = defaultdict(list)   # short type name -> files
        self.fqn_file = {}                     # full name (a::b::C) -> file
        self.ns_files = defaultdict(list)      # namespace -> files
        self.func_files = defaultdict(list)    # function name -> files
        self.dir_files = defaultdict(list)     # directory -> files (Go packages)
        for s in sources:
            for name, _parent, _pos in s.types:
                if s.rel not in self.type_files[name]:
                    self.type_files[name].append(s.rel)
                self.fqn_file[s.full_name(name)] = s.rel
            if s.namespace:
                self.ns_files[s.namespace.replace('\\', '.')].append(s.rel)
            for name, _pos in s.funcs:
                if s.rel not in self.func_files[name]:
                    self.func_files[name].append(s.rel)
            self.dir_files[os.path.dirname(s.rel)].append(s.rel)
        self.go_module = self._go_module()
        self.js_aliases = self._js_aliases()

    def _go_module(self):
        p = os.path.join(ROOT, 'go.mod')
        if not os.path.exists(p):
            return None
        m = re.search(r'^module\s+(\S+)', read_file(p), re.M)
        return m.group(1) if m else None

    def _js_aliases(self):
        """tsconfig の paths（`@/*` -> `src/*` 等）。無ければよく使われる既定を試す。"""
        aliases = []
        for name in ('tsconfig.json', 'jsconfig.json', 'tsconfig.base.json'):
            p = os.path.join(ROOT, name)
            if not os.path.exists(p):
                continue
            text = re.sub(r'//[^\n]*|/\*.*?\*/', '', read_file(p), flags=re.S)
            text = re.sub(r',(\s*[}\]])', r'\1', text)
            try:
                opts = json.loads(text).get('compilerOptions') or {}
            except ValueError:
                continue
            base = opts.get('baseUrl') or '.'
            for key, targets in (opts.get('paths') or {}).items():
                if targets:
                    aliases.append((key.rstrip('*'), os.path.normpath(os.path.join(base, targets[0].rstrip('*')))))
            if opts.get('baseUrl'):
                aliases.append(('', os.path.normpath(base)))
        aliases += [('@/', 'src'), ('~/', 'src')]
        return aliases

    def unique_type(self, name):
        files = self.type_files.get(name) or []
        return files[0] if len(files) == 1 else None

    def _first_file(self, candidates):
        for c in candidates:
            c = os.path.normpath(c).replace(os.sep, '/')
            if c in self.by_rel:
                return c
        return None

    def _js_candidates(self, base):
        exts = ['', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.vue', '.svelte', '.dart', '.rb', '.php']
        out = [base + e for e in exts]
        out += [base + '/index' + e for e in ('.ts', '.tsx', '.js', '.jsx')]
        return out

    def resolve_import(self, s, spec):
        """import 文字列 -> リポジトリ内のファイル（見つからなければ None）。"""
        kind = s.spec['import_kind']
        here = os.path.dirname(s.rel)
        if kind == 'relative':
            if spec.startswith('.'):
                return self._first_file(self._js_candidates(os.path.join(here, spec)))
            for prefix, target in self.js_aliases:
                if prefix and spec.startswith(prefix):
                    return self._first_file(self._js_candidates(os.path.join(target, spec[len(prefix):])))
            if s.lang == 'dart' and spec.startswith('package:'):
                parts = spec.split('/', 1)
                if len(parts) == 2:
                    return self._first_file(['lib/' + parts[1]])
            if s.lang == 'ruby':
                return self._first_file(self._js_candidates(os.path.join(here, spec)))
            return self._first_file(self._js_candidates(os.path.join('src', spec)))
        if kind == 'python':
            dots = len(spec) - len(spec.lstrip('.'))
            mod = spec.lstrip('.').replace('.', '/')
            if dots:
                base = here
                for _ in range(dots - 1):
                    base = os.path.dirname(base)
                roots = [base]
            else:
                roots = ['', 'src', 'app']
            for r in roots:
                p = os.path.join(r, mod) if mod else r
                hit = self._first_file([p + '.py', os.path.join(p, '__init__.py')])
                if hit:
                    return hit
            return None
        if kind == 'fqn':
            fq = spec.replace('\\', '::').replace('.', '::').strip(':')
            if fq in self.fqn_file:
                return self.fqn_file[fq]
            if s.lang == 'php' and not spec.startswith('\\') and ('/' in spec or spec.endswith('.php')):
                return self._first_file([os.path.join(here, spec), spec])
            return None
        if kind == 'namespace':
            return None   # C#: `using` names a namespace, not a file; classes are matched by name
        if kind == 'go':
            if self.go_module and spec.startswith(self.go_module):
                d = spec[len(self.go_module):].strip('/')
                return ('dir', d)
            return None
        if kind == 'rust_mod':
            return self._first_file([os.path.join(here, spec + '.rs'), os.path.join(here, spec, 'mod.rs')])
        return None


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

IDENT_RE = re.compile(r'[A-Za-z_$][\w$]*')
# `Type name` (Java / C# / Dart), `Type $name` (PHP), `name: Type` (TS / Kotlin / Swift / Scala / Python)
FIELD_PATTERNS = [
    re.compile(r'\b(?P<type>[A-Z]\w*)(?:<[^<>;(){}]*>)?\??\s+\$?(?P<var>[a-z_]\w*)\s*[;,)=]'),
    re.compile(r'\b(?P<var>[a-z_]\w*)\s*:\s*(?P<type>[A-Z]\w*)'),
]
QUALIFIED_RE = re.compile(r'\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)')
TYPE_NAME_RE = re.compile(r'^[A-Z][A-Za-z0-9_]{2,}$')


class Linker(object):
    """ファイル・関数本文から、参照している他ファイルを割り出す。
    手がかりは2つ: (1) import で解決できたファイル、(2) 一意に定義されている型名。"""

    def __init__(self, index):
        self.index = index
        self.import_files = {}   # rel -> [files]
        self.import_names = {}   # rel -> {local name -> file}
        self.go_pkgs = {}        # rel -> {package alias -> dir}
        for s in index.sources:
            self._imports_of(s)

    def _imports_of(self, s):
        files = []
        names = {}
        pkgs = {}
        for spec, pos in s.imports:
            target = self.index.resolve_import(s, spec)
            if isinstance(target, tuple):   # Go package directory
                alias = self._go_alias(s.code, pos) or spec.rstrip('/').split('/')[-1]
                pkgs[alias] = target[1]
                continue
            if not target:
                continue
            files.append(target)
            for local in self._local_names(s, spec, pos):
                sub = None
                if s.lang == 'python' and spec.split(' ')[0] and local != '*':
                    # `from app.api.routes import users` は users.py（サブモジュール）を指すことが多い
                    sub = self.index.resolve_import(s, spec.rstrip('.') + ('.' if not spec.endswith('.') else '') + local)
                if sub and not isinstance(sub, tuple):
                    names[local] = sub
                    files.append(sub)
                else:
                    names[local] = target
        self.import_files[s.rel] = uniq(files)
        self.import_names[s.rel] = names
        self.go_pkgs[s.rel] = pkgs

    @staticmethod
    def _go_alias(code, pos):
        m = re.match(r'\s*(?:import\s+)?(\w+)\s+"', code[pos:pos + 200])
        return m.group(1) if m and m.group(1) not in ('import', '_') else None

    @staticmethod
    def _local_names(s, spec, pos):
        """import 文が手元に持ち込む名前。`ns.fn()` や `fn()` がどのファイルを指すかに使う。"""
        stmt = s.code[pos:pos + 400].split(';', 1)[0]
        if s.lang == 'javascript':
            head = stmt.split('from', 1)[0] if 'from' in stmt else ''
            names = re.findall(r'(?:\bas\s+|[{,]\s*|import\s+(?:type\s+)?|\*\s*as\s+)([A-Za-z_$][\w$]*)', head)
            m = re.match(r'\s*(?:const|let|var)\s+(\w+)\s*=\s*require', s.code[max(0, pos - 60):pos + 10])
            return [n for n in names if n not in ('import', 'type', 'from', 'as')] + ([m.group(1)] if m else [])
        if s.lang == 'python':
            m = re.match(r'\s*from\s+\S+\s+import\s+\(?([^)\n]*(?:\n[^)\n]*)*?)\)?\s*$', stmt.split('\n\n')[0], re.M)
            if m:
                out = []
                for part in m.group(1).replace('\n', ' ').split(','):
                    bits = part.strip().split()
                    if bits:
                        out.append(bits[-1])
                return out
            m = re.match(r'\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?', stmt)
            if m:
                return [m.group(2) or m.group(1).split('.')[0]]
            return []
        return [re.split(r'[.\\:]+', spec)[-1]]

    def field_types(self, s, pos):
        """pos を含むクラスのフィールド・コンストラクタ引数の {変数名: 型名}。
        DI で受け取ったサービス（`private final ArticleService articleService;`,
        `constructor(private articleService: ArticleService)`）は本文に型名が出てこないので、
        変数名から型を引けるようにする。"""
        best = None
        for name, _parent, dpos in s.types:
            q = s.bare.find('{', dpos) if s.spec['body'] == 'brace' else dpos
            if q < 0:
                continue
            end = match_brace(s.bare, q) if s.spec['body'] == 'brace' else (body_span(s, dpos) or (dpos, dpos))[1]
            if q <= pos < end and (best is None or q > best[0]):
                best = (q, end)
        if not best:
            return {}
        text = s.bare[best[0]:best[1]]
        out = {}
        for rx in FIELD_PATTERNS:
            for m in rx.finditer(text):
                var, typ = m.group('var'), m.group('type')
                if TYPE_NAME_RE.match(typ) and var not in out:
                    out[var] = typ
        return out

    def refs_in(self, s, text, fields=None):
        """text（s の一部または全部の bare コード）が参照するファイル。"""
        out = []
        idents = uniq(IDENT_RE.findall(text))
        for w in idents:
            typ = (fields or {}).get(w)
            if typ:
                f = self.index.unique_type(typ) or self.nearest_type(s.rel, typ)
                if f:
                    out.append(f)
        names = self.import_names.get(s.rel, {})
        own_types = set(t[0] for t in s.types)
        for w in idents:
            if w in names:
                out.append(names[w])
            elif TYPE_NAME_RE.match(w) and w not in own_types:
                f = self.index.unique_type(w) or self.nearest_type(s.rel, w)
                if f:
                    out.append(f)
        pkgs = self.go_pkgs.get(s.rel, {})
        if pkgs:
            for alias, name in uniq(QUALIFIED_RE.findall(text)):
                d = pkgs.get(alias)
                if d is None:
                    continue
                hit = [f for f in self.index.func_files.get(name, []) + self.index.type_files.get(name, [])
                       if os.path.dirname(f) == d]
                out.extend(hit[:1])
        if s.lang == 'go':
            # 同じパッケージ（同じディレクトリ）の関数・型は import なしで呼べる
            here = os.path.dirname(s.rel)
            for w in idents:
                for f in self.index.func_files.get(w, []) + self.index.type_files.get(w, []):
                    if f != s.rel and os.path.dirname(f) == here:
                        out.append(f)
                        break
        return [f for f in uniq(out) if f != s.rel]

    def nearest_type(self, here, name):
        """同名の型が複数あるとき、パスの共通部分が一番長い1つを選ぶ（同点なら選ばない）。
        機能ごとにフォルダを切る構成（Features/Articles/List.cs, Features/Comments/List.cs）で、
        名前だけでは決まらない参照を拾うため。"""
        files = self.index.type_files.get(name) or []
        if len(files) < 2:
            return None
        parts = here.split('/')[:-1]

        def shared(f):
            n = 0
            for a, b in zip(parts, f.split('/')[:-1]):
                if a != b:
                    break
                n += 1
            return n
        scored = sorted(((shared(f), f) for f in files), reverse=True)
        if scored[0][0] > scored[1][0] and scored[0][0] >= max(1, len(parts) - 1):
            return scored[0][1]
        return None

    def file_refs(self, s):
        return [f for f in uniq(self.import_files.get(s.rel, []) + self.refs_in(s, s.bare)) if f != s.rel]


# ---------------------------------------------------------------------------
# Function bodies
# ---------------------------------------------------------------------------

def body_span(s, def_pos):
    """def_pos から始まる関数の範囲 [def_pos, end)。見つからなければ None。
    引数の型（`show(Article $article)`, `create(@Body() dto: CreateDto)`）も実際の依存なので、
    本文だけでなく宣言の頭から含める。"""
    kind = s.spec['body']
    bare = s.bare
    if kind == 'brace':
        p = bare.find('(', def_pos)
        if p < 0 or bare.find('\n', def_pos, p) >= 0 and bare.count('\n', def_pos, p) > 3:
            return None
        after = match_brace(bare, p)
        m = re.compile(r'[^;{}]*?(\{|=>|;|=)').match(bare, after)
        if not m:
            return None
        if m.group(1) == '{':
            start = m.end() - 1
            return (def_pos, match_brace(bare, start))
        if m.group(1) in ('=>', '='):
            q = m.end()
            while q < len(bare) and bare[q] in ' \t\n':
                q += 1
            if q < len(bare) and bare[q] == '{':
                return (def_pos, match_brace(bare, q))
            end = bare.find('\n', q)
            return (def_pos, len(bare) if end < 0 else end)
        return None
    line_start = bare.rfind('\n', 0, def_pos) + 1
    indent = len(bare[line_start:def_pos]) - len(bare[line_start:def_pos].lstrip())
    first_nl = bare.find('\n', def_pos)
    if first_nl < 0:
        return (def_pos, len(bare))
    i = first_nl + 1
    end = len(bare)
    while i < len(bare):
        nl = bare.find('\n', i)
        line = bare[i:len(bare) if nl < 0 else nl]
        stripped = line.strip()
        if stripped:
            ind = len(line) - len(line.lstrip())
            if kind == 'indent' and ind <= indent:
                end = i
                break
            if kind == 'end' and ind == indent and re.match(r'end\b', stripped):
                end = len(bare) if nl < 0 else nl
                break
        if nl < 0:
            break
        i = nl + 1
    return (def_pos, end)


def next_function(s, pos):
    """pos 以降で最初に定義される関数名と、その定義位置。デコレータ・注釈・属性は飛ばす。"""
    code = s.code
    n = len(code)
    i = pos
    for _ in range(40):
        while i < n and code[i] in ' \t\r\n':
            i += 1
        if i >= n:
            return None
        if code[i] == '@':
            m = re.compile(r'@[\w.]+').match(code, i)
            i = m.end() if m else i + 1
            while i < n and code[i] in ' \t':
                i += 1
            if i < n and code[i] == '(':
                i = match_brace(s.bare, i)
            continue
        if code[i] == '[' and s.lang == 'csharp':
            i = match_brace(s.bare, i)
            continue
        if code.startswith('#[', i):
            i = match_brace(s.bare, i + 1)
            continue
        break
    if s.lang == 'python':
        m = re.compile(r'(?:async\s+)?def\s+([A-Za-z_]\w*)').search(code, i)
        return (m.group(1), m.start()) if m else None
    rx = re.compile(r'\b(?:fn|fun|func|def|function)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)|([A-Za-z_$][\w$]*)\s*(?:<[^<>()]*>)?\s*\(')
    for m in rx.finditer(code, i):
        name = m.group(1) or m.group(2)
        if name in KEYWORDS or name in ('public', 'private', 'protected', 'static', 'async', 'override'):
            continue
        return (name, m.start())
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
# kind 'call'      a statement that declares a route and names its handler
#                  (Laravel Route::get, Express router.get, Django path, Go r.GET).
#                  Named groups: method, path, and either controller + action, or
#                  handler (a name such as `usersController.list`), or nothing, in
#                  which case the last argument of the call is the handler.
# kind 'decorator' an annotation on the handler itself (@Get, @GetMapping, [HttpGet],
#                  @router.get). The handler is the next function after it. The same
#                  pattern placed on a class supplies the class's path prefix.
# `lang` limits a rule to one language; `files` (regex on the path) to some files.
# `methods_in` extracts the HTTP method from the matched text when it is an argument.
# `expand` turns one declaration into several routes (resources, viewsets).

HTTP = r'(?P<method>get|post|put|patch|delete|options|head|all|any)'

ROUTE_RULES = [
    # --- PHP: Laravel ------------------------------------------------------
    {'name': 'laravel', 'lang': 'php', 'kind': 'call', 'files': r'(^|/)routes/',
     'pattern': r'''Route::''' + HTTP + r'''\(\s*['"](?P<path>[^'"]*)['"]\s*,\s*(?:\[\s*(?P<controller>[\w\\]+)::class\s*,\s*['"](?P<action>\w+)['"]\s*\]|['"](?P<ctrl2>[\w\\]+)@(?P<act2>\w+)['"]|(?P<ctrl3>[\w\\]+)::class)'''},
    {'name': 'laravel', 'lang': 'php', 'kind': 'call', 'files': r'(^|/)routes/',
     'pattern': r'''Route::match\(\s*\[(?P<methods>[^\]]*)\]\s*,\s*['"](?P<path>[^'"]*)['"]\s*,\s*(?:\[\s*(?P<controller>[\w\\]+)::class\s*,\s*['"](?P<action>\w+)['"]\s*\]|['"](?P<ctrl2>[\w\\]+)@(?P<act2>\w+)['"])'''},
    {'name': 'laravel', 'lang': 'php', 'kind': 'call', 'files': r'(^|/)routes/', 'expand': 'laravel_resource',
     'pattern': r'''Route::(?P<res>resource|apiResource)\(\s*['"](?P<path>[^'"]*)['"]\s*,\s*(?:['"](?P<ctrl2>[\w\\]+)['"]|(?P<controller>[\w\\]+)::class)(?P<opts>(?:[^;]|\n)*?)\)\s*(?:->|;)'''},
    # --- PHP: Symfony attributes ------------------------------------------
    {'name': 'symfony', 'lang': 'php', 'kind': 'decorator',
     'pattern': r'''#\[Route\(\s*(?:path:\s*)?['"](?P<path>[^'"]*)['"](?P<args>[^\]]*)\]''',
     'methods_in': r'''methods:\s*\[\s*['"](\w+)['"]'''},
    # --- JavaScript / TypeScript: Express, Koa-router, Fastify, Hono ------
    {'name': 'express', 'lang': 'javascript', 'kind': 'call',
     'pattern': r'''\b(?:app|router|server|api|routes|fastify|\w*[Rr]outer|\w*[Aa]pp)\s*\.\s*''' + HTTP + r'''\s*\(\s*['"`](?P<path>/[^'"`]*|)['"`]'''},
    # --- NestJS -----------------------------------------------------------
    {'name': 'nestjs', 'lang': 'javascript', 'kind': 'decorator',
     'pattern': r'''@(?P<method>Get|Post|Put|Patch|Delete|All|Options|Head)\(\s*(?:['"`](?P<path>[^'"`]*)['"`])?[^)]*\)''',
     'prefix': r'''@Controller\(\s*(?:['"`](?P<path>[^'"`]*)['"`]|\{[^}]*?path\s*:\s*['"`](?P<path2>[^'"`]*)['"`][^}]*\})?[^)]*\)'''},
    # --- Java / Kotlin: Spring --------------------------------------------
    {'name': 'spring', 'lang': ['java', 'kotlin'], 'kind': 'decorator',
     'pattern': r'''@(?P<method>Get|Post|Put|Patch|Delete)Mapping\b(?:\(\s*(?:(?:value|path)\s*=\s*)?[\[{]?\s*"?(?P<path>[^"),}\]]*)"?[^)]*\))?''',
     'prefix': r'''@RequestMapping\(\s*(?:(?:value|path)\s*=\s*)?[\[{]?\s*"(?P<path>[^"]*)"'''},
    {'name': 'spring', 'lang': ['java', 'kotlin'], 'kind': 'decorator', 'member_only': True,
     'pattern': r'''@RequestMapping\((?P<args>[^)]*)\)''',
     'prefix': r'''@RequestMapping\(\s*(?:(?:value|path)\s*=\s*)?[\[{]?\s*"(?P<path>[^"]*)"''',
     'path_in': r'''(?:(?:value|path)\s*=\s*)?[\[{]?\s*"([^"]*)"''',
     'methods_in': r'''method\s*=\s*[\[{]?\s*(?:RequestMethod\.)?(\w+)'''},
    # --- Kotlin: Ktor -----------------------------------------------------
    {'name': 'ktor', 'lang': 'kotlin', 'kind': 'call', 'inline': True,
     'pattern': r'''\b(?P<method>get|post|put|patch|delete)\(\s*"(?P<path>[^"]*)"\s*\)\s*\{'''},
    # --- C#: ASP.NET Core -------------------------------------------------
    {'name': 'aspnet', 'lang': 'csharp', 'kind': 'decorator',
     'pattern': r'''\[Http(?P<method>Get|Post|Put|Patch|Delete)(?:\(\s*"(?P<path>[^"]*)"[^)]*\))?\]''',
     'prefix': r'''\[Route\(\s*"(?P<path>[^"]*)"\s*\)\]'''},
    {'name': 'aspnet-minimal', 'lang': 'csharp', 'kind': 'call',
     'pattern': r'''\.Map(?P<method>Get|Post|Put|Patch|Delete)\(\s*"(?P<path>[^"]*)"'''},
    # --- Python: FastAPI, Flask, Starlette --------------------------------
    {'name': 'fastapi', 'lang': 'python', 'kind': 'decorator',
     'pattern': r'''@\w+\.(?P<method>get|post|put|patch|delete|options|head|route|api_route)\(\s*(?:['"](?P<path>[^'"]*)['"])?(?P<args>[^\n]*)''',
     'methods_in': r'''methods\s*=\s*\[\s*['"](\w+)['"]''',
     'file_prefix': r'''(?:APIRouter|Blueprint)\([^)]*?(?:prefix|url_prefix)\s*=\s*['"](?P<path>[^'"]*)['"]'''},
    # --- Python: Django, Django REST framework ----------------------------
    {'name': 'django', 'lang': 'python', 'kind': 'call', 'files': r'urls\.py$', 'expand': 'python_view',
     'pattern': r'''\b(?:path|re_path|url)\(\s*r?['"](?P<path>[^'"]*)['"]\s*,\s*(?!include\b)(?P<handler>[\w.]+)'''},
    {'name': 'drf-router', 'lang': 'python', 'kind': 'call', 'expand': 'drf_viewset',
     'pattern': r'''\.register\(\s*r?['"](?P<path>[^'"]*)['"]\s*,\s*(?P<handler>[\w.]+)'''},
    # --- Go: net/http, gin, echo, chi, fiber, gorilla ---------------------
    {'name': 'go', 'lang': 'go', 'kind': 'call',
     'pattern': r'''\b\w+\s*\.\s*(?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|Any|Get|Post|Put|Patch|Delete|HandleFunc|Handle)\(\s*"(?P<path>[^"]*)"'''},
    # --- Ruby: Sinatra, Roda-like DSLs (Rails has its own scanner) --------
    {'name': 'sinatra', 'lang': 'ruby', 'kind': 'call', 'inline': True,
     'pattern': r'''^\s*(?P<method>get|post|put|patch|delete)\s+['"](?P<path>/[^'"]*)['"]\s*(?:,[^\n]*)?\s*(?:do\b|\{)'''},
    # --- Rust: axum, actix ------------------------------------------------
    {'name': 'rust-attr', 'lang': 'rust', 'kind': 'decorator',
     'pattern': r'''#\[(?P<method>get|post|put|patch|delete)\(\s*"(?P<path>[^"]*)"'''},
    {'name': 'axum', 'lang': 'rust', 'kind': 'call',
     'pattern': r'''\.route\(\s*"(?P<path>[^"]*)"\s*,\s*(?P<method>get|post|put|patch|delete)\(\s*(?P<handler>[\w:]+)'''},
]

# 名前は関数でも、ハンドラではなく「別のルート表を差し込む」もの
NOT_HANDLERS = {'urls', 'include', 'router', 'routes', 'site', 'static'}

# 別ファイルのルート群に接頭辞を付けて取り込む書き方。group 'module' が取り込まれる側の名前。
MOUNT_RULES = [
    ('python', r'''\.include_router\(\s*(?P<module>[\w.]+?)(?:\.router)?\s*,(?P<args>[^)]*)\)''',
     r'''prefix\s*=\s*['"](?P<path>[^'"]*)['"]'''),
    ('python', r'''\.register_blueprint\(\s*(?P<module>[\w.]+?)(?:\.\w+)?\s*,(?P<args>[^)]*)\)''',
     r'''url_prefix\s*=\s*['"](?P<path>[^'"]*)['"]'''),
    ('javascript', r'''\.use\(\s*['"`](?P<path>/[^'"`]*)['"`]\s*,\s*(?:[\w.]+\s*,\s*)*(?P<module>[\w$]+)\s*\)''', None),
]

LARAVEL_RESOURCE = [('index', 'GET', ''), ('create', 'GET', 'create'), ('store', 'POST', ''),
                    ('show', 'GET', ':id'), ('edit', 'GET', ':id/edit'), ('update', 'PUT', ':id'),
                    ('destroy', 'DELETE', ':id')]
DRF_ACTIONS = [('list', 'GET', ''), ('create', 'POST', ''), ('retrieve', 'GET', ':pk'), ('update', 'PUT', ':pk'),
               ('partial_update', 'PATCH', ':pk'), ('destroy', 'DELETE', ':pk')]
DRF_MIXINS = {'ListModelMixin': ['list'], 'CreateModelMixin': ['create'], 'RetrieveModelMixin': ['retrieve'],
              'UpdateModelMixin': ['update', 'partial_update'], 'DestroyModelMixin': ['destroy'],
              'ModelViewSet': ['list', 'create', 'retrieve', 'update', 'partial_update', 'destroy'],
              'ReadOnlyModelViewSet': ['list', 'retrieve']}
# Django / DRF class-based views: which handler methods a generic view provides.
PY_VIEW_METHODS = [('get', 'GET'), ('post', 'POST'), ('put', 'PUT'), ('patch', 'PATCH'), ('delete', 'DELETE'),
                   ('list', 'GET'), ('retrieve', 'GET'), ('create', 'POST'), ('update', 'PUT'),
                   ('partial_update', 'PATCH'), ('destroy', 'DELETE')]
DRF_GENERIC = {'ListAPIView': ['list'], 'CreateAPIView': ['create'], 'RetrieveAPIView': ['retrieve'],
               'UpdateAPIView': ['update'], 'DestroyAPIView': ['destroy'], 'ListCreateAPIView': ['list', 'create'],
               'RetrieveUpdateAPIView': ['retrieve', 'update'], 'RetrieveDestroyAPIView': ['retrieve', 'destroy'],
               'RetrieveUpdateDestroyAPIView': ['retrieve', 'update', 'destroy']}


def compile_rules():
    rules = []
    for r in ROUTE_RULES + list((CONFIG.get('routes') or {}).get('rules') or []):
        r = dict(r)
        langs = r.get('lang')
        r['langs'] = set([langs] if isinstance(langs, str) else (langs or LANGS.keys()))
        r['re'] = re.compile(r['pattern'], re.M | (re.I if r.get('ignore_case') else 0))
        r['prefix_re'] = re.compile(r['prefix'], re.M) if r.get('prefix') else None
        r['file_prefix_re'] = re.compile(r['file_prefix'], re.M) if r.get('file_prefix') else None
        r['files_re'] = re.compile(r['files']) if r.get('files') else None
        rules.append(r)
    disabled = set((CONFIG.get('routes') or {}).get('disable') or [])
    return [r for r in rules if r.get('name') not in disabled]


def clean_route_path(path):
    """フレームワークごとの書き方を `/a/:id` に揃える。"""
    p = path or ''
    p = re.sub(r'\(\?P<(\w+)>[^)]*\)', r':\1', p)          # Django regex group
    p = re.sub(r'<(?:\w+:)?(\w+)>', r':\1', p)               # Django / Flask converter
    p = re.sub(r'\{(\w+)(?::[^}]*)?\??\}', r':\1', p)        # {id} / {id:int}
    p = p.replace('^', '').replace('$', '').replace('/?', '/')
    p = re.sub(r'\\(.)', r'\1', p)
    p = re.sub(r'/{2,}', '/', '/' + p.strip('/'))
    return p if p != '/' or not path else p


def join_paths(*parts):
    out = '/'.join(x.strip('/') for x in parts if x and x.strip('/'))
    return clean_route_path(out)


def snake(name):
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').lower()


CTRL_SUFFIX = re.compile(r'(Controllers?|APIView|ApiView|ViewSet|Views?|Api|API|Resources?|Handlers?|'
                         r'Endpoints?|Routers?|Routes?)$')
FILE_SUFFIX = re.compile(r'([._-](controllers?|routes?|router|routers|views|handlers?|resource|endpoints?|api))$')
GENERIC_STEMS = {'views', 'routes', 'router', 'routers', 'urls', 'handlers', 'handler', 'controller',
                 'controllers', 'index', 'main', 'api', 'app', 'server', 'endpoints', 'resource'}


class Route(object):
    def __init__(self, method, path, file, line, action, owner_file, owner_class, body, source, raw, pos=0):
        self.pos = pos
        self.method = method
        self.path = path
        self.file = file              # where the route is declared
        self.line = line
        self.action = action
        self.owner_file = owner_file  # where the handler lives
        self.owner_class = owner_class
        self.body = body              # (start, end) of the handler body in owner_file, or None
        self.source = source
        self.raw = raw


class RouteFinder(object):
    def __init__(self, index, rules):
        self.index = index
        self.rules = rules
        self.routes = []
        self.used = set()
        self.bodies = {}

    def run(self):
        for s in self.index.sources:
            for r in self.rules:
                if s.lang not in r['langs']:
                    continue
                if r['files_re'] and not r['files_re'].search(s.rel):
                    continue
                if r['kind'] == 'call':
                    self._calls(s, r)
                else:
                    self._decorators(s, r)
        return self.routes

    # --- helpers -----------------------------------------------------------

    def _methods(self, m, r):
        gd = m.groupdict()
        if gd.get('methods'):
            return [x.upper() for x in re.findall(r'\w+', gd['methods'])]
        if r.get('methods_in'):
            found = re.findall(r['methods_in'], m.group(0), re.I)
            if found:
                return [x.upper() for x in found]
        meth = (gd.get('method') or r.get('method') or 'ANY').upper()
        if meth in ('ROUTE', 'API_ROUTE', 'HANDLE', 'HANDLEFUNC', 'ALL'):
            meth = 'ANY'
        return [meth]

    def _add(self, methods, path, s, pos, action, owner, owner_class, body, r):
        # 同名の別関数（C# / Java のオーバーロード Get() と Get(slug)）は別の画面にする
        if body is not None:
            seen = self.bodies.setdefault((owner, owner_class, action), body[0])
            if seen != body[0]:
                n = 2
                while self.bodies.get((owner, owner_class, '%s_%d' % (action, n)), body[0]) != body[0]:
                    n += 1
                action = '%s_%d' % (action, n)
                self.bodies[(owner, owner_class, action)] = body[0]
        line = line_of(s.code, pos)
        raw = s.code[pos:s.code.find('\n', pos) if s.code.find('\n', pos) > 0 else pos + 200].strip()[:200]
        for meth in methods:
            key = (meth, path, owner, action)
            if key in self.used:
                continue
            self.used.add(key)
            self.routes.append(Route(meth, path, s.rel, line, action, owner, owner_class, body, r['name'], raw, pos))

    def _class_at(self, s, pos):
        """pos を本文に含む最も内側の型。 (name, decl_pos, body_start, body_end) または None。"""
        best = None
        for name, _parent, dpos in s.types:
            span = self._type_body(s, dpos)
            if span and span[0] <= pos < span[1]:
                if best is None or span[0] > best[2]:
                    best = (name, dpos, span[0], span[1])
        return best

    def _type_body(self, s, dpos):
        if s.spec['body'] == 'brace':
            q = s.bare.find('{', dpos)
            semi = s.bare.find(';', dpos)
            if q < 0 or (0 <= semi < q and s.lang not in ('kotlin', 'scala')):
                return None
            return (q, match_brace(s.bare, q))
        span = body_span(s, dpos)
        return span

    def _prefix_for(self, s, r, cls):
        """クラスに付いたパスの接頭辞（@Controller('x'), @RequestMapping("x"), [Route("x")]）。"""
        if not (r['prefix_re'] and cls):
            return ''
        name, dpos = cls[0], cls[1]
        # bare で探す。code だと "articles/{slug}" の中の `}` を拾ってしまう
        window_start = s.bare.rfind('}', 0, dpos) + 1
        found = ''
        for m in r['prefix_re'].finditer(s.code, window_start, dpos):
            between = s.bare[m.end():dpos]
            if '{' in between or ';' in between:
                continue
            gd = m.groupdict()
            found = gd.get('path') or gd.get('path2') or ''
        base = CTRL_SUFFIX.sub('', name)
        return found.replace('[controller]', base.lower()).replace('[Controller]', base)

    def _file_prefix(self, s, r):
        if not r['file_prefix_re']:
            return ''
        m = r['file_prefix_re'].search(s.code)
        return m.group('path') if m else ''

    # --- 'call' rules -------------------------------------------------------

    def _calls(self, s, r):
        for m in r['re'].finditer(s.code):
            gd = m.groupdict()
            path = clean_route_path(gd.get('path'))
            methods = self._methods(m, r)
            controller = gd.get('controller') or gd.get('ctrl2') or gd.get('ctrl3')
            action = gd.get('action') or gd.get('act2')
            if r.get('expand') == 'laravel_resource':
                self._laravel_resource(s, m, r, path, controller)
                continue
            if controller:
                owner = self._resolve_class(s, controller)
                act = action or ('__invoke' if gd.get('ctrl3') else 'index')
                body = self._func_body(self.index.by_rel.get(owner[0]), act)
                self._add(methods, path, s, m.start(), act, owner[0], owner[1], body, r)
                continue
            handler = gd.get('handler')
            inline_body = None
            if not handler:
                handler, inline_body = self._last_arg(s, m, r)
            if handler:
                handler = re.sub(r'\.as_view$', '', handler)
                if re.split(r'[.:\\]+', handler)[-1] in NOT_HANDLERS:
                    continue
            if r.get('expand') in ('python_view', 'drf_viewset'):
                self._python_view(s, m, r, path, methods, handler)
                continue
            if handler and not inline_body:
                owner_file, owner_class, act, body = self._resolve_handler(s, handler)
                self._add(methods, path, s, m.start(), act, owner_file, owner_class, body, r)
            else:
                act = '%s_%s' % (methods[0].lower(), snake(path.replace(':', '')) or 'root')
                self._add(methods, path, s, m.start(), act, s.rel, None, inline_body, r)

    def _last_arg(self, s, m, r):
        """呼び出しの最後の引数。名前ならハンドラ名、関数リテラルなら本文の範囲を返す。"""
        bare = s.bare
        if r.get('inline'):
            q = bare.find('{', m.start())
            return None, (q, match_brace(bare, q)) if q >= 0 else None
        open_paren = bare.rfind('(', m.start(), m.end())
        if open_paren < 0:
            return None, None
        close = match_brace(bare, open_paren)
        inner_start = open_paren + 1
        args = split_args(bare[inner_start:close - 1])
        if len(args) < 2:
            return None, None
        a, b = args[-1]
        text = s.code[inner_start + a:inner_start + b].strip()
        if re.match(r'^[\w$.:\\]+$', text) and not re.match(r'^\d', text):
            return text, None
        m2 = re.match(r'^([\w$.]+)\.as_view\(', text)
        if m2:
            return m2.group(1), None
        return None, (inner_start + a, inner_start + b)

    def _resolve_class(self, s, name):
        """クラス名 -> (定義ファイル, クラス名)。見つからなければ宣言元ファイルに寄せる。"""
        short = re.split(r'[\\.:]+', name)[-1]
        fq = name.replace('\\', '::').replace('.', '::').strip(':')
        if fq in self.index.fqn_file:
            return (self.index.fqn_file[fq], short)
        files = self.index.type_files.get(short) or []
        if len(files) == 1:
            return (files[0], short)
        if files:
            # 同名クラスが複数: 名前空間の末尾が一番長く一致するものを選ぶ
            parts = [p.lower() for p in re.split(r'[\\.:/]+', name)]
            best = max(files, key=lambda f: sum(1 for p in parts if p in f.lower()))
            return (best, short)
        return (s.rel, short)

    def _resolve_handler(self, s, handler):
        """`listUsers` / `users.list` / `views.user_list` / `handlers.ListUsers` をたどる。"""
        parts = [p for p in re.split(r'[.:\\]+', handler) if p]
        action = parts[-1]
        names = LINKER.import_names.get(s.rel, {})
        if len(parts) >= 2:
            head = parts[-2]
            if head in names:                                   # imported module / object
                target = self.index.by_rel.get(names[head])
                return (names[head], self._class_of_func(target, action), action,
                        self._func_body(target, action))
            pkgs = LINKER.go_pkgs.get(s.rel, {})
            if head in pkgs:                                   # Go package
                for f in self.index.func_files.get(action, []):
                    if os.path.dirname(f) == pkgs[head]:
                        target = self.index.by_rel[f]
                        return (f, None, action, self._func_body(target, action))
            owner = self._resolve_class(s, head)
            if owner[0] != s.rel or head in [t[0] for t in s.types]:
                target = self.index.by_rel.get(owner[0])
                return (owner[0], owner[1], action, self._func_body(target, action))
        if action in names:
            target = self.index.by_rel.get(names[action])
            return (names[action], None, action, self._func_body(target, action))
        if any(f[0] == action for f in s.funcs):
            return (s.rel, self._class_of_func(s, action), action, self._func_body(s, action))
        here = os.path.dirname(s.rel)
        same_dir = [f for f in self.index.func_files.get(action, []) if os.path.dirname(f) == here]
        cands = same_dir or self.index.func_files.get(action, [])
        if len(cands) >= 1 and (same_dir or len(cands) == 1):
            target = self.index.by_rel[cands[0]]
            return (cands[0], self._class_of_func(target, action), action, self._func_body(target, action))
        owner = self._resolve_class(s, action)
        if owner[0] != s.rel:
            return (owner[0], owner[1], '__invoke', None)
        return (s.rel, None, action, None)

    def _class_of_func(self, s, name):
        if s is None:
            return None
        for fname, pos in s.funcs:
            if fname == name:
                cls = self._class_at(s, pos)
                return cls[0] if cls else None
        return None

    @staticmethod
    def _func_body(s, name):
        if s is None:
            return None
        for fname, pos in s.funcs:
            if fname == name:
                return body_span(s, pos)
        return None

    def _laravel_resource(self, s, m, r, path, controller):
        owner = self._resolve_class(s, controller)
        opts = m.group('opts') or ''
        only = re.search(r'''['"]only['"]\s*=>\s*\[([^\]]*)\]|->only\(\s*\[([^\]]*)\]''', opts)
        except_ = re.search(r'''['"]except['"]\s*=>\s*\[([^\]]*)\]|->except\(\s*\[([^\]]*)\]''', opts)
        tail = s.code[m.end():m.end() + 300].split(';', 1)[0]
        only = only or re.search(r'''->only\(\s*\[([^\]]*)\]''', tail)
        except_ = except_ or re.search(r'''->except\(\s*\[([^\]]*)\]''', tail)
        only_set = set(re.findall(r'\w+', (only.group(1) or only.group(2) or '') if only and only.lastindex else '')) \
            if only else None
        except_set = set(re.findall(r'\w+', ''.join(g or '' for g in except_.groups()))) if except_ else set()
        for action, meth, suffix in LARAVEL_RESOURCE:
            if m.group('res') == 'apiResource' and action in ('create', 'edit'):
                continue
            if only_set is not None and action not in only_set:
                continue
            if action in except_set:
                continue
            body = self._func_body(self.index.by_rel.get(owner[0]), action)
            self._add([meth], join_paths(path, suffix), s, m.start(), action, owner[0], owner[1], body, r)

    def _python_view(self, s, m, r, path, methods, handler):
        if not handler:
            return
        owner_file, owner_class, act, body = self._resolve_handler(s, handler)
        target = self.index.by_rel.get(owner_file)
        cls_name = re.split(r'[.:]+', handler)[-1]
        cls = None
        if target:
            for name, parent, dpos in target.types:
                if name == cls_name:
                    cls = (name, parent, dpos)
        if not cls:
            self._add(methods if methods != ['ANY'] or True else methods, path, s, m.start(), act,
                      owner_file, owner_class, body, r)
            return
        span = self._type_body(target, cls[2]) or (cls[2], len(target.bare))
        defined = [f for f, pos in target.funcs if span[0] <= pos < span[1]]
        parents = re.findall(r'\w+', target.code[cls[2]:target.code.find(':', cls[2]) if target.code.find(':', cls[2]) > 0 else cls[2]])
        if r.get('expand') == 'drf_viewset':
            provided = [a for a, _m, _s in DRF_ACTIONS if a in defined]
            for p in parents:
                provided += DRF_MIXINS.get(p, [])
            for action, meth, suffix in DRF_ACTIONS:
                if action in provided:
                    self._add([meth], join_paths(path, suffix), s, m.start(), action, owner_file, cls_name,
                              self._func_body_in(target, action, span), r)
            return
        wanted = [a for a, _meth in PY_VIEW_METHODS if a in defined]
        for p in parents:
            wanted += DRF_GENERIC.get(p, [])
        provided = [(a, meth) for a, meth in PY_VIEW_METHODS if a in wanted]
        if not provided:
            provided = [('dispatch', 'ANY')]
        for action, meth in provided:
            # クラスベースのビューは serializer_class などクラス属性で依存を持つので、クラス全体を本文とする
            self._add([meth], path, s, m.start(), action, owner_file, cls_name, span, r)

    @staticmethod
    def _func_body_in(s, name, span):
        for fname, pos in s.funcs:
            if fname == name and span[0] <= pos < span[1]:
                return body_span(s, pos)
        return None

    # --- 'decorator' rules --------------------------------------------------

    def _decorators(self, s, r):
        file_prefix = self._file_prefix(s, r)
        for m in r['re'].finditer(s.code):
            cls = self._class_at(s, m.start())
            if r['prefix_re'] and cls is None and r.get('prefix') and r['prefix_re'].match(s.code, m.start()):
                continue
            if cls is None and r.get('member_only'):
                continue
            if cls is None and s.lang in ('java', 'kotlin', 'csharp') and r.get('prefix'):
                continue   # a class-level annotation, read by _prefix_for
            gd = m.groupdict()
            path = gd.get('path')
            if path is None and r.get('path_in'):
                pm = re.search(r['path_in'], gd.get('args') or '')
                path = pm.group(1) if pm else ''
            fn = next_function(s, m.end())
            if not fn:
                continue
            name, pos = fn
            prefix = self._prefix_for(s, r, cls) if cls else ''
            full = join_paths(file_prefix, prefix, path or '')
            self._add(self._methods(m, r), full, s, m.start(), name, s.rel, cls[0] if cls else None,
                      body_span(s, pos), r)


def apply_mounts(index, routes):
    """`include_router(users.router, prefix="/user")` のように別ファイルで付く接頭辞を、
    取り込まれたファイルのルートに足す。取り込みが何段あっても親から順に積み上げる。"""
    parent = {}   # file -> (parent file, prefix)
    for s in index.sources:
        for lang, rx, prefix_rx in MOUNT_RULES:
            if s.lang != lang:
                continue
            for m in re.finditer(rx, s.code):
                alias = m.group('module').split('.')[0]
                target = LINKER.import_names.get(s.rel, {}).get(alias)
                if not target:
                    target = index._first_file(index._js_candidates(os.path.join(os.path.dirname(s.rel), alias))
                                               + [os.path.join(os.path.dirname(s.rel), alias + '.py'),
                                                  os.path.join(os.path.dirname(s.rel), alias, '__init__.py')])
                if not target or target == s.rel:
                    continue
                if prefix_rx:
                    pm = re.search(prefix_rx, m.group('args') or '')
                    prefix = pm.group('path') if pm else ''
                else:
                    prefix = m.group('path')
                parent.setdefault(target, (s.rel, prefix))
    # `articles/__init__.py` が `from .api import router` で中身を再公開しているとき、
    # 取り込み先は __init__.py でも実際のルートは api.py 側にある。再公開をたどって親子をつなぐ。
    for f in list(parent):
        if os.path.basename(f) in ('__init__.py', 'index.ts', 'index.js'):
            for g in LINKER.import_files.get(f, []):
                parent.setdefault(g, (f, ''))

    go_prefix_funcs = go_group_prefixes(index)

    def full(f, depth=0):
        if f not in parent or depth > 10:
            return ''
        p, prefix = parent[f]
        return join_paths(full(p, depth + 1), prefix)

    for r in routes:
        pre = full(r.file)
        for (f, start, end), gp in go_prefix_funcs.items():
            if f == r.file and start <= r.pos < end:
                pre = join_paths(pre, gp)
                break
        if pre and pre != '/':
            r.path = join_paths(pre, r.path)


_GO_GROUP_VAR = re.compile(r'(\w+)\s*:?=\s*(\w+)\.Group\(\s*"([^"]*)"')
_GO_GROUP_ARG = re.compile(r'\b(\w+)\.(\w+)\(\s*(\w+)\.Group\(\s*"([^"]*)"\s*\)')
_GO_GROUP_VAR_ARG = re.compile(r'\b(\w+)\.(\w+)\(\s*(\w+)\s*\)')


def go_group_prefixes(index):
    """Gin / Echo のルートグループ。`v1 := r.Group("/api")` と
    `users.UsersRegister(v1.Group("/users"))` から、UsersRegister の中で宣言されたルートに
    `/api/users` を付ける。戻り値は {(file, 関数本文の開始, 終了): 接頭辞}。"""
    out = {}
    for s in index.sources:
        if s.lang != 'go':
            continue
        var_prefix = {}
        for m in _GO_GROUP_VAR.finditer(s.code):
            var_prefix[m.group(1)] = join_paths(var_prefix.get(m.group(2), ''), m.group(3))
        calls = [(m.group(1), m.group(2), join_paths(var_prefix.get(m.group(3), ''), m.group(4)))
                 for m in _GO_GROUP_ARG.finditer(s.code)]
        calls += [(m.group(1), m.group(2), var_prefix[m.group(3)])
                  for m in _GO_GROUP_VAR_ARG.finditer(s.code) if m.group(3) in var_prefix]
        pkgs = LINKER.go_pkgs.get(s.rel, {})
        for alias, func, prefix in calls:
            d = pkgs.get(alias)
            if d is None:
                continue
            for f in index.func_files.get(func, []):
                if os.path.dirname(f) != d:
                    continue
                t = index.by_rel[f]
                for name, pos in t.funcs:
                    if name == func:
                        span = body_span(t, pos)
                        if span:
                            out[(f, span[0], span[1])] = prefix
    return out


# ---------------------------------------------------------------------------
# Entry points when a project declares no routes
# ---------------------------------------------------------------------------

MAIN_PATTERNS = {
    'python': r'''^if\s+__name__\s*==\s*['"]__main__['"]''',
    'go': r'^func\s+main\s*\(',
    'java': r'\bpublic\s+static\s+void\s+main\s*\(',
    'kotlin': r'^fun\s+main\s*\(',
    'csharp': r'\bstatic\s+(?:async\s+)?(?:void|int|Task)\s+Main\s*\(',
    'rust': r'^fn\s+main\s*\(',
    'swift': r'^@main\b',
    'dart': r'^(?:void\s+)?main\s*\(',
    'scala': r'\bdef\s+main\s*\(',
}


def entry_routes(index):
    out = []
    for s in index.sources:
        rx = MAIN_PATTERNS.get(s.lang)
        m = re.search(rx, s.code, re.M) if rx else None
        if m:
            # 起点のファイルは丸ごと起点の処理。`main()` を同じファイルで呼ぶだけの書き方でも依存を拾えるように
            out.append(Route('MAIN', '/' + s.rel, s.rel, line_of(s.code, m.start()), 'main', s.rel, None,
                             (0, len(s.bare)), 'entry-point', m.group(0)[:200]))
    pkg = os.path.join(ROOT, 'package.json')
    if os.path.exists(pkg):
        try:
            data = json.loads(read_file(pkg))
        except ValueError:
            data = {}
        bins = data.get('bin')
        cands = [data.get('main')] + (list(bins.values()) if isinstance(bins, dict) else [bins])
        for c in cands:
            if not c:
                continue
            r = os.path.normpath(c).replace(os.sep, '/')
            hit = index._first_file([r, r + '.js', r + '.ts', r.replace('dist/', 'src/').replace('.js', '.ts')])
            if hit:
                out.append(Route('MAIN', '/' + hit, hit, 1, 'main', hit, None, None, 'entry-point', 'package.json'))
    return out


# ---------------------------------------------------------------------------
# Build scan.json
# ---------------------------------------------------------------------------

def controller_key(route, taken):
    """画面 ID の前半。Rails の `inventories` と同じ見た目になるよう snake_case にする。"""
    if route.owner_class:
        base = snake(CTRL_SUFFIX.sub('', route.owner_class) or route.owner_class)
    else:
        stem = os.path.splitext(os.path.basename(route.owner_file))[0]
        stem2 = FILE_SUFFIX.sub('', stem)
        if not stem2 or stem2.lower() in GENERIC_STEMS:
            stem2 = os.path.basename(os.path.dirname(route.owner_file)) or stem
        base = snake(stem2)
    base = base or 'root'
    key = (route.owner_file, route.owner_class)
    if base in taken and taken[base] != key:
        parent = snake(os.path.basename(os.path.dirname(route.owner_file)))
        base = '%s/%s' % (parent, base) if parent else base
        n = 2
        cand = base
        while cand in taken and taken[cand] != key:
            cand = '%s_%d' % (base, n)
            n += 1
        base = cand
    taken[base] = key
    return base


def main():
    rules = compile_rules()
    sources = [Source(p) for p in discover()]
    index = Index(sources)
    global LINKER
    LINKER = Linker(index)

    routes = RouteFinder(index, rules).run()
    apply_mounts(index, routes)
    routes_source = 'generic:' + ','.join(sorted(set(r.source for r in routes))) if routes else 'generic:none'
    if not routes:
        routes = entry_routes(index)
        routes_source = 'generic:entry-points' if routes else 'generic:none'

    # 画面 = (所有ファイル, クラス) ごとのコントローラー
    taken = {}
    ctrl_of = OrderedDict()   # (owner_file, owner_class) -> key
    for r in sorted(routes, key=lambda r: (r.owner_file, r.owner_class or '', r.line)):
        k = (r.owner_file, r.owner_class)
        if k not in ctrl_of:
            ctrl_of[k] = controller_key(r, taken)

    by_file = dict((s.rel, s) for s in sources)
    controller_files = OrderedDict()
    for (owner_file, owner_class), key in ctrl_of.items():
        controller_files.setdefault(owner_file, []).append((owner_class, key))

    def entry_class(s):
        if not s.types:
            return None
        return s.full_name(s.types[0][0])

    def superclass(s):
        if not s.types or not s.types[0][1]:
            return None
        parent = re.split(r'[\\.:]+', s.types[0][1])[-1]
        f = index.unique_type(parent)
        if f and by_file.get(f):
            t = by_file[f]
            for name, _p, _pos in t.types:
                if name == parent:
                    return t.full_name(name)
        return parent

    buckets = OrderedDict((b, []) for b in ['controllers', 'models', 'services', 'jobs', 'mailers', 'lib'])
    ctrl_class_of = {}   # (owner_file, owner_class) -> controller_class written on the route
    for s in sources:
        refs = LINKER.file_refs(s)
        entry = OrderedDict([
            ('file', s.rel), ('pack', pack_of(s.rel)), ('class', entry_class(s)),
            ('superclass', superclass(s)), ('class_line', line_of(s.code, s.types[0][2]) if s.types else None),
            ('kind', 'class' if s.types else 'module'), ('lang', s.lang),
            ('public_methods', uniq([f[0] for f in s.funcs])),
            ('includes', []), ('loc', s.src.count('\n') + (1 if s.src and not s.src.endswith('\n') else 0)),
            ('references', refs),
            ('references_classes', []),
        ])
        if s.rel in controller_files:
            # 1ファイル1エントリ。クラス名はこのファイルのルートが指す controller_class と一致させる
            entry['class'] = 'ctrl::' + s.rel
            for owner_class, key in controller_files[s.rel]:
                ctrl_class_of[(s.rel, owner_class)] = entry['class']
            entry['actions'] = []
            entry['before_actions'] = []
            entry['filter_references'] = []
            entry['action_references'] = OrderedDict()
            buckets['controllers'].append(entry)
        else:
            buckets[bucket_of(s.rel)].append(entry)

    out_routes = []
    ctrl_entries = dict((e['file'], e) for e in buckets['controllers'])
    for r in routes:
        key = ctrl_of[(r.owner_file, r.owner_class)]
        e = ctrl_entries.get(r.owner_file)
        if e is not None:
            if r.action not in e['actions']:
                e['actions'].append(r.action)
            s = by_file.get(r.owner_file)
            if s is not None and r.body and r.action not in e['action_references']:
                fields = LINKER.field_types(s, r.body[0])
                e['action_references'][r.action] = LINKER.refs_in(s, s.bare[r.body[0]:r.body[1]], fields)
        out_routes.append(OrderedDict([
            ('method', r.method), ('path', r.path), ('controller', key), ('action', r.action),
            ('controller_class', ctrl_class_of.get((r.owner_file, r.owner_class), 'ctrl::' + r.owner_file)),
            ('source_file', r.file), ('source_line', r.line), ('source', r.source), ('raw', r.raw),
        ]))

    langs = defaultdict(int)
    for s in sources:
        langs[s.lang] += 1

    head = (git('rev-parse', 'HEAD') or '').strip() or None
    data = OrderedDict()
    data['meta'] = OrderedDict([
        ('schema_version', SCHEMA_VERSION),
        ('generated_at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')),
        ('commit', head), ('previous_commit', None), ('mode', 'full'), ('root', '.'),
        ('routes_source', routes_source), ('files_reparsed', None),
        ('scanner', 'generic'), ('languages', OrderedDict(sorted(langs.items(), key=lambda kv: -kv[1]))),
    ])
    data['packs'] = [OrderedDict([('name', p), ('path', None), ('enforce_dependencies', False),
                                  ('enforce_privacy', False), ('dependencies', [])])
                     for p in sorted(set(pack_of(s.rel) for s in sources if pack_of(s.rel)))]
    data['routes'] = out_routes
    data['routes_files'] = sorted(set(r.file for r in routes))
    for b, entries in buckets.items():
        data[b] = entries
    data['views'] = []
    data['frontend_pages'] = []
    data['frontend_api'] = []
    data['frontend_modules'] = []

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if not options.quiet:
        err = sys.stderr
        err.write('[code-map] generic scan -> %s\n' % rel(OUT))
        err.write('[code-map] commit=%s files=%d languages=%s\n' % (
            head, len(sources), ' '.join('%s:%d' % kv for kv in data['meta']['languages'].items())))
        err.write('[code-map] routes=%d (%s) controllers=%d models=%d services=%d jobs=%d lib=%d\n' % (
            len(out_routes), routes_source, len(buckets['controllers']), len(buckets['models']),
            len(buckets['services']), len(buckets['jobs']), len(buckets['lib'])))
        if not out_routes:
            err.write('[code-map] no routes and no entry points found: the map will have no screens. '
                      'Add routes.rules to .claude/code-map.json (see SKILL.md).\n')


LINKER = None

if __name__ == '__main__':
    main()
