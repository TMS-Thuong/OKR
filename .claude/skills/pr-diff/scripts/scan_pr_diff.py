#!/usr/bin/env python3
# scan_pr_diff.py - PR で変わった行を読み取る（KR2 step 1）
#
# 出力は .ai/code-map/raw/pr-diff.json だけ。良し悪しの判断は一切しない。
# 「どのファイルの、どの行が、どの定義の中で変わったか」に限る。
# KR1 の impact.json との突き合わせもレビュー判定も後続 skill の仕事。
#
# 標準ライブラリのみ。Python 3.8 以降で動く記法に限る。

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

TOOL_VERSION = '3.0.0'

# 拡張子から言語を決める。定義まで掘るのは ruby だけ（後述 ruby_symbols 参照）。
# ここに無い拡張子はファイル単位の差分のみ出す。
LANG_BY_EXT = {
    '.rb': 'ruby',
    '.rake': 'ruby',
    '.erb': 'erb',
    '.vue': 'vue',
    '.ts': 'ts',
    '.tsx': 'ts',
    '.js': 'js',
    '.jsx': 'js',
    '.yml': 'yaml',
    '.yaml': 'yaml',
    '.json': 'json',
    '.sql': 'sql',
    '.md': 'markdown',
}

SYMBOL_LANGS = ['ruby']


def run(*cmd):
    """コマンドを実行し (標準出力, 成否) を返す。stderr は捨てる。"""
    try:
        proc = subprocess.run(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return '', False
    out = proc.stdout.decode('utf-8', errors='replace')
    return out, proc.returncode == 0


def run_or_die(*cmd):
    out, ok = run(*cmd)
    if not ok:
        raise RuntimeError('コマンドが失敗しました: ' + ' '.join(cmd))
    return out


def parse_args():
    p = argparse.ArgumentParser(
        prog='scan_pr_diff.py',
        description='PR で変わった行を読み取る（KR2 step 1）',
    )
    p.add_argument('--base', help='ベースrefを明示する（既定: origin/master との merge-base）')
    p.add_argument('--head', default='HEAD', help='ヘッドrefを明示する（既定: HEAD）')
    p.add_argument('--working', action='store_true', help='コミット前の作業ツリーの変更を対象にする')
    p.add_argument('--staged', action='store_true', help='git add 済み・未コミットの変更を対象にする')
    p.add_argument('--pr', help='GitHub PR番号。gh があればタイトル等を添える')
    p.add_argument('--out', help='出力先（既定: .ai/code-map/raw/pr-diff.json）')
    p.add_argument('--root', help='リポジトリルート')
    p.add_argument('-q', '--quiet', action='store_true', help='進捗を出さない')
    return p.parse_args()


def rev_exists(ref):
    _, ok = run('git', 'rev-parse', '--verify', '--quiet', ref + '^{commit}')
    return ok


# PR レビューの対象は「この枝が足した分」であって「master との現在の差」ではない。
# master が先に進んでいると git diff master HEAD は他人の変更まで
# 「消した」ように見せるため、必ず分岐点（merge-base）を基準にする。
def resolve_base(explicit, head):
    if explicit:
        return explicit
    for cand in ('origin/master', 'origin/main', 'master', 'main'):
        if not rev_exists(cand):
            continue
        mb, ok = run('git', 'merge-base', cand, head)
        if ok and mb.strip():
            return mb.strip()
    return None


def new_entry(path):
    return {
        'path': path,
        'old_path': None,
        'status': 'modified',
        'binary': False,
        'added': [],
        'removed': [],
    }


RE_DIFF_HEADER = re.compile(r'^diff --git a/(.+?) b/(.+)$')
RE_RENAME_FROM = re.compile(r'^rename from (.+)$')
RE_HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def line_count(ranges):
    """from/to の並びが何行あるかを数える。両端を含む。"""
    return sum(r['to'] - r['from'] + 1 for r in ranges)


def parse_diff(raw_diff):
    files = []
    current = None
    for line in raw_diff.split('\n'):
        line = line.rstrip('\r')

        m = RE_DIFF_HEADER.match(line)
        if m:
            if current:
                files.append(current)
            old_p, new_p = m.group(1), m.group(2)
            current = new_entry(new_p)
            if old_p != new_p:
                current['old_path'] = old_p
            continue

        if line.startswith('new file mode'):
            if current:
                current['status'] = 'added'
            continue

        if line.startswith('deleted file mode'):
            if current:
                current['status'] = 'deleted'
            continue

        m = RE_RENAME_FROM.match(line)
        if m:
            if current:
                current['status'] = 'renamed'
                current['old_path'] = m.group(1)
            continue

        if line.startswith('Binary files '):
            if current:
                current['binary'] = True
            continue

        m = RE_HUNK.match(line)
        if m:
            if not current:
                continue
            old_start = int(m.group(1))
            old_count = int(m.group(2) or '1')
            new_start = int(m.group(3))
            new_count = int(m.group(4) or '1')
            # 追加行と削除行は別の座標系にある。added は変更後のファイル、
            # removed は変更前のファイルの行番号で、同じ行番号でも同じ場所を指さない。
            # 削除された行は変更後のファイルに存在しないので、混ぜることはできない。
            if new_count > 0:
                current['added'].append({'from': new_start, 'to': new_start + new_count - 1})
            if old_count > 0:
                current['removed'].append({'from': old_start, 'to': old_start + old_count - 1})

    if current:
        files.append(current)
    return files


def lang_of(path):
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower())


def indent_of(line):
    return len(line) - len(line.lstrip(' \t'))


# 対象ファイルの中身を取る。diff に出たファイルだけを開き、そこから先へは辿らない。
def file_content(path, ref):
    if ref:
        out, ok = run('git', 'show', '{}:{}'.format(ref, path))
        return out if ok else None
    try:
        if not os.path.exists(path):
            return None
        with open(path, 'rb') as fh:
            return fh.read().decode('utf-8', errors='replace')
    except OSError:
        return None


# 変更前のファイルに存在した def の qualified 名。
# 新規ファイルは「変更前」が無いので空集合、削除ファイルは比較する意味がないので None。
def prior_defs(entry, base_ref, mode):
    status = entry['status']
    if status == 'deleted':
        return None
    if status == 'added':
        return set()
    ref = base_ref if mode == 'branch' else 'HEAD'
    text = file_content(entry['old_path'] or entry['path'], ref)
    if text is None:
        return None
    syms = resolve_bounds(ruby_symbols(text), text.count('\n') + 1)
    return set(qualify(s, syms) for s in syms if s['kind'] == 'def')


RE_END = re.compile(r'^end[\s.,)\]}]')
RE_CLASS = re.compile(r'^(class|module)\s+([A-Z][\w:]*)')
RE_DEF = re.compile(r'^def\s+(self\.)?([\w?!=\[\]<>+\-*/%]+)')
RE_ONELINE_END = re.compile(r';\s*end$')
RE_ENDLESS_DEF = re.compile(r'^def\s+[^=\s(]+(\([^)]*\))?\s*=\s*\S')


# Ruby だけ class / module / def を字下げで積んで閉じ区間まで取る。
# zaico の Ruby は rubocop 済みで字下げが揃っているため `end` の位置合わせが効く。
# Vue/TS を同じやり方でやると閉じ位置を外して誤った関数名が付くので、
# 言語ごと対象外にしてある（間違った名前を出すより、名前なしの方が安全）。
def ruby_symbols(text):
    stack = []
    symbols = []
    for no, raw in enumerate(text.split('\n'), 1):
        line = raw.rstrip('\r')
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        indent = indent_of(line)

        if stripped == 'end' or RE_END.match(stripped):
            while stack and stack[-1]['indent'] >= indent:
                done = stack.pop()
                done['last_line'] = no
                if done['indent'] == indent:
                    break
            continue

        m = RE_CLASS.match(stripped)
        if m:
            sym = {'kind': m.group(1), 'name': m.group(2),
                   'line': no, 'indent': indent, 'last_line': None}
            symbols.append(sym)
            # `class Foo < Bar; end` のような1行定義は積むと閉じ損ねる。
            if RE_ONELINE_END.search(stripped):
                sym['last_line'] = no
            else:
                stack.append(sym)
            continue

        m = RE_DEF.match(stripped)
        if m:
            name = '{}{}'.format(m.group(1) or '', m.group(2))
            sym = {'kind': 'def', 'name': name,
                   'line': no, 'indent': indent, 'last_line': None}
            symbols.append(sym)
            # エンドレスメソッド（def x = y）と1行 def は end を持たない。
            if RE_ENDLESS_DEF.match(stripped) or RE_ONELINE_END.search(stripped):
                sym['last_line'] = no
            else:
                stack.append(sym)

    return symbols


# last_line が取れなかった定義は「同じか外側の字下げに現れる次の定義の直前」で代用する。
def resolve_bounds(symbols, total_lines):
    for i, sym in enumerate(symbols):
        if sym['last_line']:
            continue
        nxt = next((s for s in symbols[i + 1:] if s['indent'] <= sym['indent']), None)
        sym['last_line'] = nxt['line'] - 1 if nxt else total_lines
    return symbols


def enclosing(symbols, ranges):
    if not symbols or not ranges:
        return []

    hits = []
    seen = set()
    for rng in ranges:
        start, last = rng['from'], rng['to']
        for sym in symbols:
            if sym['line'] > last or sym['last_line'] < start:
                continue
            key = (sym['kind'], sym['name'], sym['line'])
            if key in seen:
                continue
            seen.add(key)
            hits.append(sym)

    hits.sort(key=lambda s: s['line'])
    return hits


# `Web::ItemsController#create` の形にする。KR2 の次の段が
# KR1 の地図の controller#action と突き合わせるための鍵になる。
def qualify(sym, symbols):
    outer = [s for s in symbols
             if s['kind'] in ('class', 'module')
             and s['line'] < sym['line']
             and s['last_line'] >= sym['line']
             and s['indent'] < sym['indent']]
    scope = '::'.join(s['name'] for s in outer)

    if sym['kind'] != 'def':
        return sym['name'] if not scope else '{}::{}'.format(scope, sym['name'])
    if not scope:
        return sym['name']
    if sym['name'].startswith('self.'):
        return '{}.{}'.format(scope, sym['name'][5:])
    return '{}#{}'.format(scope, sym['name'])


def main():
    options = parse_args()

    root = options.root
    if root is None:
        out, ok = run('git', 'rev-parse', '--show-toplevel')
        if not ok:
            sys.exit('git リポジトリの中で実行してください。')
        root = out.strip()
    os.chdir(root)

    def log(msg):
        if not options.quiet:
            print(msg, file=sys.stderr)

    # ------------------------------------------------------------ 比較範囲

    head_ref = options.head
    if options.staged:
        mode = 'staged'
    elif options.working:
        mode = 'working'
    else:
        mode = 'branch'

    base_ref = resolve_base(options.base, head_ref) if mode == 'branch' else None

    diff_cmd = ['git', 'diff', '--no-color', '--find-renames', '--unified=0']
    if mode == 'staged':
        diff_cmd.append('--cached')
    elif mode == 'branch':
        if base_ref is None:
            sys.exit('ベースrefが決まりません。--base で明示してください。')
        diff_cmd.extend([base_ref, head_ref])

    span = ' ({}..{})'.format(base_ref[0:12], head_ref) if base_ref else ''
    log('差分を取得中: {}{}'.format(mode, span))
    raw_diff = run_or_die(*diff_cmd)

    # ------------------------------------------------------------ 差分の解析

    files = parse_diff(raw_diff)
    log('変更ファイル: {}'.format(len(files)))

    # ------------------------------------------------------------ 定義の抽出

    stats = {}

    def bump(key, by=1):
        stats[key] = stats.get(key, 0) + by

    no_symbol_langs = {}

    for f in files:
        lang = lang_of(f['path'])
        f['lang'] = lang
        bump('files_changed')
        bump('files_{}'.format(f['status']))
        bump('lines_added', line_count(f['added']))
        bump('lines_removed', line_count(f['removed']))
        f['symbols'] = {}

        if f['binary']:
            continue

        if lang not in SYMBOL_LANGS:
            key = lang or '(不明)'
            no_symbol_langs[key] = no_symbol_langs.get(key, 0) + 1
            continue

        # 削除ファイルは base 側、それ以外は変更後の姿を読む。
        if f['status'] == 'deleted':
            ref = base_ref
        else:
            ref = head_ref if mode == 'branch' else None
        path = (f['old_path'] or f['path']) if f['status'] == 'deleted' else f['path']
        text = file_content(path, ref)
        if text is None:
            continue

        all_syms = resolve_bounds(ruby_symbols(text), text.count('\n') + 1)

        # 変更前に存在した定義の名前。既存の def を書き換えたのか、
        # 新しい def を足しただけなのかは、ここでしか分からない。
        # 足しただけなら既存の呼び出し元はどれも影響を受けない。
        prior = prior_defs(f, base_ref, mode)

        # 出力は qualified 名だけにする。name / line / last_line は
        # qualify() と enclosing() の計算に使うだけで、step 2 以降は読まない。
        # 変更箇所の位置は added / removed の方が正確なので、定義の行境界は残さない。
        # 削除ファイルは変更前の姿を読んでいるので、照合も removed 側で行う。
        ranges = f['removed'] if f['status'] == 'deleted' else f['added']
        # kind ごとにまとめる。step 2 が照合するのは def と new_def だけで、
        # 素のクラス名は画面の鍵 (Class#action) と形が違うため決して一致しない。
        grouped = {}
        for sym in enclosing(all_syms, ranges):
            name = qualify(sym, all_syms)
            kind = sym['kind']
            if kind == 'def' and prior is not None and name not in prior:
                kind = 'new_def'
            grouped.setdefault(kind, []).append(name)
        f['symbols'] = grouped
        bump('symbols_touched', sum(len(v) for v in grouped.values()))

    # ------------------------------------------------------------ PR メタ

    pr_meta = None
    if options.pr:
        out, ok = run('gh', 'pr', 'view', options.pr, '--json',
                      'number,title,url,state,baseRefName,headRefName,author')
        if ok and out.strip():
            try:
                pr_meta = json.loads(out)
            except ValueError:
                pr_meta = None
        if pr_meta is None:
            log('gh から PR 情報を取得できませんでした。差分だけで続けます。')

    branch, _ = run('git', 'rev-parse', '--abbrev-ref', 'HEAD')
    head_sha, _ = run('git', 'rev-parse', head_ref)

    payload = {
        'meta': {
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'tool_version': TOOL_VERSION,
            'root': root,
            'mode': mode,
            'base': base_ref,
            'head': head_ref if mode == 'branch' else None,
            'head_sha': head_sha.strip(),
            'branch': branch.strip(),
            'symbol_langs': SYMBOL_LANGS,
            'pr': pr_meta,
        },
        'stats': stats,
        'files': files,
    }

    out_path = options.out or os.path.join(root, '.ai/code-map/raw/pr-diff.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write('\n')

    log('出力: {}'.format(out_path))

    print('変更ファイル {} 件 / +{} -{} 行 / 定義 {} 個'.format(
        stats.get('files_changed', 0),
        stats.get('lines_added', 0),
        stats.get('lines_removed', 0),
        stats.get('symbols_touched', 0),
    ))
    if no_symbol_langs:
        detail = ', '.join(
            '{} {}'.format(k, v)
            for k, v in sorted(no_symbol_langs.items(), key=lambda kv: -kv[1])
        )
        print('定義の抽出は Ruby のみ。ファイル単位のみで記録: {}'.format(detail))

    top = sorted(files, key=lambda f: -(line_count(f['added']) + line_count(f['removed'])))[:10]
    for f in top:
        names = ', '.join((f['symbols'].get('def') or f['symbols'].get('class') or [])[:4])
        print('  {:<58} +{:<5} -{:<5} {}'.format(
            f['path'], line_count(f['added']), line_count(f['removed']), names))


if __name__ == '__main__':
    main()
