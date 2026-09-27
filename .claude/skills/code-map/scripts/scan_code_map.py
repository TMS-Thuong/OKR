#!/usr/bin/env python3
# Code-map scanner (KR1 step 1: read the code).
#
# Extracts RAW structural facts from a Rails codebase into a single JSON file.
# It does NOT group files into features and it does NOT write human docs --
# those are steps 2 and 3 of KR1.
#
# Python stdlib only, and only syntax that runs on Python 3.8.
#
# Usage:
#   python3 scan_code_map.py [--root DIR] [--out FILE] [--since SHA]
#                            [--routes-table FILE] [--quiet]
#
# Ported from the Ruby version. Every regex goes through R(), which reproduces
# Ruby's defaults: `^` / `$` anchor at every line, `\w` / `\s` / `\d` are ASCII-only,
# and `\b` still treats Japanese as word characters. See _rubyish().

import argparse
import codecs
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = 1


# Ruby (Onigmo) の `\w` `\s` `\d` は ASCII だけに合うが、`\b` は Unicode の単語文字で
# 境界を判定する。つまり "日本blur" の "blur" の前は単語境界ではない。Python の re.ASCII は
# `\b` まで ASCII にしてしまい、"会社Company" から Company を拾う。そこで `\w` 等だけを
# ASCII の文字クラスに書き換え、`\b` は Unicode のまま残す。
_ASCII_CLASSES = {
    'w': ('[a-zA-Z0-9_]', 'a-zA-Z0-9_'),
    'd': ('[0-9]', '0-9'),
    's': ('[ \\t\\n\\x0b\\f\\r]', ' \\t\\n\\x0b\\f\\r'),
    'W': ('[^a-zA-Z0-9_]', None),
    'S': ('[^ \\t\\n\\x0b\\f\\r]', None),
}


def _rubyish(pattern):
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
            # `[]...]` や `[^]...]` の先頭の `]` は文字として扱う
            if i < n and pattern[i] == '^':
                out.append('^')
                i += 1
            if i < n and pattern[i] == ']':
                out.append(']')
                i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def R(pattern, dotall=False):
    """Ruby の既定と同じ意味の正規表現を作る。`/m` 付きの Ruby 正規表現は dotall=True。"""
    return re.compile(_rubyish(pattern), re.MULTILINE | (re.DOTALL if dotall else 0))


# Ruby String#scrub('?') と同じく、壊れたバイト列は '?' に置き換える
codecs.register_error('ruby_scrub', lambda e: ('?', e.end))

RUBY_WS = ' \t\n\v\f\r\x00'


def rstrip_all(s):
    return s.strip(RUBY_WS)


def ruby_split(s, sep):
    """Ruby の String#split(str) は末尾の空要素を捨てる。Python の split は捨てない。"""
    parts = s.split(sep)
    while parts and parts[-1] == '':
        parts.pop()
    return parts


def ruby_lines(s):
    """Ruby の String#lines。改行は '\\n' のみで分け、改行文字を残す。"""
    if not s:
        return []
    parts = s.split('\n')
    out = [p + '\n' for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def uniq(items):
    seen = set()
    out = []
    for x in items:
        k = tuple(x) if isinstance(x, list) else x
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def scan(regex, s):
    """Ruby の String#scan。グループがあれば各マッチのグループ列（不一致は None）を返す。"""
    if regex.groups == 0:
        return [m.group(0) for m in regex.finditer(s)]
    return [list(m.groups()) for m in regex.finditer(s)]


def scan1(regex, s):
    """グループ1つの scan を平らにしたもの（Ruby の scan(...).flatten）。"""
    return [g[0] for g in scan(regex, s)]


def flatten_compact(rows):
    return [x for row in rows for x in row if x is not None]


def cap(regex, s, group=1):
    """Ruby の str[/re/, n]。"""
    m = regex.search(s)
    return m.group(group) if m else None


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

ap = argparse.ArgumentParser(prog='scan_code_map.py')
ap.add_argument('--root', default='.', help='Repository root (default: .)')
ap.add_argument('--out', help='Output JSON path (default: <root>/.ai/code-map/raw/scan.json)')
ap.add_argument('--since', help='Incremental: only re-read files changed since SHA')
ap.add_argument('--routes-table', dest='routes_table',
                help='Optional `rails routes` text output, used as ground truth for routes')
ap.add_argument('--quiet', action='store_true', help='Suppress the summary printed to stderr')
options = ap.parse_args()

ROOT = os.path.abspath(options.root)
OUT = os.path.abspath(options.out or os.path.join(ROOT, '.ai', 'code-map', 'raw', 'scan.json'))

if not os.path.isdir(ROOT):
    sys.exit('not a directory: %s' % ROOT)

_ROOT_RE = re.compile(r'\A' + re.escape(ROOT) + r'/?')


def rel(path):
    return _ROOT_RE.sub('', path, count=1)


def read_file(path):
    try:
        with open(path, 'rb') as f:
            return f.read().decode('utf-8', errors='ruby_scrub')
    except (OSError, ValueError):
        return ''


def git(root, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    try:
        proc = subprocess.run(['git', '-C', root] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=env)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode('utf-8', errors='ruby_scrub')


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
# Ruby の Dir.glob と同じ規則で辿る: 隠しファイル・隠しディレクトリは見ない。
# シンボリックリンクのディレクトリには降りない（Ruby の `**/` も降りない）。

def glob_dirs(base, name_pattern):
    """base 直下で name_pattern（fnmatch）に合う、隠しでない項目。"""
    import fnmatch
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, n) for n in os.listdir(base)
            if not n.startswith('.') and fnmatch.fnmatchcase(n, name_pattern)]


def walk_files(base, exts=None, include_dirs=False):
    """base 以下を再帰的に辿り、拡張子が exts のファイルを返す。"""
    out = []
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        names = filenames + (dirnames if include_dirs else [])
        for n in names:
            if n.startswith('.'):
                continue
            if exts is None or any(n.endswith(e) for e in exts):
                out.append(os.path.join(dirpath, n))
    return out


def _app_roots():
    roots = []
    if os.path.isdir(os.path.join(ROOT, 'app')):
        roots.append(os.path.join(ROOT, 'app'))
    packs = os.path.join(ROOT, 'packs')
    found = [os.path.join(p, 'app') for p in glob_dirs(packs, '*') if os.path.exists(os.path.join(p, 'app'))]
    roots.extend(sorted(found))
    return roots


APP_ROOTS = _app_roots()

_PACK_RE = R(r'\Apacks/([^/]+)/')


def pack_of(path):
    return cap(_PACK_RE, rel(path))


def ruby_files(subdir):
    out = []
    for r in APP_ROOTS:
        out.extend(walk_files(os.path.join(r, subdir), ['.rb']))
    return sorted(out)


def view_files():
    out = []
    for r in APP_ROOTS:
        out.extend(f for f in walk_files(os.path.join(r, 'views')) if os.path.isfile(f))
    return sorted(out)


# ---------------------------------------------------------------------------
# Generic Ruby source parsing (regex-based, intentionally shallow)
# ---------------------------------------------------------------------------

_LINE_COMMENT_RE = R(r'^\s*#.*$')
_BLOCK_COMMENT_RE = R(r'=begin.*?=end', dotall=True)


def strip_comments(src):
    return _BLOCK_COMMENT_RE.sub('', _LINE_COMMENT_RE.sub('', src))


_MODULE_RE = R(r'^\s*module\s+([A-Z][\w:]*)\s*$')
_CLASS_RE = R(r'^\s*class\s+([A-Z][\w:]*)\s*(?:<\s*([A-Z][\w:.]*(?:\([^)]*\))?))?')


def primary_class(src):
    """[full_name, superclass, line, kind]。class が無ければ module の入れ子で名前を付ける。"""
    stack = []
    first_module_line = None
    line_no = 0
    for line in ruby_lines(src):
        line_no += 1
        m = _MODULE_RE.search(line)
        if m:
            stack.append(m.group(1))
            if first_module_line is None:
                first_module_line = line_no
            continue
        m = _CLASS_RE.search(line)
        if m:
            name = m.group(1)
            full = name if '::' in name else '::'.join(stack + [name])
            return [full, m.group(2), line_no, 'class']
    if stack:
        return ['::'.join(stack), None, first_module_line, 'module']
    return [None, None, None, None]


_VISIBILITY_RE = R(r'^\s*(private|protected)\s*$')
_DEF_RE = R(r'^\s*def\s+(?:self\.)?([a-zA-Z_][\w]*[?!=]?)')


def public_methods_of(src):
    methods = []
    for line in ruby_lines(src):
        if _VISIBILITY_RE.search(line):
            break
        m = _DEF_RE.search(line)
        if m:
            name = m.group(1)
            if name.startswith('_'):
                continue
            methods.append(name)
    return uniq(methods)


_INCLUDE_RE = R(r'^\s*(?:include|extend|prepend)\s+([A-Z][\w:]*)')


def includes_of(src):
    return uniq(scan1(_INCLUDE_RE, src))


CONST_RE = R(r'\b((?:[A-Z][A-Za-z0-9_]*)(?:::[A-Z][A-Za-z0-9_]*)*)')


def constants_in(src):
    return uniq(scan1(CONST_RE, src))


_BODY_DEF_RE = R(r'^(\s*)def\s+(?:self\.)?([a-zA-Z_]\w*[?!=]?)')
_ONE_LINE_END_RE = R(r';\s*end\s*$')
_ENDLESS_DEF_RE = R(r'^\s*def\s+[^=\s(]+(\([^)]*\))?\s*=\s*\S')
_END_RE = R(r'^\s*end\b')
_INDENT_RE = R(r'^\s*')


def method_bodies(src):
    """メソッド名 → 本文。`def` と同じ字下げの `end` までを本文とみなす（rubocop 整形前提）。"""
    bodies = {}
    lines = ruby_lines(src)
    i = 0
    while i < len(lines):
        m = _BODY_DEF_RE.search(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        name = m.group(2)
        if _ONE_LINE_END_RE.search(lines[i]) or _ENDLESS_DEF_RE.search(lines[i]):
            bodies[name] = lines[i]
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not (_END_RE.search(lines[j]) and len(_INDENT_RE.search(lines[j]).group(0)) == indent):
            j += 1
        bodies[name] = ''.join(lines[i:min(j, len(lines) - 1) + 1])
        i = j + 1
    return bodies


_FILTER_RE = R(r'^\s*(?:prepend_)?(?:before|around)_action\s+([^\n]+)')
_FILTER_SPLIT_RE = R(r'\b(?:only|except|if|unless|prepend):')
_FILTER_NAMES_RE = R(r'(?:\A|,\s*):(\w+)')
_ONLY_RE = R(r'only:\s*(\[[^\]]*\]|:\w+)')
_EXCEPT_RE = R(r'except:\s*(\[[^\]]*\]|:\w+)')
_SYM_WORD_RE = R(r':?(\w+)')


def filter_rules(src):
    """before_action :name[, only: [...] | except: [...]] -> [[name, only, except], ...]"""
    out = []
    for rest in scan1(_FILTER_RE, src):
        head = _FILTER_SPLIT_RE.split(rest)[0] if rest else ''
        names = scan1(_FILTER_NAMES_RE, head)
        only = scan1(_SYM_WORD_RE, cap(_ONLY_RE, rest) or '')
        except_ = scan1(_SYM_WORD_RE, cap(_EXCEPT_RE, rest) or '')
        for n in names:
            out.append([n, only, except_])
    return out


_WORD_RE = R(r'\b([a-z_]\w*[?!]?)')


def action_source(action, bodies, rules, max_hops=3):
    """アクションが実際に走らせるソース: 本文、効くフィルタ、そこから数段たどる private ヘルパー。"""
    names = [action]
    for name, only, except_ in rules:
        if only and action not in only:
            continue
        if action in except_:
            continue
        names.append(name)
    seen = set()
    frontier = [n for n in names if n in bodies]
    parts = []
    for _ in range(max_hops):
        nxt = []
        for n in frontier:
            if n in seen:
                continue
            seen.add(n)
            body = bodies[n]
            parts.append(body)
            for w in uniq(scan1(_WORD_RE, body)):
                if w in bodies and w not in seen:
                    nxt.append(w)
        frontier = nxt
        if not frontier:
            break
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------

_PACK_DEP_RE = R(r'''^\s*-\s*["']?([\w/.\-]+)["']?\s*$''')
_ENFORCE_DEPS_RE = R(r'enforce_dependencies:\s*true')
_ENFORCE_PRIV_RE = R(r'enforce_privacy:\s*true')


def scan_packs():
    files = sorted(os.path.join(d, 'package.yml') for d in glob_dirs(os.path.join(ROOT, 'packs'), '*')
                   if os.path.exists(os.path.join(d, 'package.yml')))
    out = []
    for f in files:
        src = read_file(f)
        out.append({
            'name': os.path.basename(os.path.dirname(f)),
            'path': rel(os.path.dirname(f)),
            'enforce_dependencies': bool(_ENFORCE_DEPS_RE.search(src)),
            'enforce_privacy': bool(_ENFORCE_PRIV_RE.search(src)),
            'dependencies': scan1(_PACK_DEP_RE, src),
        })
    return out


# ---------------------------------------------------------------------------
# Routes (static parse of the Rails routing DSL)
# ---------------------------------------------------------------------------

HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete', 'options', 'head']
REST_ACTIONS = {
    'index': ['GET', ''],
    'create': ['POST', ''],
    'new': ['GET', 'new'],
    'edit': ['GET', ':id/edit'],
    'show': ['GET', ':id'],
    'update': ['PATCH', ':id'],
    'destroy': ['DELETE', ':id'],
}
SINGULAR_REST_ACTIONS = {
    'create': ['POST', ''],
    'new': ['GET', 'new'],
    'edit': ['GET', 'edit'],
    'show': ['GET', ''],
    'update': ['PATCH', ''],
    'destroy': ['DELETE', ''],
}

# Routes Devise generates for each module, as HTTP method + path suffix + action.
# Only modules whose controller the app actually overrides are emitted, so we
# never invent routes for a module the model does not enable.
DEVISE_ROUTES = {
    'sessions': [
        ['GET', 'sign_in', 'new'],
        ['POST', 'sign_in', 'create'],
        ['DELETE', 'sign_out', 'destroy'],
    ],
    'registrations': [
        ['GET', 'sign_up', 'new'],
        ['POST', '', 'create'],
        ['GET', 'edit', 'edit'],
        ['PATCH', '', 'update'],
        ['DELETE', '', 'destroy'],
        ['GET', 'cancel', 'cancel'],
    ],
    'passwords': [
        ['GET', 'password/new', 'new'],
        ['POST', 'password', 'create'],
        ['GET', 'password/edit', 'edit'],
        ['PATCH', 'password', 'update'],
    ],
    'confirmations': [
        ['GET', 'confirmation/new', 'new'],
        ['POST', 'confirmation', 'create'],
        ['GET', 'confirmation', 'show'],
    ],
    'unlocks': [
        ['GET', 'unlock/new', 'new'],
        ['POST', 'unlock', 'create'],
        ['GET', 'unlock', 'show'],
    ],
}


def camelize(s):
    return ''.join((p[0].upper() + p[1:]) if p else p for p in ruby_split(s, '_'))


def camelize_path(s):
    return '::'.join(camelize(seg) for seg in ruby_split(s, '/'))


class RoutesParser(object):
    RE_END = R(r'^end\b')
    RE_DO = R(r'\bdo\b(\s*\|[^|]*\|)?\s*$')
    RE_KEYWORD_BLOCK = R(r'^(if|unless|case|begin|while|until|for)\b')
    RE_DEF_BLOCK = R(r'^(def|class|module)\s')
    RE_ROUTES_DRAW = R(r'\broutes\.draw\b')
    RE_DRAW = R(r'^draw\(?\s*:([\w/]+)\s*\)?')
    RE_DEVISE = R(r'^devise_for\s+:(\w+)(.*)$')
    RE_NAMESPACE = R(r'''^namespace\s+:?["']?([\w/]+)["']?(.*)$''')
    RE_MODULE_OPT = R(r'''module:\s*["':]([\w/]+)["']?''')
    RE_CONTROLLER_OPT = R(r'''controller:\s*["':]([\w/]+)["']?''')
    RE_SCOPE = R(r'^scope\s+(.*)$')
    RE_RESOURCES = R(r'^(?:resources|resource)\s+:([\w]+)(.*)$')
    RE_MEMBER = R(r'^(member|collection)\b')
    RE_ROOT = R(r'^root\s+(.*)$')
    RE_METHOD = R(r'^(' + '|'.join(HTTP_METHODS) + r'|match)\s+(.*)$')
    RE_PATH_OPT = R(r'''path:\s*["']([^"']+)["']''')
    RE_LEADING_STR = R(r'''^["']([^"']+)["']''')
    RE_DEVISE_PATH = R(r'''path:\s*["']([\w/]+)["']''')
    RE_SKIP_LIST = R(r'skip:\s*\[([^\]]*)\]')
    RE_SKIP_SYM = R(r'skip:\s*:(\w+)')
    RE_WORD = R(r'\w+')
    RE_DEVISE_OVERRIDE = R(r'''(?::)?(\w+)\s*(?::|=>)\s*["']([\w/]+)["']''')
    RE_VIA = R(r'via:\s*\[?:(\w+)')
    RE_ON = R(r'on:\s*:(\w+)')
    RE_LEADING_STR0 = R(r'''^["']([^"']*)["']''')
    RE_LEADING_SYM = R(r'^:(\w+)')
    RE_ACTION_OPT = R(r'action:\s*:(\w+)')
    RE_WORD_PATH = R(r'\A[\w/]+\Z')
    RE_TARGET_TO = R(r'''(?:to:|=>)\s*["'](/?[\w/]+#\w+)["']''')
    RE_TARGET_HASH = R(r'''^["'][^"']*["']\s*=>\s*["'](/?[\w/]+#\w+)["']''')
    RE_ONLY = R(r'only:')
    RE_EXCEPT = R(r'except:')
    RE_ONLY_LIST = R(r'only:\s*(?:%[iw]?\[([^\]]*)\]|\[([^\]]*)\]|:(\w+))')
    RE_EXCEPT_LIST = R(r'except:\s*(?:%[iw]?\[([^\]]*)\]|\[([^\]]*)\]|:(\w+))')
    RE_TRAILING_S = R(r's\Z')
    RE_LEADING_SLASHES = R(r'\A/+')

    def __init__(self, root):
        self.root = root
        self.routes = []
        self.files_read = []

    def parse_entrypoints(self):
        entry = os.path.join(self.root, 'config', 'routes.rb')
        if os.path.exists(entry):
            self.parse_file(entry)
        return self.routes

    @staticmethod
    def candidate_files(root):
        lst = [os.path.join(root, 'config', 'routes.rb')]
        lst += walk_files(os.path.join(root, 'config', 'routes'), ['.rb'])
        for p in glob_dirs(os.path.join(root, 'packs'), '*'):
            lst += walk_files(os.path.join(p, 'config', 'routes'), ['.rb'])
        return sorted(f for f in lst if os.path.exists(f))

    def parse_file(self, path, stack=None):
        if path in self.files_read:
            return
        self.files_read.append(path)
        src = read_file(path)
        frames = list(stack or [])
        block_stack = []
        line_no = 0

        # A route statement may be spread over several lines when its options do not
        # fit on one. Join those into one logical line first, otherwise the options are
        # lost and the frame is pushed without its matching `end` being recognised.
        logical = []
        pending = None
        pending_line = None
        for raw in ruby_lines(src):
            line_no += 1
            stripped = rstrip_all(self.strip_line_comment(raw))
            if not stripped:
                continue
            if pending is not None:
                pending = pending + ' ' + stripped
            else:
                pending = stripped
                pending_line = line_no
            if pending.endswith(',') or self.unbalanced(pending):
                continue
            logical.append([pending_line, pending])
            pending = None
        if pending is not None:
            logical.append([pending_line, pending])

        for line_no, line in logical:
            if self.RE_END.search(line):
                frame = block_stack.pop() if block_stack else None
                if frame == 'scope' and frames:
                    frames.pop()
                continue

            # Anything that needs a matching `end` must be pushed, not just `... do`.
            opens_block = bool(self.RE_DO.search(line)) or \
                bool(self.RE_KEYWORD_BLOCK.search(line)) or \
                bool(self.RE_DEF_BLOCK.search(line))

            # A drawn file that opens its own `...routes.draw do` restarts at the root
            # scope in Rails, so any namespace/scope active at the call site is dropped.
            if self.RE_ROUTES_DRAW.search(line):
                del frames[:]
                if opens_block:
                    block_stack.append('other')
                continue

            m = self.RE_DRAW.search(line)
            if m:
                target = self.resolve_draw(m.group(1))
                if target:
                    self.parse_file(target, frames)
                if opens_block:
                    block_stack.append('other')
                continue

            handled = False

            m = self.RE_DEVISE.search(line)
            if m:
                self.emit_devise(m.group(1), m.group(2), frames, path, line_no, line)
                if opens_block:
                    block_stack.append('other')
                continue

            m = self.RE_NAMESPACE.search(line)
            ms = self.RE_SCOPE.search(line) if not m else None
            mr = self.RE_RESOURCES.search(line) if not (m or ms) else None
            mc = self.RE_MEMBER.search(line) if not (m or ms or mr) else None
            mroot = self.RE_ROOT.search(line) if not (m or ms or mr or mc) else None
            mm = self.RE_METHOD.search(line) if not (m or ms or mr or mc or mroot) else None
            if m:
                # `namespace :x, module: :y` keeps the path x but puts controllers under y/.
                ns_module = cap(self.RE_MODULE_OPT, m.group(2)) or m.group(1)
                frames.append({'kind': 'namespace', 'path': m.group(1), 'module': ns_module})
                block_stack.append('scope')
                handled = True
            elif ms:
                frames.append(self.scope_frame(ms.group(1)))
                if opens_block:
                    block_stack.append('scope')
                handled = True
            elif mr:
                singular = line.startswith('resource ')
                self.emit_resource(mr.group(1), mr.group(2), frames, path, line_no, singular)
                if opens_block:
                    frames.append({
                        'kind': 'resource',
                        'path': mr.group(1),
                        'param': None if singular else ':%s_id' % self.RE_TRAILING_S.sub('', mr.group(1), count=1),
                        'controller': self.resource_controller(mr.group(1), mr.group(2), singular),
                        'module': None,
                    })
                    block_stack.append('scope')
                handled = True
            elif mc:
                # member/collection add no path segment of their own; they change how
                # the enclosing `resources` frame renders its id parameter.
                if opens_block:
                    frames.append({'kind': mc.group(1)})
                    block_stack.append('scope')
                handled = True
            elif mroot:
                target = self.extract_target(mroot.group(1))
                if target:
                    self.add_route('GET', self.join_path(frames, ''), target, frames, path, line_no, line)
                handled = True
            elif mm:
                self.emit_method(mm.group(1), mm.group(2), frames, path, line_no, line)
                handled = True

            if opens_block and not handled:
                block_stack.append('other')

    @staticmethod
    def unbalanced(line):
        """文にまだ閉じていない括弧が残っているか。"""
        depth = 0
        quote = None
        for ch in line:
            if quote:
                if ch == quote:
                    quote = None
            elif ch == "'" or ch == '"':
                quote = ch
            elif ch in '{(':
                depth += 1
            elif ch in '})':
                depth -= 1
        return depth > 0

    @staticmethod
    def strip_line_comment(line):
        """引用符を意識してコメントを落とす。素朴な /#.*$/ だと `to: 'home#index'` を壊す。"""
        quote = None
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch == "'" or ch == '"':
                quote = ch
            elif ch == '#':
                return line[:i]
        return line

    def resolve_draw(self, name):
        candidates = [os.path.join(self.root, 'config', 'routes', '%s.rb' % name)]
        for p in sorted(glob_dirs(os.path.join(self.root, 'packs'), '*')):
            candidates.append(os.path.join(p, 'config', 'routes', '%s.rb' % name))
        for f in candidates:
            if os.path.exists(f):
                return f
        return None

    def scope_frame(self, rest):
        path = cap(self.RE_PATH_OPT, rest) or cap(self.RE_LEADING_STR, rest)
        mod = cap(self.RE_MODULE_OPT, rest)
        ctrl = cap(self.RE_CONTROLLER_OPT, rest)
        return {'kind': 'scope', 'path': path, 'module': mod, 'controller': ctrl}

    def emit_devise(self, name, rest, frames, file, line_no, raw):
        prefix = cap(self.RE_DEVISE_PATH, rest) or name
        skipped = self.RE_WORD.findall(cap(self.RE_SKIP_LIST, rest) or cap(self.RE_SKIP_SYM, rest) or '')
        if 'all' in skipped:
            return

        overrides = {}
        for mod, ctrl in scan(self.RE_DEVISE_OVERRIDE, rest):
            if mod in DEVISE_ROUTES:
                overrides[mod] = ctrl

        for mod, controller in overrides.items():
            if mod in skipped:
                continue
            if not self.controller_exists(controller):
                continue
            for http_method, suffix, action in DEVISE_ROUTES[mod]:
                seg = '/'.join(x for x in [prefix, suffix] if x)
                # Devise takes `controllers:` as an absolute controller path, so the
                # surrounding namespace must not be prepended to it again.
                self.add_route(http_method, self.join_path(frames, seg), '%s#%s' % (controller, action),
                               [], file, line_no, raw, 'devise')

    def controller_exists(self, controller):
        relp = 'app/controllers/%s_controller.rb' % controller
        if os.path.exists(os.path.join(self.root, relp)):
            return True
        return any(os.path.exists(os.path.join(p, relp)) for p in glob_dirs(os.path.join(self.root, 'packs'), '*'))

    def emit_method(self, http_method, rest, frames, file, line_no, raw):
        if http_method == 'match':
            http_method = (cap(self.RE_VIA, rest) or 'match').upper()
        else:
            http_method = http_method.upper()
        on = cap(self.RE_ON, rest)
        path = cap(self.RE_LEADING_STR0, rest) or cap(self.RE_LEADING_SYM, rest) or ''
        target = self.extract_target(rest)

        if target is None:
            # `get :status` inside a resources block: the action name is the path and
            # the controller comes from the nearest frame that names one.
            res = None
            for f in reversed(frames):
                if f.get('controller'):
                    res = f
                    break
            literal_path = cap(self.RE_LEADING_STR0, rest)
            action = cap(self.RE_LEADING_SYM, rest) or cap(self.RE_ACTION_OPT, rest)
            if not action and literal_path is not None:
                segs = [x for x in ruby_split(literal_path, '/') if not x.startswith(':')]
                action = segs[-1] if segs else None
            if res and action:
                target = '%s#%s' % (res['controller'], action)
            else:
                # `get 'labels/show_qr_codes'` with no `to:`: Rails derives
                # controller#action from the path itself. Only safe when every segment
                # is a plain word.
                if literal_path is None:
                    literal_path = cap(self.RE_LEADING_SYM, rest)
                if not (literal_path and self.RE_WORD_PATH.search(literal_path)):
                    return
                segs = ruby_split(literal_path, '/')
                mods = [f.get('module') for f in frames if f.get('module') is not None]
                if len(segs) > 1:
                    target = '%s#%s' % ('/'.join(segs[:-1]), segs[-1])
                elif mods:
                    # `namespace :inventory_imports do match 'upload_file' end` maps to
                    # InventoryImportsController#upload_file: the namespace itself is the
                    # controller, so it must not also be applied as a module prefix.
                    self.add_route(http_method, self.join_path(frames, path, on),
                                   '%s#%s' % ('/'.join(mods), segs[0]), [], file, line_no, raw)
                    return
                else:
                    return

        self.add_route(http_method, self.join_path(frames, path, on), target, frames, file, line_no, raw)

    def extract_target(self, rest):
        return cap(self.RE_TARGET_TO, rest) or cap(self.RE_TARGET_HASH, rest)

    @staticmethod
    def pluralize(word):
        """Inflector#pluralize のうち、ルート名に要る分だけ。"""
        if re.search(r'(?:ss|us|is)\Z', word):
            return word + 'es'
        if re.search(r's\Z', word):
            return word
        if re.search(r'(?:x|z|ch|sh)\Z', word):
            return word + 'es'
        if re.search(r'[^aeiou]y\Z', word):
            return word[:-1] + 'ies'
        return word + 's'

    def resource_controller(self, name, rest, singular):
        default_controller = self.pluralize(name) if singular else name
        controller = cap(self.RE_CONTROLLER_OPT, rest) or default_controller
        res_module = cap(self.RE_MODULE_OPT, rest)
        return '%s/%s' % (res_module, controller) if res_module else controller

    def emit_resource(self, name, rest, frames, file, line_no, singular):
        controller = self.resource_controller(name, rest, singular)
        # `only: []` declares zero REST routes, which is not the same as omitting it.
        has_only = bool(self.RE_ONLY.search(rest))
        has_except = bool(self.RE_EXCEPT.search(rest))
        only = ','.join(flatten_compact(scan(self.RE_ONLY_LIST, rest)))
        except_ = ','.join(flatten_compact(scan(self.RE_EXCEPT_LIST, rest)))
        table = SINGULAR_REST_ACTIONS if singular else REST_ACTIONS
        actions = list(table.keys())
        if has_only:
            actions = [a for a in actions if a in only]
        if has_except:
            actions = [a for a in actions if a not in except_]

        for action in actions:
            http_method, suffix = table[action]
            seg = '/'.join(s for s in [name, suffix] if s)
            self.add_route(http_method, self.join_path(frames, seg), '%s#%s' % (controller, action),
                           frames, file, line_no, 'resources :%s' % name)

    def join_path(self, frames, tail, on=None):
        """`on` は1行ルートの `on: :member` / `on: :collection`。"""
        parts = []
        for i, f in enumerate(frames):
            kind = f.get('kind')
            if kind == 'resource':
                parts.append(f.get('path'))
                nxt = frames[i + 1] if i + 1 < len(frames) else None
                if (nxt and nxt.get('kind') == 'member') or (nxt is None and on == 'member'):
                    parts.append(':id')
                elif (nxt and nxt.get('kind') == 'collection') or (nxt is None and on == 'collection'):
                    pass  # collection routes hang off the bare resource path
                elif f.get('param'):
                    parts.append(f.get('param'))
            elif kind in ('member', 'collection'):
                pass
            elif f.get('path'):
                parts.append(f.get('path'))
        if tail:
            parts.append(tail)
        joined = '/'.join(p for p in parts if p)
        return '/' + self.RE_LEADING_SLASHES.sub('', joined, count=1)

    def add_route(self, http_method, path, target, frames, file, line_no, raw, source='static-parse'):
        # A leading slash means "absolute controller path" in Rails: the enclosing
        # namespace is NOT applied.
        if target.startswith('/'):
            target = target[1:]
            frames = []
        mods = [f.get('module') for f in frames if f.get('module') is not None]
        parts = ruby_split(target, '#')
        ctrl = parts[0] if parts else ''
        action = parts[1] if len(parts) > 1 else None
        full_ctrl = '/'.join(mods + [ctrl])
        self.routes.append({
            'method': http_method,
            'path': path,
            'controller': full_ctrl,
            'action': action,
            'controller_class': '::'.join(camelize(s) for s in ruby_split(full_ctrl, '/')) + 'Controller',
            'source_file': rel(file),
            'source_line': line_no,
            'source': source,
            'raw': raw[:200],
        })


_ROUTES_TABLE_RE = R(r'^\s*(\S+)?\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)(?:\|\S+)?\s+(\S+)\s+([\w/]+)#(\w+)')


def parse_routes_table(path):
    """`rails routes` の出力 → 正式なルート一覧。"""
    routes = []
    for line in ruby_lines(read_file(path)):
        m = _ROUTES_TABLE_RE.search(line)
        if not m:
            continue
        routes.append({
            'method': m.group(2),
            'path': m.group(3),
            'controller': m.group(4),
            'action': m.group(5),
            'controller_class': camelize_path(m.group(4)) + 'Controller',
            'name': m.group(1),
            'source_file': rel(os.path.abspath(path)),
            'source_line': None,
            'source': 'rails-routes',
        })
    return routes


# ---------------------------------------------------------------------------
# Per-file entry builders
# ---------------------------------------------------------------------------

_BEFORE_ACTIONS_RE = R(r'^\s*(?:before_action|prepend_before_action|around_action|after_action)\s+:(\w+)')
_CTRL_RENDERS_RE = R(r'''\brender\s+(?:template:\s*)?["']([\w/.\-]+)["']''')
_ASSOC_RE = R(r'^\s*(belongs_to|has_many|has_one|has_and_belongs_to_many)\s+:(\w+)(.*)$')
_CLASS_NAME_RE = R(r'''class_name:\s*["']([\w:]+)["']''')
_TABLE_NAME_RE = R(r'''self\.table_name\s*=\s*["'](\w+)["']''')
_SCOPE_RE = R(r'^\s*scope\s+:(\w+)')
_ENUM_RE = R(r'^\s*enum\s+:?(\w+)')
_VIEW_PATH_RE = R(r'views/(.+?)/([^/]+?)\.[\w.]+\Z')
_VIEW_RENDERS_RE = R(r'''\brender\s*\(?\s*(?:partial:\s*)?["']([\w/.\-]+)["']''')
_PAGE_KEY_RE = R(r'javascripts?/pages/(.+)\.vue\Z')


def build_controller(file):
    raw = read_file(file)
    src = strip_comments(raw)
    klass, sup, line, kind = primary_class(src)
    return {
        'file': rel(file),
        'pack': pack_of(file),
        'class': klass,
        'superclass': sup,
        'class_line': line,
        'kind': kind,
        'actions': public_methods_of(src),
        'before_actions': uniq(scan1(_BEFORE_ACTIONS_RE, src)),
        'includes': includes_of(src),
        'renders': uniq(scan1(_CTRL_RENDERS_RE, src)),
        'loc': len(ruby_lines(raw)),
    }


def build_model(file):
    raw = read_file(file)
    src = strip_comments(raw)
    klass, sup, line, kind = primary_class(src)
    assocs = [{'kind': k, 'name': n, 'class_name': cap(_CLASS_NAME_RE, rest)}
              for k, n, rest in scan(_ASSOC_RE, src)]
    return {
        'file': rel(file),
        'pack': pack_of(file),
        'class': klass,
        'superclass': sup,
        'class_line': line,
        'kind': kind,
        'table_name': cap(_TABLE_NAME_RE, src),
        'associations': assocs,
        'scopes': uniq(scan1(_SCOPE_RE, src)),
        'enums': uniq(scan1(_ENUM_RE, src)),
        'includes': includes_of(src),
        'public_methods': public_methods_of(src),
        'loc': len(ruby_lines(raw)),
    }


def build_plain(file):
    raw = read_file(file)
    src = strip_comments(raw)
    klass, sup, line, kind = primary_class(src)
    return {
        'file': rel(file),
        'pack': pack_of(file),
        'class': klass,
        'superclass': sup,
        'class_line': line,
        'kind': kind,
        'public_methods': public_methods_of(src),
        'includes': includes_of(src),
        'loc': len(ruby_lines(raw)),
    }


def build_view(file):
    raw = read_file(file)
    r = rel(file)
    m = _VIEW_PATH_RE.search(r)
    base = m.group(2) if m else os.path.basename(r)
    return {
        'file': r,
        'pack': pack_of(file),
        'controller_hint': m.group(1) if m else None,
        'action_hint': None if base.startswith('_') else base,
        'partial': base.startswith('_'),
        'renders': uniq(scan1(_VIEW_RENDERS_RE, raw)),
        'loc': len(ruby_lines(raw)),
    }


def build_frontend(file):
    r = rel(file)
    # app.ts loads `pages/<body data-component>.vue`, keyed "<controller path>/<action>".
    # Recording the key here lets step 2 join a screen to its Vue page by convention.
    m = _PAGE_KEY_RE.search(r)
    return {
        'file': r,
        'pack': pack_of(file),
        'kind': os.path.splitext(file)[1].replace('.', '', 1),
        'page_key': m.group(1) if m else None,
        'loc': len(ruby_lines(read_file(file))),
    }


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------

BUCKET_SPECS = [
    ['controllers', 'controllers', 'controller'],
    ['models', 'models', 'model'],
    ['services', 'services', 'plain'],
    ['jobs', 'jobs', 'plain'],
    ['mailers', 'mailers', 'plain'],
    ['lib', 'lib', 'plain'],
]


def build_for(kind, file):
    if kind == 'controller':
        return build_controller(file)
    if kind == 'model':
        return build_model(file)
    return build_plain(file)


_BUCKET_PATHS = [
    (R(r'(?:\A|packs/[^/]+/)app/controllers/'), 'controllers', 'controller'),
    (R(r'(?:\A|packs/[^/]+/)app/models/'), 'models', 'model'),
    (R(r'(?:\A|packs/[^/]+/)app/services/'), 'services', 'plain'),
    (R(r'(?:\A|packs/[^/]+/)app/jobs/'), 'jobs', 'plain'),
    (R(r'(?:\A|packs/[^/]+/)app/mailers/'), 'mailers', 'plain'),
    (R(r'(?:\A|packs/[^/]+/)app/lib/'), 'lib', 'plain'),
    (R(r'(?:\A|packs/[^/]+/)app/views/'), 'views', 'view'),
    (R(r'app/frontend/javascripts?/pages/.*\.(vue|ts|js)\Z'), 'frontend_pages', 'frontend'),
]


def bucket_for_path(relpath):
    for rx, bucket, kind in _BUCKET_PATHS:
        if rx.search(relpath):
            return [bucket, kind]
    return None


def js_dirs(app_root):
    """`frontend/javascript{,s}` のうち存在するもの。Ruby の brace 展開と同じ順。"""
    return [d for d in (os.path.join(app_root, 'frontend', 'javascript'),
                        os.path.join(app_root, 'frontend', 'javascripts')) if os.path.isdir(d)]


def frontend_files():
    out = []
    for r in APP_ROOTS:
        for d in js_dirs(r):
            out.extend(walk_files(os.path.join(d, 'pages'), ['.vue', '.ts', '.js']))
    return sorted(out)


# ---------------------------------------------------------------------------
# Frontend -> API: which page calls which endpoint
# ---------------------------------------------------------------------------
# A Vue page does not name a controller. It imports a composable, which calls
# `inventoriesV2Api.createInventory()`, which posts to `/api/inventories_v2.json`.
# Recording the three hops as facts lets step 2 connect a button on a page to the
# Rails action that handles it. Always re-read in full: it is regex over ~1k files.

FRONTEND_SKIP = R(r'/(tests?|__tests__|mocks|typescript-client|locales)/|\.(test|spec|stories)\.')
API_CALL_RE = R(r'''\bapiClient\s*\.\s*(get|post|put|patch|delete)\s*(?:<(?:[^<>]|<[^<>]*>)*>)?\s*\(\s*(['"`])(/[^'"`]*)\2''',
                dotall=True)
_API_OBJ_RE = R(r'export\s+const\s+(\w+Api)\s*=')
_API_KEY_RE = R(r'^\s{2}(\w+)\s*:\s*(?:async\s*)?(?:\(|\w+\s*=>|<)')
_IMPORT_RE = R(r'''\bfrom\s+['"]([^'"]+)['"]|\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)''')
_API_USE_RE = R(r'\b(\w+Api)\s*\.\s*(\w+)\s*\(')
_JS_FN_RE = R(r'(?:\b(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::\s*[^=]+?)?=>\s*\{|\bfunction\s+(\w+)\s*\([^)]*\)[^{]*\{)')
_CALL_RE = R(r'\b([a-zA-Z_]\w*)\s*\(')
_WORDS_RE = R(r'\b[a-zA-Z_]\w{2,}\b')
_TITLE_KEY_RE = R(r"<z-page-header\b[^>]*?:title=\"\$t\(\s*'([^']+)'", dotall=True)


def frontend_js_root():
    for r in APP_ROOTS:
        dirs = js_dirs(r)
        if dirs:
            return dirs[0]
    return None


def resolve_import(spec, from_abs, js_root):
    if spec.startswith('@/'):
        base = os.path.join(js_root, spec[2:])
    elif spec.startswith('.'):
        base = os.path.normpath(os.path.join(os.path.dirname(from_abs), spec))
    else:
        return None
    for c in [base, base + '.ts', base + '.vue', base + '.js',
              os.path.join(base, 'index.ts'), os.path.join(base, 'index.js')]:
        if os.path.isfile(c):
            return c
    return None


def js_functions(src):
    """TS/Vue の名前付き関数と本文。波括弧の対応で本文を切り出す。"""
    out = {}
    for m in _JS_FN_RE.finditer(src):
        name = m.group(1) or m.group(2)
        i = m.end(0)
        depth = 1
        n = len(src)
        while i < n and depth > 0:
            c = src[i]
            if c == '{':
                depth += 1
            if c == '}':
                depth -= 1
            i += 1
        if name not in out:
            out[name] = src[m.end(0):i]
    return out


def scan_frontend_calls(data):
    js_root = frontend_js_root()
    if not js_root:
        return data

    api = []
    endpoint_dir = os.path.join(js_root, 'api', 'endpoints')
    endpoint_files = sorted(os.path.join(endpoint_dir, n) for n in
                            (os.listdir(endpoint_dir) if os.path.isdir(endpoint_dir) else [])
                            if not n.startswith('.') and (n.endswith('.ts') or n.endswith('.js')))
    for f in endpoint_files:
        src = read_file(f)
        obj = cap(_API_OBJ_RE, src)
        if not obj:
            continue
        keys = list(_API_KEY_RE.finditer(src))
        for k, m in enumerate(keys):
            stop = keys[k + 1].start(0) if k + 1 < len(keys) else len(src)
            body = src[m.start(0):stop]
            call = API_CALL_RE.search(body)
            if not call:
                continue
            api.append({'object': obj, 'fn': m.group(1), 'method': call.group(1).upper(),
                        'path': call.group(3), 'file': rel(f)})

    # The title a user actually reads on the page, resolved from the page header's
    # i18n key, so the map can be searched by what is on screen.
    ja = {}
    ja_file = os.path.join(js_root, 'locales', 'translation.ja.json')
    if os.path.isfile(ja_file):
        try:
            ja = json.loads(read_file(ja_file))
        except ValueError:
            ja = {}

    def resolve_ja(key):
        o = ja
        for k in ruby_split(key, '.'):
            o = o.get(k) if isinstance(o, dict) else None
        return o if isinstance(o, str) else None

    modules = []
    for f in sorted(walk_files(js_root, ['.vue', '.ts', '.js'])):
        if FRONTEND_SKIP.search(f):
            continue
        src = read_file(f)
        resolved = [resolve_import(spec, f, js_root) for spec in flatten_compact(scan(_IMPORT_RE, src))]
        imports = [rel(x) for x in uniq([x for x in resolved if x is not None])]
        calls = uniq(scan(_API_USE_RE, src))
        urls = uniq([[m.group(1).upper(), m.group(3)] for m in API_CALL_RE.finditer(src)])
        if not imports and not calls and not urls:
            continue
        # Per function: which API calls and which sibling functions it reaches.
        fns = {}
        bodies = js_functions(src)
        for name, body in bodies.items():
            # A composable's outer function contains every inner function. Count only
            # its own statements, or it would claim all of its children's API calls.
            for other, ob in bodies.items():
                if other != name and len(ob) < len(body) and ob in body:
                    body = body.replace(ob, '', 1)
            fns[name] = {
                'api_calls': uniq(scan(_API_USE_RE, body)),
                'calls': [w for w in uniq(scan1(_CALL_RE, body)) if w in bodies and w != name],
            }
        words = uniq(_WORDS_RE.findall(src))
        title_key = cap(_TITLE_KEY_RE, src)
        modules.append({
            'file': rel(f),
            'imports': imports,
            'api_calls': calls,
            'urls': urls,
            'page_title_key': title_key,
            'page_title_ja': resolve_ja(title_key) if title_key else None,
            'functions': dict((k, v) for k, v in fns.items() if not (not v['api_calls'] and not v['calls'])),
            '_words': words,
        })

    # Which functions of each imported file this file actually names.
    by_file = dict((m['file'], m) for m in modules)
    for m in modules:
        words = set(m.pop('_words'))
        uses = {}
        for imp in m['imports']:
            t = by_file.get(imp)
            if not (t and t.get('functions')):
                continue
            used = [k for k in t['functions'] if k in words]
            if used:
                uses[imp] = used
        if uses:
            m['import_uses'] = uses
    for m in modules:
        if 'functions' in m and not m['functions']:
            del m['functions']

    data['frontend_api'] = api
    data['frontend_modules'] = modules
    return data


# ---------------------------------------------------------------------------
# Cross-file references (edges), computed after a class index exists
# ---------------------------------------------------------------------------

# Method names Ruby, ActiveRecord or plain objects use constantly. A model that
# happens to share one (Index, Status, Setting...) must not be matched by `.index`.
ASSOC_STOPWORDS = set('''
  index name names type types status statuses count first last find where order orders group groups
  update create delete destroy save value values data params errors error file files path paths size
  time date dates each list lists item items key keys text body title code codes state states sort
  result results page pages limit offset join joins select includes present blank empty
  settings setting option options config configs content contents detail details parent children
  history histories log logs message messages request response token tokens account accounts
'''.split())

_CAMEL_SPLIT_RE = R(r'([a-z\d])([A-Z])')
_TRAILING_Y_RE = R(r'y\Z')
_DQ_STR_RE = R(r'"(?:\\.|[^"\\])*"')
_SQ_STR_RE = R(r"'(?:\\.|[^'\\])*'")
_ASSOC_CALL_RE = R(r'\.([a-z][a-z0-9_]{3,})\b')
REF_BUCKETS = ['controllers', 'models', 'services', 'jobs', 'mailers', 'lib']


def attach_references(data):
    index = {}
    shorts = defaultdict(list)
    for bucket in REF_BUCKETS:
        for e in data.get(bucket) or []:
            if not e.get('class'):
                continue
            index[e['class']] = [bucket, e['file']]
            short = e['class'].split('::')[-1]
            if short != e['class']:
                shorts[short].append([bucket, e['file']])
    # A bare `Client` or `Service` exists under dozens of namespaces, so a short name
    # only resolves when exactly one class carries it.
    for short, hits in shorts.items():
        if len(hits) == 1 and short not in index:
            index[short] = hits[0]

    # `current_company.categories.find(id)` names no class, yet it is how a Rails
    # controller most often reaches a model. Map association-style method names to
    # the model file -- only when unambiguous and only for app/ models.
    assoc_models = {}
    seen_assoc = defaultdict(int)
    for m in data.get('models') or []:
        if not (m.get('class') and (m['file'].startswith('app/models/') or m['file'].startswith('packs/'))):
            continue
        snake = _CAMEL_SPLIT_RE.sub(r'\1_\2', m['class'].split('::')[-1]).lower()
        cands = [snake, m.get('table_name'), snake + 's', _TRAILING_Y_RE.sub('ies', snake, count=1)]
        for w in uniq([c for c in cands if c is not None]):
            seen_assoc[w] += 1
            assoc_models[w] = m['file']
    assoc_models = dict((w, f) for w, f in assoc_models.items()
                        if not (seen_assoc[w] > 1 or len(w) < 4 or w in ASSOC_STOPWORDS))

    def via_assoc(body):
        return [assoc_models[w] for w in uniq(scan1(_ASSOC_CALL_RE, body)) if w in assoc_models]

    def minus(items, drop):
        return [x for x in items if x != drop]

    for bucket in REF_BUCKETS:
        for e in data.get(bucket) or []:
            path = os.path.join(ROOT, e['file'])
            if not os.path.exists(path):
                continue
            src = strip_comments(read_file(path))
            # Words inside string literals ("Service unavailable") are not references.
            src = _SQ_STR_RE.sub("''", _DQ_STR_RE.sub('""', src))
            own = e.get('class')
            refs = [c for c in constants_in(src) if c in index and c != own]
            e['references'] = minus(uniq([index[c][1] for c in refs]), e['file'])
            e['references_classes'] = uniq(refs)

            # Controllers: which classes each action reaches, so a screen can be told
            # apart from its siblings instead of inheriting the whole file's references.
            if bucket != 'controllers':
                continue

            bodies = method_bodies(src)
            rules = filter_rules(src)
            filter_refs = []
            for name, only, except_ in rules:
                if name not in bodies:
                    continue
                fsrc = action_source(name, bodies, [])
                frefs = [c for c in constants_in(fsrc) if c in index and c != own]
                filter_refs.append({
                    'name': name, 'only': only, 'except': except_,
                    'references': minus(uniq([index[c][1] for c in frefs] + via_assoc(fsrc)), e['file']),
                })
            e['filter_references'] = filter_refs
            e['action_references'] = {}
            for action in e.get('actions') or []:
                if action not in bodies:
                    continue
                body = action_source(action, bodies, rules)
                arefs = [c for c in constants_in(body) if c in index and c != own]
                e['action_references'][action] = minus(
                    uniq([index[c][1] for c in arefs] + via_assoc(body)), e['file'])
    return data


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

def read_routes(data, routes_table):
    if routes_table:
        data['routes'] = parse_routes_table(routes_table)
        data['routes_files'] = [rel(os.path.abspath(routes_table))]
    else:
        parser = RoutesParser(ROOT)
        data['routes'] = parser.parse_entrypoints()
        data['routes_files'] = [rel(f) for f in parser.files_read]


def full_scan(routes_table):
    data = {}
    data['packs'] = scan_packs()
    read_routes(data, routes_table)
    for key, subdir, kind in BUCKET_SPECS:
        data[key] = [build_for(kind, f) for f in ruby_files(subdir)]
    data['views'] = [build_view(f) for f in view_files()]
    data['frontend_pages'] = [build_frontend(f) for f in frontend_files()]
    attach_references(data)
    scan_frontend_calls(data)
    return data


# ---------------------------------------------------------------------------
# Incremental scan
# ---------------------------------------------------------------------------

_ROUTES_PATH_RE = R(r'\Aconfig/routes')
_PACK_ROUTES_PATH_RE = R(r'\Apacks/[^/]+/config/routes')
_PACKAGE_YML_RE = R(r'\Apacks/[^/]+/package\.yml\Z')


def incremental_scan(previous, since, routes_table):
    diff = git(ROOT, 'diff', '--name-status', '%s..HEAD' % since)
    if diff is None:
        sys.exit('cannot diff from %s (commit missing? rebased?). Re-run a full scan.' % since)

    changed = []
    for line in ruby_lines(diff):
        parts = ruby_split(rstrip_all(line), '\t')
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith('R'):
            changed.append(['D', parts[1]])
            changed.append(['M', parts[2]])
        else:
            changed.append([status[0], parts[1]])

    data = previous
    touched = []
    routes_dirty = False
    packs_dirty = False

    for status, relpath in changed:
        if _ROUTES_PATH_RE.search(relpath) or _PACK_ROUTES_PATH_RE.search(relpath):
            routes_dirty = True
        if _PACKAGE_YML_RE.search(relpath):
            packs_dirty = True

        spec = bucket_for_path(relpath)
        if not spec:
            continue
        bucket, kind = spec
        data[bucket] = [e for e in (data.get(bucket) or []) if e['file'] != relpath]
        if status == 'D':
            continue

        abs_path = os.path.join(ROOT, relpath)
        if not os.path.exists(abs_path):
            continue
        if kind == 'view':
            entry = build_view(abs_path)
        elif kind == 'frontend':
            entry = build_frontend(abs_path)
        else:
            entry = build_for(kind, abs_path)
        data[bucket].append(entry)
        touched.append(relpath)

    if packs_dirty:
        data['packs'] = scan_packs()
    if routes_dirty:
        read_routes(data, routes_table)

    attach_references(data)
    scan_frontend_calls(data)
    for b in ['controllers', 'models', 'services', 'jobs', 'mailers', 'lib', 'views', 'frontend_pages']:
        if data.get(b):
            data[b].sort(key=lambda e: e['file'])
    return data, touched, routes_dirty


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main():
    head = (git(ROOT, 'rev-parse', 'HEAD') or '').strip() or None

    previous = None
    if os.path.exists(OUT):
        try:
            previous = json.loads(read_file(OUT))
        except ValueError:
            previous = None
    mode = 'full'
    touched = None

    if options.since:
        if previous is None:
            sys.exit('--since needs an existing scan at %s; run a full scan first.' % rel(OUT))
        data, touched, _routes_dirty = incremental_scan(previous, options.since, options.routes_table)
        mode = 'incremental'
    else:
        data = full_scan(options.routes_table)

    meta = {
        'schema_version': SCHEMA_VERSION,
        'generated_at': now_iso(),
        'commit': head,
        'previous_commit': options.since,
        'mode': mode,
        'root': rel(ROOT) or '.',
        'routes_source': 'rails-routes' if options.routes_table else 'static-parse',
        'files_reparsed': len(touched) if touched is not None else None,
    }

    out = {'meta': meta}
    for k, v in data.items():
        if k != 'meta':
            out[k] = v

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    if not options.quiet:
        counts = ' '.join('%s=%d' % (k, len(out.get(k) or [])) for k in
                          ['packs', 'routes', 'controllers', 'models', 'services', 'jobs', 'mailers',
                           'lib', 'views', 'frontend_pages'])
        err = sys.stderr
        err.write('[code-map] %s scan -> %s\n' % (mode, rel(OUT)))
        err.write('[code-map] commit=%s %s\n' % (head, counts))
        if touched is not None:
            err.write('[code-map] reparsed %d file(s)\n' % len(touched))
        err.write('[code-map] routes source: %s\n' % meta['routes_source'])


if __name__ == '__main__':
    main()
