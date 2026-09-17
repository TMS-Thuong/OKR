# checks_rules.py - 「規約に反していないか」
#
# 追加行だけを見る（既存の違反を掘り返すと、この PR 自身の問題が埋もれる）。
# 規約の出典は2つ:
#   .claude/rules/*.md  … repo の規約
#   CLAUDE.local.md     … チームのローカル規約（.gitignore 対象。レビューで繰り返し指摘された「必須」）
# `pr-check:ignore <理由>` を含む行は対象外。

import re

from common import is_comment, is_spec, ruby_defs

GROUP = 'rules'
LOCAL = 'CLAUDE.local.md'

RUBY = r'\.(rb|rake|erb|jbuilder)$'
FRONT = r'\.(vue|ts|tsx|js)$'
SPEC = r'((^|/)spec/|\.spec\.[jt]s$|\.test\.[jt]s$)'
APP = r'^(packs/[^/]+/)?app/'

# (check, severity, 対象パス, 除外パス, 行, vi, en, 規約)
LINE_RULES = [
    # ---- Ruby 3.3 互換（3.0.5 では動いてしまうので気付けない）
    ('ruby33_incompatible', 'error', RUBY, None,
     r'\b(File|Dir)\.exists\?|\bTime\.new\(\s*[\'"]|\bENV\.(dup|clone)\b|\.(un)?taint\b(?![\w?!])|^(?!\s*#).*\b(Fixnum|Bignum)\b',
     'Cú pháp chạy trên Ruby 3.0.5 nhưng lỗi trên 3.3 (exists? / Time.new("…") / ENV.dup / taint / Fixnum).',
     'Works on Ruby 3.0.5 but breaks on 3.3 (exists? / Time.new("…") / ENV.dup / taint / Fixnum).',
     '.claude/rules/ruby-version-compat.md'),
    # ---- 消し忘れ
    ('debug_left', 'error', RUBY, None, r'\bbinding\.(pry|irb)\b|^\s*byebug\b|^\s*debugger\b',
     'Còn lệnh debug.', 'Debug statement left in.', None),
    ('debug_left', 'warning', FRONT, SPEC, r'^\s*debugger\b|\bconsole\.(log|debug)\(',
     'Còn `debugger` / `console.log`.', '`debugger` / `console.log` left in.', None),
    ('focused_or_skipped_test', 'error', SPEC, None,
     r'^\s*(fit|fdescribe|fcontext|xit|xdescribe|xcontext)\b|\bfocus:\s*true\b|\b(it|describe|test)\.(only|skip)\(',
     'Test bị focus/skip.', 'Focused or skipped test.', '.claude/rules/testing.md'),
    # ---- TypeScript / Vue
    ('ts_any', 'error', r'\.(vue|ts)$', r'(\.d\.ts$|' + SPEC + ')', r'(:\s*any\b|\bas\s+any\b|<any>)',
     'Dùng kiểu `any` (bị cấm).', '`any` type is prohibited.', '.claude/rules/typescript.md'),
    ('vue_v_html', 'error', r'\.vue$', r'z-safe-html\.vue$', r'\bv-html\s*=',
     '`v-html` bị cấm (XSS); dùng `z-safe-html`.', '`v-html` is forbidden (XSS); use `z-safe-html`.', '.claude/rules/vue.md'),
    # ---- Packwerk
    ('packwerk', 'error', r'(^|/)package_todo\.yml$', None, r'^\s*-\s',
     'Thêm vào `package_todo.yml` để né Packwerk; phải sửa code.', 'Adds to `package_todo.yml` to silence Packwerk; fix the code.',
     '.claude/rules/architecture.md'),
    ('packwerk', 'error', APP + r'models/', None, r'^\s*#\s*pack_public:\s*true',
     'Model không được `pack_public`.', 'Models must never be `pack_public`.', '.claude/rules/architecture.md'),
    # ---- CLAUDE.local.md: RSpec でモック・スタブを使わない
    ('spec_mock_stub', 'error', r'_spec\.rb$', None,
     r'\b(allow|expect)\(.*\)\.to\s+(receive|have_received)\b|\b(allow|expect)_any_instance_of\b|\bstub_const\(|\b(instance_)?double\(',
     'Spec dùng mock/stub; dựng trạng thái bằng record thật (FactoryBot).',
     'Spec uses a mock/stub; build the state with real records (FactoryBot).', LOCAL),
    # ---- CLAUDE.local.md: Controller にクエリ・トランザクション・ジョブ登録を書かない
    ('controller_business_logic', 'warning', APP + r'controllers/', None,
     r'\.(where|pluck|find_by!?|joins|includes|group|order|exists\?|sum|not_deleted)\b|\b(transaction|with_lock)\b|\.perform_later\b',
     'Controller chứa truy vấn/transaction/job; chuyển vào Service, controller chỉ gọi Service rồi render.',
     'Controller contains a query/transaction/job; move it into a Service — controllers only call it and render.', LOCAL),
    # ---- CLAUDE.local.md: Service から別の Service を呼ばない
    ('service_calls_service', 'warning', APP + r'services/(?!concerns/)', None, r'\b[A-Z]\w*Service\.(call!?|new)\b',
     'Service gọi Service khác; phần dùng chung đưa vào Concern rồi include.',
     'Service calls another Service; share the logic as a Concern instead.', LOCAL),
    # ---- CLAUDE.local.md: 業務の計算を SQL に書かない / 条件は DSL で書く
    ('sql_calculation', 'warning', RUBY, SPEC,
     r'(?i)\b(COALESCE|IFNULL|GREATEST|LEAST)\s*\(|\bSUM\s*\(|JOIN\s*\(\s*SELECT|\(\s*[\w.#{}]+\s*[-+]\s*[\w.#{}(]+.*\)\s*(AS|>|<|=)',
     'Tính toán nghiệp vụ trong SQL; SQL chỉ lấy dữ liệu, tính bằng Ruby.',
     'Business calculation inside SQL; fetch with SQL, compute in Ruby.', LOCAL),
    ('string_sql_condition', 'warning', RUBY, SPEC, r'\.(where|not|order|group|having)\(\s*["\']',
     'Điều kiện SQL viết bằng chuỗi; dùng DSL ActiveRecord (Hash/Symbol/Range).',
     'SQL condition written as a string; use the ActiveRecord DSL (Hash/Symbol/Range).', LOCAL),
]

# ループ（do |x| / { |x| }）
LOOP = re.compile(r'\.(each\w*|map|flat_map|filter_map|select|reject|find_each|sum|group_by|sort_by|index_by|count|any\?|all\?)\b'
                  r'[^|{]*?(\bdo\b|\{)\s*\|([^|]*)\|')


def run_all(changes, corpus, found, commits):
    compiled = [(c, s, re.compile(p), re.compile(x) if x else None, re.compile(l), vi, en, r)
                for c, s, p, x, l, vi, en, r in LINE_RULES]
    for ch in changes:
        if ch.head is None:
            continue
        for no, line in ch.added_text():
            if 'pr-check:ignore' in line or (is_comment(line) and not ch.path.endswith('.yml')):
                continue
            for check, sev, prx, xrx, lrx, vi, en, rule in compiled:
                if prx.search(ch.path) and not (xrx and xrx.search(ch.path)) and lrx.search(line):
                    found.add(GROUP, check, sev, ch.path, no, vi, en, rule, code=line.strip()[:200])
        if ch.path.endswith('.rb') and not is_spec(ch.path):
            model_logic(ch, found)
            loops(ch, found)
        if ch.path.endswith('.vue'):
            vue_template(ch, found)
    for sha, subject in commits:
        if not re.match(r'^(test|update|change|feat): \S+ .+ #time \S+', subject) and not subject.startswith('Merge '):
            found.add(GROUP, 'commit_message_format', 'warning', '(commit)', None,
                      'Commit {} sai format `(feat|update|change|test): KEY nội dung #time 1h`.'.format(sha[:10]),
                      'Commit {} does not match `(feat|update|change|test): KEY summary #time 1h`.'.format(sha[:10]),
                      '.claude/rules/git.md', subject=subject)


def model_logic(ch, found):
    """CLAUDE.local.md「業務ロジックを Model に書かない」: 設定・他モデル・集計を使う新規メソッド。"""
    if not re.match(APP + r'models/(?!concerns/)', ch.path):
        return
    defs = ruby_defs(ch.head)
    for q in ch.symbols.get('new_def') or []:
        d = defs.get(q)
        if not d:
            continue
        body = '\n'.join(ch.head_lines[d['line'] - 1:d['last_line']])
        if re.search(r'GeneralSetting|can_use_func|<<~SQL|sanitize_sql|\.(joins|group|sum|pluck)\(|\b[A-Z]\w+\.(where|find_by|exists\?)', body):
            found.add(GROUP, 'logic_in_model', 'warning', ch.path, d['line'],
                      'Thêm logic nghiệp vụ `{}` vào Model (truy vấn/cài đặt/tính toán); đưa vào Service/Concern.'.format(q),
                      'Adds business logic `{}` to a Model (queries/settings/calculation); move it to a Service/Concern.'.format(q),
                      LOCAL, method=q)


def loops(ch, found):
    """CLAUDE.local.md「ループの外で決まる判定をループの中に書かない」: ループ内のクエリと、要素に依存しない条件。"""
    lines = ch.head_lines
    stack = []  # (終了行, ブロック変数)
    for no, line in enumerate(lines, 1):
        stack = [s for s in stack if s[0] >= no]
        m = LOOP.search(line)
        if m and m.group(2) == 'do':
            indent = len(line) - len(line.lstrip())
            end = next((j + 1 for j in range(no, len(lines)) if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= indent), no)
            stack.append((end, {v.strip(' *&()') for v in m.group(3).split(',')}))
            continue
        if not stack or no not in ch.added or is_comment(line):
            continue
        if re.search(r'\.(where|find_by!?|exists\?|pluck)\(|\bGeneralSetting\.\w+', line):
            found.add(GROUP, 'query_in_loop', 'warning', ch.path, no,
                      'Truy vấn DB trong vòng lặp (số query = số phần tử); lấy trước 1 lần ngoài vòng lặp.',
                      'DB query inside a loop (one per element); fetch once before the loop.', LOCAL, code=line.strip()[:200])
        loop_vars = set().union(*(s[1] for s in stack))
        rhs = re.sub(r'^\s*[@\w.\[\]]+\s*(\|\||&&)?=(?![=~])\s*', '', line)
        cond = re.match(r'^\s*(?:return\s+)?([^?(){}\n]+?)\s\?\s[^:]+\s:\s', rhs) or re.search(r'\s(?:if|unless)\s+(.+?)\s*$', line)
        if cond:
            tokens = {t.split('.')[0] for t in re.findall(r'@?[a-z_][\w.]*[?!]?', re.sub(r'(["\']).*?\1|:\w+', '', cond.group(1)))}
            tokens -= {'nil', 'true', 'false', 'self', 'and', 'or', 'not'}
            if tokens and not tokens & loop_vars and all(t.startswith('@') or t.endswith('?') for t in tokens):
                found.add(GROUP, 'loop_invariant_condition', 'warning', ch.path, no,
                          'Điều kiện `{}` không đổi theo phần tử nhưng kiểm tra mỗi vòng; quyết định 1 lần ngoài vòng lặp.'.format(cond.group(1).strip()[:60]),
                          'Condition `{}` does not depend on the element; decide once outside the loop.'.format(cond.group(1).strip()[:60]),
                          LOCAL, code=line.strip()[:200])


def vue_template(ch, found):
    """テンプレートに直書きした日本語と、GTM 属性の無い新しいボタン。"""
    lines = ch.head_lines
    top = next((i for i, l in enumerate(lines, 1) if l.startswith('<template')), None)
    bottom = max((i for i, l in enumerate(lines, 1) if l.startswith('</template>')), default=None)
    if not top or not bottom:
        return
    for no, line in ch.added_text():
        if not top < no < bottom or '<!--' in line:
            continue
        if re.search('[\u3040-\u30ff\u4e00-\u9fff]', line) and '$t(' not in line:
            found.add(GROUP, 'hardcoded_text', 'warning', ch.path, no,
                      'Chữ tiếng Nhật viết cứng trong template; dùng `$t()`.', 'Hard-coded Japanese in template; use `$t()`.',
                      '.claude/rules/vue.md', code=line.strip()[:200])
        if re.search(r'<(v-btn|z-[\w-]*button)(\s|>|$)', line):
            tag = ' '.join(lines[no - 1:no + 15])
            tag = tag[:tag.find('>') + 1] if '>' in tag else tag
            gtm = re.search(r'(?<!:)data-gtm-id="([^"]+)"', tag)
            if not gtm:
                found.add(GROUP, 'gtm', 'warning', ch.path, no,
                          'Nút mới thiếu `data-gtm-id`.', 'New button lacks `data-gtm-id`.', '.claude/rules/gtm.md', code=line.strip()[:200])
            elif not re.match(r'^[a-z0-9-]+$', gtm.group(1)):
                found.add(GROUP, 'gtm', 'error', ch.path, no,
                          '`data-gtm-id` "{}" chỉ được chữ thường, số, `-`.'.format(gtm.group(1)),
                          '`data-gtm-id` "{}" must be lowercase letters, digits and `-`.'.format(gtm.group(1)), '.claude/rules/gtm.md')
