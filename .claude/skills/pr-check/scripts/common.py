# common.py - pr-check の各チェックが共有する読み込み・検索・出力の部品
#
# 標準ライブラリのみ。Python 3.8 以降で動く記法に限る。

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Ruby の定義解析は step 1 と同じ実装を使う。別実装にすると step 1 の def / new_def と食い違う。
_spec = importlib.util.spec_from_file_location(
    'scan_pr_diff', os.path.join(HERE, '..', '..', 'pr-diff', 'scripts', 'scan_pr_diff.py'))
prdiff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prdiff)

CODE_EXT = ('.rb', '.rake', '.erb', '.jbuilder', '.vue', '.ts', '.tsx', '.js', '.jsx')
SKIP_DIRS = re.compile(r'\A(vendor|node_modules|public|tmp|log|coverage|\.ai|\.claude|\.git)/')
SEVERITY_RANK = {'error': 0, 'warning': 1, 'info': 2}
RISK_RANK = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}

# 名前が汎用すぎて、名前だけの grep では呼び出し元が特定できないメソッド
GENERIC_METHODS = {
    'initialize', 'call', 'call!', 'perform', 'to_s', 'to_h', 'to_a', 'to_json', 'as_json',
    'index', 'show', 'new', 'create', 'edit', 'update', 'destroy', 'id', 'name', 'type',
    'value', 'data', 'params', 'run', 'execute', 'process', 'build', 'save', 'valid?',
    'each', 'map', 'size', 'count', 'first', 'last', 'find', 'all', 'where', 'status',
    'result', 'errors', 'message', 'key', 'hash', 'get', 'set', 'load', 'list', 'items',
}


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def run(*cmd):
    try:
        proc = subprocess.run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return '', False
    return proc.stdout.decode('utf-8', errors='replace'), proc.returncode == 0


def is_spec(path):
    return bool(re.search(r'(^|/)spec/|\.spec\.[jt]sx?$|\.test\.[jt]sx?$', path))


def is_comment(line):
    return line.lstrip().startswith(('#', '//', '<!--', '/*', '*'))


def range_lines(ranges):
    lines = set()
    for r in ranges or []:
        lines.update(range(r['from'], r['to'] + 1))
    return lines


class Change:
    """PR の1ファイル分。base / head の本文と、変わった行番号を持つ。"""

    def __init__(self, entry, base_ref):
        self.entry = entry
        self.path = entry['path']
        self.status = entry['status']
        self.lang = entry.get('lang')
        self.added = range_lines(entry.get('added'))
        self.symbols = entry.get('symbols') or {}
        self.base = self.head = None
        if not entry.get('binary'):
            if self.status != 'added':
                self.base = prdiff.file_content(entry.get('old_path') or self.path, base_ref)
            if self.status != 'deleted':
                self.head = prdiff.file_content(self.path, None)
        self.base_lines = self.base.split('\n') if self.base is not None else []
        self.head_lines = self.head.split('\n') if self.head is not None else []

    def added_text(self):
        return [(no, self.head_lines[no - 1]) for no in sorted(self.added) if no <= len(self.head_lines)]


class Corpus:
    """リポジトリのコードファイルを一度だけ読み、名前検索に使う（作業ツリーの内容）。"""

    def __init__(self):
        out, ok = run('git', 'ls-files', '--cached', '--others', '--exclude-standard')
        if not ok:
            die('git ls-files に失敗しました。')
        self.texts = {}
        for path in out.split('\n'):
            if path.endswith(CODE_EXT) and not SKIP_DIRS.match(path) and os.path.isfile(path):
                with open(path, 'rb') as fh:
                    self.texts[path] = fh.read().decode('utf-8', errors='replace')
        self._lines = {}

    def lines(self, path):
        if path not in self._lines:
            self._lines[path] = self.texts.get(path, '').split('\n')
        return self._lines[path]

    def grep(self, rx, needle, paths=None):
        """(path, 行番号, 行)。needle は正規表現より先に当てる部分文字列フィルタ。"""
        hits = []
        for path in (paths if paths is not None else self.texts):
            text = self.texts.get(path)
            if text is None or needle not in text:
                continue
            for no, line in enumerate(self.lines(path), 1):
                if needle in line and rx.search(line):
                    hits.append((path, no, line.strip()[:200]))
        return hits


def ruby_defs(text):
    """{qualified: {'name', 'line', 'last_line', 'params'}}。params は引数部分を1行にしたもの。"""
    if text is None:
        return {}
    syms = prdiff.resolve_bounds(prdiff.ruby_symbols(text), text.count('\n') + 1)
    lines = text.split('\n')
    defs = {}
    for s in syms:
        if s['kind'] != 'def':
            continue
        params = ''
        m = re.search(r'def\s+(?:self\.)?[\w?!=\[\]<>+\-*/%]+\s*\((.*)', lines[s['line'] - 1])
        if m:
            buf, i = m.group(1), s['line']
            while buf.count('(') >= buf.count(')') and i < len(lines) and i < s['line'] + 20:
                buf += ' ' + lines[i].strip()
                i += 1
            depth = 1
            for idx, ch in enumerate(buf):
                depth += (ch == '(') - (ch == ')')
                if depth == 0:
                    buf = buf[:idx]
                    break
            params = buf
        defs[prdiff.qualify(s, syms)] = {
            'name': s['name'][5:] if s['name'].startswith('self.') else s['name'],
            'line': s['line'], 'last_line': s['last_line'], 'params': params,
        }
    return defs


def split_top(text, sep=','):
    """括弧・文字列の外側にある区切りで分割する。"""
    parts, depth, cur, quote = [], 0, '', None
    for ch in text:
        if quote:
            cur += ch
            quote = None if ch == quote else quote
            continue
        if ch in '\'"`':
            quote = ch
        elif ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def flatten_json(text):
    """ロケール JSON を {'a.b.c': '値'} にする。読めなければ {}。"""
    try:
        data = json.loads(text or '')
    except ValueError:
        return {}
    out = {}

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, prefix + [k])
        else:
            out['.'.join(prefix)] = str(node)
    walk(data, [])
    return out


class Findings:
    def __init__(self, risk_by_file):
        self.items = []
        self.risk_by_file = risk_by_file

    def add(self, group, check, severity, path, line, vi, en, rule=None, **evidence):
        digest = hashlib.sha1('{}|{}|{}|{}'.format(check, path, line, en).encode('utf-8')).hexdigest()[:8]
        item = {'id': '{}-{}'.format(check, digest), 'group': group, 'check': check, 'severity': severity,
                'file': path, 'line': line, 'message': {'vi': vi, 'en': en}}
        if self.risk_by_file.get(path):
            item['file_risk'] = self.risk_by_file[path]
        if rule:
            item['rule'] = rule
        if evidence:
            item['evidence'] = evidence
        self.items.append(item)

    def sorted(self):
        unique = {f['id']: f for f in self.items}.values()
        return sorted(unique, key=lambda f: (SEVERITY_RANK[f['severity']], RISK_RANK.get(f.get('file_risk'), 9),
                                             f['group'], f['file'], f['line'] or 0))
