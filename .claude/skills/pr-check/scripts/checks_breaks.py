# checks_breaks.py - 「他の箇所を壊していないか」
#
# base と head を比べて「消えたもの・変わったもの」を見つけ、その名前をまだ使っている箇所を探す。
# PR 自身が追加した行は「追従済み」として呼び出し元から除く。

import os
import re

from common import GENERIC_METHODS, flatten_json, is_comment, is_spec, prdiff, ruby_defs, run, split_top

GROUP = 'breaks'
RE_TS_EXPORT = re.compile(r'^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\s*\*?|const|let|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)')


def run_all(changes, corpus, found, pr_added):
    def fresh(path, no):
        return no not in pr_added.get(path, ())

    for ch in changes:
        if ch.base is None:
            continue
        if ch.lang == 'ruby':
            ruby_methods(ch, corpus, found, fresh)
            ruby_classes(ch, corpus, found, fresh)
            if '/controllers/' in ch.path:
                controller_actions(ch, corpus, found, fresh)
        elif ch.path.endswith(('.ts', '.tsx', '.js', '.vue')):
            ts_exports(ch, corpus, found, fresh)
        if re.search(r'locales/.*\.json$', ch.path):
            i18n_keys(ch, corpus, found, fresh)
    untested(changes, found)


# ---------------------------------------------------------------- 呼び出し元

def callers(fresh, corpus, qualified, name, skip=()):
    """(呼び出し箇所, 確度)。クラスメソッドは `Owner.name` で探せるので exact。"""
    if '.' in qualified and '#' not in qualified:
        owner = qualified.rsplit('.', 1)[0].split('::')[-1]
        rx = re.compile(r'\b' + re.escape(owner) + r'(\.|&\.)' + re.escape(name) + r'(?![\w?!])')
        hits = [{'file': p, 'line': n, 'code': t} for p, n, t in corpus.grep(rx, owner + '.')
                if fresh(p, n) and not is_comment(t) and (p, n) not in skip]
        if hits:
            return hits, 'exact'
    rx = re.compile(r'(?<![\w@$])' + re.escape(name) + r'(?![\w?!=])')
    hits = [{'file': p, 'line': n, 'code': t} for p, n, t in corpus.grep(rx, name)
            if fresh(p, n) and not is_comment(t) and (p, n) not in skip
            and not re.search(r'def\s+(self\.)?' + re.escape(name) + r'(?![\w?!])', t)]
    return hits, 'name_only'


def signature(params):
    sig = {'req': 0, 'opt': 0, 'splat': False, 'kwreq': set(), 'kwopt': set(), 'kwsplat': False}
    for p in split_top(params):
        if p.startswith('**') or p == '...':
            sig['kwsplat'] = True
            sig['splat'] = sig['splat'] or p == '...'
        elif p.startswith('*'):
            sig['splat'] = True
        elif p.startswith('&'):
            continue
        elif re.match(r'^\w+:\s*$', p):
            sig['kwreq'].add(p.rstrip()[:-1])
        elif re.match(r'^\w+:', p):
            sig['kwopt'].add(p.split(':')[0])
        elif '=' in p:
            sig['opt'] += 1
        else:
            sig['req'] += 1
    return sig


def breaking(old, new):
    """古い呼び方が新しい定義で動かなくなる理由と、新たに必須になったキーワード。"""
    reasons = []
    if new['req'] > old['req']:
        reasons.append('required args {} -> {}'.format(old['req'], new['req']))
    if not new['splat'] and new['req'] + new['opt'] < old['req'] + old['opt'] + (99 if old['splat'] else 0):
        reasons.append('accepts fewer args')
    needed = (new['kwreq'] - old['kwreq'] - old['kwopt']) | (old['kwopt'] & new['kwreq'])
    if needed:
        reasons.append('new required keyword: ' + ', '.join(sorted(needed)))
    dropped = (old['kwreq'] | old['kwopt']) - (new['kwreq'] | new['kwopt'])
    if dropped and not new['kwsplat']:
        reasons.append('keyword removed: ' + ', '.join(sorted(dropped)))
    return reasons, needed


# ---------------------------------------------------------------- Ruby

def ruby_methods(ch, corpus, found, fresh):
    old_defs, new_defs = ruby_defs(ch.base), ruby_defs(ch.head)
    new_names = {d['name'] for d in new_defs.values()}

    for q, d in old_defs.items():
        if q in new_defs or d['name'] in new_names or d['name'] in GENERIC_METHODS or '/controllers/' in ch.path:
            continue
        hits, match = callers(fresh, corpus, q, d['name'])
        if hits:
            found.add(GROUP, 'removed_but_still_called', 'error' if match == 'exact' else 'warning', ch.path, None,
                      'Đã xoá method `{}` nhưng còn {} chỗ gọi.'.format(q, len(hits)),
                      'Method `{}` was removed but {} call site(s) remain.'.format(q, len(hits)),
                      removed=q, match=match, call_site_count=len(hits), call_sites=hits[:10])

    for q, nd in new_defs.items():
        od = old_defs.get(q)
        if not od or od['params'] == nd['params'] or nd['name'] in GENERIC_METHODS:
            continue
        reasons, needed = breaking(signature(od['params']), signature(nd['params']))
        if not reasons:
            continue
        hits, match = callers(fresh, corpus, q, nd['name'], skip={(ch.path, nd['line'])})
        hits = [h for h in hits if not (needed and all(k + ':' in h['code'] for k in needed))]
        if hits:
            found.add(GROUP, 'signature_changed', 'error' if match == 'exact' else 'warning', ch.path, nd['line'],
                      'Đổi tham số `{}` ({}) nhưng {} chỗ gọi chưa sửa theo.'.format(q, '; '.join(reasons), len(hits)),
                      'Parameters of `{}` changed ({}); {} call site(s) not updated.'.format(q, '; '.join(reasons), len(hits)),
                      before='({})'.format(od['params']), after='({})'.format(nd['params']), match=match,
                      call_site_count=len(hits), call_sites=hits[:10])

    # 広く使われるファイル（step 2 で critical/high）の既存メソッドを書き換えた → 人が読むリスト
    risk = found.risk_by_file.get(ch.path)
    if risk in ('critical', 'high'):
        for q in ch.symbols.get('def') or []:
            nd = new_defs.get(q)
            if nd and nd['name'] not in GENERIC_METHODS:
                hits, match = callers(fresh, corpus, q, nd['name'], skip={(ch.path, nd['line'])})
                found.add(GROUP, 'shared_method_rewritten', 'warning', ch.path, nd['line'],
                          'Sửa method có sẵn `{}` trong file dùng rộng ({}); {} chỗ gọi cần giữ nguyên hành vi.'.format(q, risk, len(hits)),
                          'Rewrote existing `{}` in a widely used file ({}); {} call site(s) rely on it.'.format(q, risk, len(hits)),
                          match=match, call_site_count=len(hits), call_sites=hits[:10])


def ruby_classes(ch, corpus, found, fresh):
    def classes(text):
        if text is None:
            return set()
        syms = prdiff.resolve_bounds(prdiff.ruby_symbols(text), text.count('\n') + 1)
        return {prdiff.qualify(s, syms) for s in syms if s['kind'] in ('class', 'module')}

    for q in sorted(classes(ch.base) - classes(ch.head)):
        short = q.split('::')[-1]
        if len(short) < 5:
            continue
        redefined = corpus.grep(re.compile(r'^\s*(class|module)\s+([\w:]*::)?' + re.escape(short) + r'\b'), short)
        if any(p != ch.path for p, _, _ in redefined):
            continue
        hits = [{'file': p, 'line': n, 'code': t}
                for p, n, t in corpus.grep(re.compile(r'(?<!\w)' + re.escape(short) + r'(?!\w)'), short)
                if p != ch.path and fresh(p, n) and not is_comment(t)]
        if hits:
            found.add(GROUP, 'removed_class_still_used', 'error', ch.path, None,
                      'Class `{}` không còn nhưng {} chỗ vẫn dùng.'.format(q, len(hits)),
                      'Class `{}` no longer exists but {} place(s) still use it.'.format(q, len(hits)),
                      removed=q, call_site_count=len(hits), call_sites=hits[:10])


def controller_actions(ch, corpus, found, fresh):
    m = re.search(r'controllers/(.+)_controller\.rb$', ch.path)
    if not m:
        return
    ctrl = m.group(1)
    new = {d['name'] for d in ruby_defs(ch.head).values()}
    route_files = [p for p in corpus.texts if re.search(r'(^|/)config/routes', p)]
    for d in ruby_defs(ch.base).values():
        action = d['name']
        if action in new:
            continue
        rx = re.compile(r'[\'"](' + re.escape(ctrl) + '|' + re.escape(ctrl.split('/')[-1]) + ')#' + re.escape(action) + r'[\'"]')
        hits = [{'file': p, 'line': n, 'code': t} for p, n, t in corpus.grep(rx, action, route_files) if fresh(p, n)]
        if hits:
            found.add(GROUP, 'removed_action_still_routed', 'error', ch.path, None,
                      'Đã xoá action `{}#{}` nhưng routes vẫn trỏ tới.'.format(ctrl, action),
                      'Action `{}#{}` was removed but routes still point to it.'.format(ctrl, action),
                      removed='{}#{}'.format(ctrl, action), call_sites=hits[:10])


# ---------------------------------------------------------------- フロント

def ts_exports(ch, corpus, found, fresh):
    old = {m.group(1) for m in map(RE_TS_EXPORT.match, ch.base_lines) if m}
    new = {m.group(1) for m in map(RE_TS_EXPORT.match, ch.head_lines) if m}
    module = os.path.splitext(os.path.basename(ch.path))[0]
    for name in sorted(old - new):
        if len(name) < 4:
            continue
        rx = re.compile(r'\bimport\b[^;]*\b' + re.escape(name) + r'\b')
        hits = [{'file': p, 'line': n, 'code': t} for p, n, t in corpus.grep(rx, name)
                if fresh(p, n) and module in ' '.join(corpus.lines(p)[n - 1:n + 6])]
        if hits:
            found.add(GROUP, 'removed_but_still_called', 'error', ch.path, None,
                      'Đã bỏ export `{}` nhưng {} file còn import.'.format(name, len(hits)),
                      'Export `{}` was removed but {} file(s) still import it.'.format(name, len(hits)),
                      removed=name, match='exact', call_site_count=len(hits), call_sites=hits[:10])


def i18n_keys(ch, corpus, found, fresh):
    """ロケールから消したキーを、まだ `$t('...')` で使っている（画面にキー名がそのまま出る）。"""
    lang = re.search(r'locales/(ja|en)/|translation\.(ja|en)\.json', ch.path)
    if not lang:
        return
    lang = lang.group(1) or lang.group(2)
    elsewhere = set()
    out, _ = run('git', 'ls-files', 'app/frontend/javascripts/locales')
    for p in out.split('\n'):
        same_lang = '/{}/'.format(lang) in p or '.{}.json'.format(lang) in p
        if p != ch.path and p.endswith('.json') and same_lang and os.path.isfile(p):
            with open(p, encoding='utf-8') as fh:
                elsewhere |= set(flatten_json(fh.read()))
    head = flatten_json(ch.head)
    for key in sorted(k for k in flatten_json(ch.base) if k not in head and k not in elsewhere):
        variants = {key, key[len('global.'):]} if key.startswith('global.') else {key}
        hits = [{'file': p, 'line': n, 'code': t} for v in variants
                for p, n, t in corpus.grep(re.compile(r'[\'"`]' + re.escape(v) + r'[\'"`]'), v) if fresh(p, n)]
        if hits:
            found.add(GROUP, 'removed_i18n_key_still_used', 'error', ch.path, None,
                      'Đã xoá key dịch `{}` nhưng {} chỗ vẫn dùng.'.format(key, len(hits)),
                      'Translation key `{}` was removed but {} place(s) still use it.'.format(key, len(hits)),
                      removed=key, call_site_count=len(hits), call_sites=hits[:10])


# ---------------------------------------------------------------- テスト

def untested(changes, found):
    """新しい public メソッドに spec が無い（CLAUDE.local.md「新しく書いたコードには必ず spec を書く」）。"""
    for ch in changes:
        if ch.lang != 'ruby' or ch.head is None or is_spec(ch.path):
            continue
        m = re.match(r'^((?:packs/[^/]+/)?)app/((models|services|jobs|forms?)/.+)\.rb$', ch.path)
        if not m or '/concerns/' in ch.path:
            continue
        spec = '{}spec/{}_spec.rb'.format(m.group(1), m.group(2))
        spec_text = ''
        if os.path.isfile(spec):
            with open(spec, encoding='utf-8', errors='replace') as fh:
                spec_text = fh.read()
        defs = ruby_defs(ch.head)
        for q in ch.symbols.get('new_def') or []:
            d = defs.get(q)
            if not d or d['name'] == 'initialize' or private(ch.head_lines, d):
                continue
            if spec_text and (d['name'] == 'call' or re.search(r'(?<!\w)' + re.escape(d['name']) + r'(?![\w?!])', spec_text)):
                continue
            found.add(GROUP, 'untested_change', 'warning', ch.path, d['line'],
                      'Method mới `{}` chưa có test ({}).'.format(q, 'spec không nhắc tới' if spec_text else 'không có ' + spec),
                      'New method `{}` has no test ({}).'.format(q, 'spec never mentions it' if spec_text else spec + ' missing'),
                      'CLAUDE.local.md', method=q, spec=spec)


def private(lines, d):
    head = lines[d['line'] - 1]
    if re.match(r'^\s*(private|protected)\s+def\b', head):
        return True
    if 'self.' in head and any(re.match(r'^\s*private_class_method\b.*:' + re.escape(d['name']) + r'\b', l) for l in lines):
        return True
    indent = prdiff.indent_of(head)
    for l in reversed(lines[:d['line'] - 1]):
        s = l.strip()
        if s and prdiff.indent_of(l) < indent and re.match(r'(class|module)\b', s):
            return False
        if s in ('private', 'protected') and prdiff.indent_of(l) == indent:
            return True
    return False
