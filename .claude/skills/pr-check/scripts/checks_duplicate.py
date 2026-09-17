# checks_duplicate.py - 「重複・不要なコード」
#
#   duplicate_method   : 新規メソッドの本文が既存メソッドと同一（名前は違ってよい）
#   duplicate_code     : 空白だけ揃えて完全一致する 6 行以上の塊（コピペ）
#   unused_new_method  : 追加したのにどこからも呼ばれていないメソッド
#   commented_out_code : コメントアウトしたコードを足している

import re
from collections import defaultdict

from common import GENERIC_METHODS, is_comment, is_spec, ruby_defs

GROUP = 'duplicate'
WINDOW = 6      # これ未満の一致は偶然（import の並び、end の連続）が多い
MIN_LINE = 8    # 正規化後これより短い行（end, }, else）は窓に入れない


def norm(line):
    s = re.sub(r'\s+', ' ', line.strip())
    return s if len(s) >= MIN_LINE and not is_comment(line) else ''


def run_all(changes, corpus, found, pr_added):
    spans = duplicate_methods(changes, corpus, found)
    duplicate_blocks(changes, corpus, found, pr_added, spans)
    unused_methods(changes, corpus, found)
    commented_out(changes, found)


def body_key(lines, d):
    body = [b for b in map(norm, lines[d['line']:d['last_line'] - 1]) if b]
    return '\n'.join(body) if len(body) >= 3 else None


def duplicate_methods(changes, corpus, found):
    targets = []
    for ch in changes:
        if ch.lang == 'ruby' and ch.head is not None:
            defs = ruby_defs(ch.head)
            for q in ch.symbols.get('new_def') or []:
                key = q in defs and body_key(ch.head_lines, defs[q])
                if key:
                    targets.append((ch.path, q, defs[q], key))
    spans = []
    if not targets:
        return spans
    wanted = {t[3] for t in targets}
    by_key = defaultdict(list)
    for path, text in corpus.texts.items():
        if path.endswith('.rb'):
            for q, d in ruby_defs(text).items():
                key = body_key(corpus.lines(path), d)
                if key in wanted:
                    by_key[key].append((path, q, d['line']))
    for path, q, d, key in targets:
        others = [o for o in by_key[key] if not (o[0] == path and o[1] == q)]
        if others:
            spans.append((path, d['line'], d['last_line']))
            found.add(GROUP, 'duplicate_method', 'warning', path, d['line'],
                      'Method mới `{}` giống hệt `{}` ({}:{}); dùng lại method có sẵn.'.format(q, others[0][1], others[0][0], others[0][2]),
                      'New method `{}` has the same body as `{}` ({}:{}); reuse it.'.format(q, others[0][1], others[0][0], others[0][2]),
                      same_as=[{'method': m, 'file': p, 'line': n} for p, m, n in others[:5]])
    return spans


def duplicate_blocks(changes, corpus, found, pr_added, method_spans):
    wanted = defaultdict(list)
    for ch in changes:
        if ch.head is None or not ch.path.endswith(('.rb', '.erb', '.vue', '.ts', '.js')):
            continue
        for r in ch.entry.get('added') or []:
            sig = [(no, norm(ch.head_lines[no - 1])) for no in range(r['from'], min(r['to'], len(ch.head_lines)) + 1)]
            sig = [s for s in sig if s[1]]
            for i in range(len(sig) - WINDOW + 1):
                wanted['\n'.join(s for _, s in sig[i:i + WINDOW])].append((ch.path, sig[i][0], sig[i + WINDOW - 1][0]))
    if not wanted:
        return
    first = {k.split('\n', 1)[0] for k in wanted}

    matches = defaultdict(list)
    for path in corpus.texts:
        lines = corpus.lines(path)
        if first.isdisjoint(l.strip() for l in lines):
            continue  # 窓の先頭行が1つも無いファイルは正規化自体を省く
        sig = [(no, s) for no, s in ((no, norm(l)) for no, l in enumerate(lines, 1)) if s]
        for i in range(len(sig) - WINDOW + 1):
            if sig[i][1] not in first:
                continue
            for src, sf, st in wanted.get('\n'.join(s for _, s in sig[i:i + WINDOW]), ()):
                of, ot = sig[i][0], sig[i + WINDOW - 1][0]
                if not (src == path and of <= st and ot >= sf):
                    matches[(src, path)].append((sf, st, of, ot))

    seen = set()
    for (src, other), spans in sorted(matches.items()):
        blocks = []
        for sf, st, of, ot in sorted(spans):
            if blocks and sf <= blocks[-1][1] + 1 and of <= blocks[-1][3] + 1:
                blocks[-1] = (blocks[-1][0], max(blocks[-1][1], st), blocks[-1][2], max(blocks[-1][3], ot))
            else:
                blocks.append((sf, st, of, ot))
        for sf, st, of, ot in blocks:
            pair = tuple(sorted([(src, sf), (other, of)]))
            covered = any(src == p and f <= sf and st <= t for p, f, t in method_spans)
            if pair in seen or covered:
                continue
            seen.add(pair)
            in_pr = of in pr_added.get(other, ())
            found.add(GROUP, 'duplicate_code', 'info' if is_spec(src) or is_spec(other) else 'warning', src, sf,
                      'Dòng {}-{} giống hệt {}:{}-{}{}.'.format(sf, st, other, of, ot, ' (cũng thêm trong PR)' if in_pr else ''),
                      'Lines {}-{} duplicate {}:{}-{}{}.'.format(sf, st, other, of, ot, ' (also added in this PR)' if in_pr else ''),
                      duplicate_of={'file': other, 'from': of, 'to': ot}, lines=st - sf + 1)


def unused_methods(changes, corpus, found):
    for ch in changes:
        if ch.lang != 'ruby' or ch.head is None or is_spec(ch.path):
            continue
        if re.search(r'/(controllers|jobs|mailers|policies|serializers)/|/lib/tasks/', ch.path):
            continue  # フレームワークが名前で呼ぶ
        defs = ruby_defs(ch.head)
        for q in ch.symbols.get('new_def') or []:
            d = defs.get(q)
            if not d or d['name'] in GENERIC_METHODS or d['name'].startswith(('validate', 'before_', 'after_')):
                continue
            rx = re.compile(r'(?<![\w@$])' + re.escape(d['name']) + r'(?![\w?!=])')
            refs = [1 for p, n, t in corpus.grep(rx, d['name']) if not (p == ch.path and n == d['line']) and not is_comment(t)]
            if not refs:
                found.add(GROUP, 'unused_new_method', 'warning', ch.path, d['line'],
                          'Method mới `{}` không được gọi ở đâu (kể cả spec, view).'.format(q),
                          'New method `{}` is not referenced anywhere (including specs and views).'.format(q), method=q)


def commented_out(changes, found):
    code_like = re.compile(r'^\s*(#|//)\s*(def |end$|if |unless |return\b|const |let |import |\w+\.\w+\(|\w+ = |\})')
    for ch in changes:
        if ch.head is None or is_spec(ch.path) or not ch.path.endswith(('.rb', '.vue', '.ts', '.js')):
            continue
        run_start, run_len = None, 0
        for no in sorted(ch.added) + [None]:
            hit = no is not None and no <= len(ch.head_lines) and code_like.match(ch.head_lines[no - 1])
            if hit and run_start is not None and no == run_start + run_len:
                run_len += 1
                continue
            if run_len >= 3:
                found.add(GROUP, 'commented_out_code', 'warning', ch.path, run_start,
                          '{} dòng code bị comment lại; xoá đi, git đã giữ lịch sử.'.format(run_len),
                          '{} lines of commented-out code; delete them, git keeps history.'.format(run_len), lines=run_len)
            run_start, run_len = (no, 1) if hit else (None, 0)
