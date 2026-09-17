#!/usr/bin/env ruby
# frozen_string_literal: true

# KR1 step 3 — turn impact.json into one browsable HTML page.
#
# Reads only impact.json: no source files, no analysis. Every number on the page
# comes from step 2. If a fact is missing, extend step 1 or step 2 — the moment
# this script starts computing, the page stops being reproducible.
#
# It draws nothing either. assets/app.html is a hand-written page with Cytoscape.js
# inlined; this script only names screens and files and injects the dataset into
# it, so nobody needs npm, a toolchain, network, or any installed app to get a page.
# The page source is readable — edit assets/app.html directly.

require 'json'
require 'fileutils'
require 'optparse'
require 'time'

EXPECTED_IMPACT_SCHEMA = 1
HERE = File.expand_path(__dir__)

options = { impact: nil, out: nil, root: nil, lang: 'vi', default_screen: nil, config: nil, refresh: false, open: false, quiet: false }
OptionParser.new do |o|
  o.banner = 'Usage: build.rb [options]'
  o.on('--impact FILE', 'Input impact.json') { |v| options[:impact] = v }
  o.on('--out FILE', 'Output HTML') { |v| options[:out] = v }
  o.on('--root DIR', 'Repository root') { |v| options[:root] = v }
  o.on('--lang LANG', %w[vi ja], 'UI language: vi (default) | ja') { |v| options[:lang] = v }
  o.on('--default-screen ID', 'Screen selected on load') { |v| options[:default_screen] = v }
  o.on('--config FILE', 'Project config (default .claude/code-map.json)') { |v| options[:config] = v }
  o.on('--refresh', 'Run steps 1 and 2 first, then build') { options[:refresh] = true }
  o.on('--open', 'Open the page when it is written') { options[:open] = true }
  o.on('--quiet', 'Suppress the stderr summary') { options[:quiet] = true }
end.parse!

# ---------------------------------------------------------------------------
# Look and density
# ---------------------------------------------------------------------------
# The bundle reads all of this from the dataset, so changing the palette, the
# node sizes, the force strengths or how many files a screen draws is an edit
# here plus a re-run — never a rebuild of assets/app.html.

UI = {
  'colors' => {
    'module' => '#7c6cf5', 'feature' => '#38bdf8', 'route' => '#fb923c', 'controller' => '#34d399',
    'service' => '#c084fc', 'model' => '#f87171', 'view' => '#fbbf24', 'lib' => '#94a3b8'
  },
  'sizes' => { 'centre' => 130, 'peer' => 46, 'module' => 90, 'file' => 22 },
  'labelSize' => 12,
  # Sàn cỡ chữ tính theo toạ độ thế giới: zoom vào thì chữ to theo, không đứng yên.
  'labelMinWorld' => 15,
  'hotspots' => 6,
  'groupGrowth' => 6,   # a feature node grows with the controllers it holds
  # Cây: cột theo độ sâu, dòng theo thứ tự lá. colWidth phải đủ rộng cho nhãn,
  # rowGap đủ thưa để chữ không chồng nhau khi zoom-to-fit.
  'tree' => { 'colWidth' => 380, 'rowGap' => 46, 'groupGap' => 0.6,
              'corner' => 12 },   # bán kính bo góc của đường kẻ vuông góc
  # Screens and modules are drawn as cards: a known width means a label can never
  # land on top of its neighbour's.
  'box' => { 'width' => 190, 'moduleWidth' => 150, 'height' => 40, 'shortHeight' => 30,
             'titleSize' => 12, 'subSize' => 9, 'subChars' => 30 },
  'homeLabelChars' => 24,   # names on the overview are trimmed to this
  # The overview is a laid-out web: modules on an outer circle, their screens
  # fanned on rings around each one.
  'grid' => { 'columns' => 4 },   # cards per row inside a module block   # below this only module names are drawn
  'maxFiles' => 22,   # code files drawn per screen; hubs are never drawn
  'homePeers' => 3,   # sibling links drawn between home screens
  'homeMinScore' => 0.2,
  'maxViews' => 6,
  'maxRoutes' => 5
}.freeze

# Which controllers count as "the main areas" on the landing view. A controller
# is the unit a person recognises as a feature — Tồn kho, Kiểm kê, Đơn bán — and
# its actions are the screens inside it. The API and the admin console are
# reachable by drilling in, not by browsing.
ROOT   = File.expand_path(options[:root] || File.join(HERE, '../../../..'))
IMPACT = File.expand_path(options[:impact] || File.join(ROOT, '.ai/code-map/analysis/impact.json'))
OUT    = File.expand_path(options[:out] || File.join(ROOT, '.ai/code-map/doc/index.html'))

# ---------------------------------------------------------------------------
# Per-project config
# ---------------------------------------------------------------------------
# The scripts are generic Rails. Everything that knows this repo is zaico — the
# Vietnamese dictionary, the feature areas, which controllers are back-office
# noise, the screen to open on — lives in `.claude/code-map.json`, so another
# project reuses these skills by writing that one file and nothing else. With no
# file at all the map still builds: screens keep their controller#action names
# and every controller lands in one unnamed area.
CONFIG_FILE = File.expand_path(options[:config] || File.join(ROOT, '.claude/code-map.json'))
CONFIG      = File.exist?(CONFIG_FILE) ? JSON.parse(File.read(CONFIG_FILE)) : {}
LABELS      = CONFIG['labels'] || {}
AREAS_EN    = (CONFIG['areas_en'] || {}).freeze
OTHER_AREA  = CONFIG['other_area'] || 'Khác'

# Packwerk packs are a deployment boundary, not a mental one: 152 of zaico's 223
# controllers live in "(root)", which tells a reader nothing. Feature areas are
# what people actually call the parts of the product. First match wins, so keep
# the narrow patterns above the broad ones; a growing "other" area is the signal
# to add a row to the config.
FEATURES = (CONFIG['features'] || []).map { |f| [f['name'], Regexp.new(f['match'])] }.freeze

def feature_of(controller)
  last = controller.split('/').last
  FEATURES.each { |name, re| return name if last =~ re }
  first = controller.split('/').first
  FEATURES.each { |name, re| return name if first =~ re }
  OTHER_AREA
end

# Back-office and API controllers are real screens but not the product, and on
# the overview they bury it. Which ones those are is a per-project question.
HIDDEN_CONTROLLERS = (CONFIG['hidden_controllers'] || []).map { |p| Regexp.new(p) }.freeze
HOME_CONTROLLER = lambda { |screen| HIDDEN_CONTROLLERS.none? { |re| screen['controller'] =~ re } }

# Steps 1 and 2 own their own refresh rules; this only spares the caller from
# remembering the order and from digging meta.commit out of scan.json.
# A first run on a fresh clone has no .ai/ at all (it is gitignored), so refresh
# implicitly rather than sending the user off to find another skill.
if options[:refresh] || !File.exist?(File.expand_path(options[:impact] || File.join(ROOT, '.ai/code-map/analysis/impact.json')))
  skills = File.join(ROOT, '.claude/skills')
  scan = File.join(ROOT, '.ai/code-map/raw/scan.json')
  since = if File.exist?(scan)
            commit = JSON.parse(File.read(scan)).dig('meta', 'commit')
            commit if commit && system('git', '-C', ROOT, 'cat-file', '-e', "#{commit}^{commit}", out: File::NULL, err: File::NULL)
          end
  scan_cmd = [File.join(skills, 'code-map/scripts/scan-code-map.sh'), '--root', ROOT]
  scan_cmd += ['--since', since] if since
  # Both sibling scripts resolve their default paths against the working
  # directory, so they have to run from the repository root.
  Dir.chdir(ROOT) do
    system(*scan_cmd, exception: true)
    system(File.join(skills, 'impact-analysis/scripts/analyze-impact.sh'), exception: true)
  end
end

abort "[doc] missing #{IMPACT} - run the impact-analysis skill first" unless File.exist?(IMPACT)

impact = JSON.parse(File.read(IMPACT))
meta   = impact['meta'] || {}
abort "[doc] impact.json schema_version=#{meta['schema_version'].inspect}, expected #{EXPECTED_IMPACT_SCHEMA}" if meta['schema_version'] != EXPECTED_IMPACT_SCHEMA

screens = impact['screens']
abort '[doc] impact.json has no related_screens - re-run step 2' unless screens.first&.key?('related_screens')

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
# `controller#action` is precise and unreadable. The rule is deliberately
# visible: {verb} {noun}, with `overrides` winning outright for names everyone
# already says. A segment with no entry keeps its original name - inventing a
# translation would be worse than showing the truth.
#
# Edit the tables below and re-run; nothing else has to change.


# Each table is optional: a project with no dictionary at all still builds a map,
# it just shows every screen as controller#action.
OVERRIDE = LABELS['overrides']  || {}
NS       = LABELS['namespaces'] || {}
ACTION   = LABELS['actions']    || {}
NOUN     = LABELS['nouns']      || {}
VERB     = LABELS['verbs']      || {}
TERM     = LABELS['terms']      || {}
VERSION_SEGMENT = /\A(v\d+|.*_v\d+)\z/.freeze

def humanize(token)
  token.tr('_', ' ')
end

def upcase_first(str)
  str.empty? ? str : str[0].upcase + str[1..-1].to_s
end

# The action vocabulary has a long tail: 427 of the 477 distinct actions appear
# on one or two screens. Listing them all would be unmaintainable, so an action
# with no entry of its own is composed from its leading verb plus the terms
# that follow it.
def translate_action(action)
  known = ACTION[action]
  return [known, true] if known

  parts = action.split('_')
  verb = VERB[parts.first]
  return [upcase_first(humanize(action)), false] if verb.nil?

  rest = parts.drop(1)
  return [verb, true] if rest.empty?

  words = rest.map { |w| TERM[w] }
  return [upcase_first(humanize(action)), false] if words.any?(&:nil?)

  [upcase_first("#{verb} #{words.join(' ')}"), true]
end

# A composed action often already names the thing it acts on:
# download_account_statement becomes "Tải xuống tài khoản sao kê" and the
# controller noun is "sao kê" again. Drop whatever the tail of the verb and the
# head of the noun have in common, word by word.
def join_phrase(verb, noun)
  a = verb.split(' ')
  b = noun.split(' ')
  overlap = [a.length, b.length].min
  overlap -= 1 while overlap.positive? &&
                     a.last(overlap).map(&:downcase) != b.first(overlap).map(&:downcase)
  (a + b.drop(overlap)).join(' ')
end

# The controller's own name, without the action verb in front of it.
def controller_label(screen)
  segments = screen['controller'].split('/')
  i = segments.length - 1
  i -= 1 while i.positive? && segments[i] =~ VERSION_SEGMENT && NOUN[segments[i]].nil?
  noun = NOUN[segments[i]] || humanize(segments[i])
  qualifiers = segments.each_with_index.reject { |_, j| j == i }.map { |seg, _| NS[seg] || humanize(seg) }
  [upcase_first(noun), qualifiers.empty? ? nil : qualifiers.join(' · ')]
end

def label_for(screen)
  segments = screen['controller'].split('/')
  if OVERRIDE.key?(screen['id'])
    return [OVERRIDE[screen['id']], nil, true]
  end

  i = segments.length - 1
  i -= 1 while i.positive? && segments[i] =~ VERSION_SEGMENT && NOUN[segments[i]].nil?
  noun = NOUN[segments[i]]
  noun_known = !noun.nil?
  noun ||= humanize(segments[i])

  verb, verb_known = translate_action(screen['action'])
  qualifiers = segments.each_with_index.reject { |_, j| j == i }.map { |seg, _| NS[seg] || humanize(seg) }

  [upcase_first(join_phrase(verb, noun)),
   qualifiers.empty? ? nil : qualifiers.join(' - '),
   noun_known && verb_known]
end

exact = 0
named = {}
screens.each do |s|
  name, qualifier, ok = label_for(s)
  exact += 1 if ok
  named[s['id']] = [name, qualifier, ok ? 1 : 0]
end

# ---------------------------------------------------------------------------
# Payload - file paths are interned so they are stored once, not once per screen
# ---------------------------------------------------------------------------

facts = {}
(impact['shared_files'] || []).each { |f| facts[f['file']] = [f['bucket'], f['screen_count'], f['hub'] ? 1 : 0] }
(impact['unreached_files'] || []).each { |f| facts[f['file']] ||= [f['bucket'], 0, 0] }

# Rails ghi đoạn tuỳ chọn trong ngoặc: `/(/companies/:company_id)/items`.
# Bỏ ngoặc xong còn lại hai dấu gạch dính nhau, nên phải dọn luôn —
# nếu không, mọi đường dẫn đa tenant hiện ra thành `//items`.
def clean_path(path)
  return path if path.nil?

  cleaned = path.gsub(/\(\/[^)]*\)/, '').gsub(/\/{2,}/, '/')
  cleaned.empty? ? '/' : cleaned
end

def bucket_for(path, given)
  return given if given

  case path
  when %r{/controllers/} then 'controllers'
  when %r{/models/}      then 'models'
  when %r{/services/}    then 'services'
  when %r{/views/}       then 'views'
  when %r{/jobs/}        then 'jobs'
  when %r{/mailers/}     then 'mailers'
  when %r{/frontend/}    then 'frontend_pages'
  else 'lib'
  end
end

# Tên tiếng Việt cho một file trong cây gọi, để người đọc hiểu vai trò trước khi
# nhìn tới tên file. Tiếng Việt đặt bổ ngữ phía sau (inventory import → nhập tồn
# kho), nên khi ghép từng từ thì đảo thứ tự. Từ nào không có trong từ điển thì
# giữ nguyên tên gốc — đoán bừa còn tệ hơn để tên thật.
def vi_words(str)
  words = str.split('_').reject { |w| w.empty? || w =~ /\Av\d+\z/ }
  return nil if words.empty?
  vi = words.map { |w| TERM[w] || NOUN[w] }
  return nil if vi.any?(&:nil?)
  vi.reverse.join(' ')
end

GENERIC_BASENAMES = %w[service client util utils base version response request parser adapter api constants config error errors helper].freeze

def vi_noun(token)
  NOUN[token] || NOUN["#{token}s"] || NOUN[token.sub(/y\z/, 'ies')] || vi_words(token)
end

def file_label(path, bucket)
  partial = File.basename(path).start_with?('_')
  base = File.basename(path).sub(/\..*\z/, '').sub(/\A_/, '')
  dirs = path.sub(%r{\A(?:packs/[^/]+/)?app/[^/]+/}, '').split('/')[0...-1]
  label =
    case bucket
    when 'models'
      vi_noun(base)
    when 'services', 'jobs', 'lib', 'mailers'
      # service.rb / client.rb / version.rb nói lên thư mục chứ không nói lên
      # chính nó: gọi theo thư mục, kèm vai trò.
      if GENERIC_BASENAMES.include?(base) && !dirs.empty?
        where = dirs.reverse.map { |d| vi_noun(d) || d.tr('_', ' ').upcase }.first
        role = TERM[base] || base
        return [upcase_first("#{role} · #{where}"), vi_noun(dirs.last) ? 1 : 0]
      end
      core = base.sub(/_(service|util|utils|job|builder|processor|mailer)\z/, '')
      tokens = core.split('_')
      verb = VERB[tokens.first]
      owner = dirs.reverse.map { |d| vi_noun(d) }.compact.first
      rest = tokens.size > 1 ? vi_words(tokens.drop(1).join('_')) : nil
      own_words = vi_words(core)
      if verb && (tokens.size == 1 || rest)
        [verb, rest, owner].compact.join(' ')
      elsif own_words
        [own_words, owner].compact.uniq.join(' · ')
      end
    when 'views', 'frontend_pages'
      owner = dirs.reverse.map { |d| vi_noun(d) }.compact.first
      return [upcase_first(['Dữ liệu JSON', translate_action(base).first.downcase, owner].compact.join(' · ')), 1] if path.end_with?('.jbuilder')
      if partial
        part = vi_words(base)
        part ? ["Khối #{part}", owner].compact.join(' · ') : nil
      else
        act, known = translate_action(base)
        known ? ["Trang #{act.downcase}", owner].compact.join(' · ') : nil
      end
    when 'controllers'
      vi_words(base.sub(/_controller\z/, ''))
    end
  label ? [upcase_first(label), 1] : [humanize(base), 0]
end

index = {}
files = []
fidx = lambda do |path|
  next -1 if path.nil? || path.empty?

  index[path] ||= begin
    f = facts[path] || [nil, 0, 0]
    b = bucket_for(path, f[0])
    files << [path, b, f[1], f[2], *file_label(path, b)]
    files.size - 1
  end
end

at = {}
screens.each_with_index { |s, i| at[s['id']] = i }

def view_payload(nodes, fidx)
  nodes.map do |n|
    i = fidx.call(n['file'])
    kids = view_payload(n['renders'] || [], fidx)
    kids.empty? ? [i] : [i, kids]
  end
end

def flow_payload(nodes, fidx)
  nodes.map do |n|
    i = fidx.call(n['file'])
    kids = flow_payload(n['calls'] || [], fidx)
    kids.empty? ? [i] : [i, kids]
  end
end

rows = screens.map do |s|
  name = named[s['id']]
  {
    'id' => s['id'], 'lb' => name[0], 'lq' => name[1], 'lx' => name[2],
    'cf' => fidx.call(s['controller_file']),
    'vue' => fidx.call(s['vue_page']),
    'ad' => s['action_defined'] ? 1 : 0,
    'nf' => s['code_file_count'] || 0,
    'ro' => (s['routes'] || []).map { |r| [r['method'], clean_path(r['path'])] },
    'vw' => (s['views'] || []).map { |v| fidx.call(v) }.reject(&:negative?),
    'cfs' => (s['code_files'] || []).map { |c| [fidx.call(c['file']), c['depth'] || 0, c['hub'] ? 1 : 0] }
                                    .reject { |e| e[0].negative? },
    'rel' => (s['related_screens'] || []).map { |r| at[r['id']] ? [at[r['id']], r['score']] : nil }.compact,
    # Cây gọi của riêng action này: [file, [con...]] lồng nhau.
    'fl' => flow_payload(s['flow'] || [], fidx),
    # Tiêu đề tiếng Nhật trên màn hình thật (在庫登録)
    'tj' => s['title_ja'],
    # View của action kèm partial nó render: [file, [con...]] lồng nhau.
    'vt' => view_payload(s['view_tree'] || [], fidx),
    # Trang Vue gọi API nào: [method, path, màn đích, hàm API, [file đi qua]]
    'ac' => (s['api_calls'] || []).map { |c| [c['method'], c['path'], c['screen'] && at[c['screen']] ? at[c['screen']] : -1, c['fn'], (c['via'] || []).map { |v| fidx.call(v) }] },
    # API action này được trang nào gọi: [màn trang, hàm API, [file đi qua]]
    'cb' => (s['called_from'] || []).map { |c| at[c['screen']] ? [at[c['screen']], c['fn'], (c['via'] || []).map { |v| fidx.call(v) }] : nil }.compact.uniq { |x| [x[0], x[1]] }
  }
end

STRINGS = {
  'vi' => {
    'title' => 'Bản đồ ảnh hưởng',
    'sub' => 'Cây phụ thuộc: Module → Màn hình → Controller → Model → Vue',
    'langBtn' => 'VI', 'langAlt' => 'EN',
    'tabs' => { 'spider' => 'Sơ đồ cây', 'modules' => 'Danh sách module' },
    'iUnresolved' => 'Đường dẫn trỏ tới controller không tồn tại',
    'iHubs' => 'File hub — nơi dừng việc lần theo tham chiếu',
    'iUnreached' => 'File không màn hình nào chạm tới',
    'iUnreachedNote' => 'Đây là ứng viên, không phải bằng chứng code chết: việc lần theo dừng ở hub và ở độ sâu tối đa.',
    'fanIn' => 'được gọi {n} lần',
    'noRoute' => 'không có đường dẫn riêng · no URL of its own',
    'search' => 'Tìm màn hình, đường dẫn, file code…',
    'filter' => 'Lọc theo module', 'showAll' => 'Hiện tất cả',
    'panel' => 'Bảng điều khiển',
    'filterKinds' => 'Lọc loại node', 'hotspots' => 'File tác động lớn',
    'inGroup' => 'Controller trong nhóm',
    'kindsShort' => { 'module' => 'Nhóm', 'feature' => 'Màn hình', 'route' => 'Route', 'controller' => 'Ctrl',
                      'service' => 'Srv', 'model' => 'Model', 'view' => 'Vue', 'lib' => 'Lib' },
    'tip' => 'Gốc cây nằm bên trái, mỗi nhánh là một loại node, lá là file cụ thể. Bấm một node để soi liên kết, bấm node nhóm để mở khu vực. Lăn chuột thu phóng, kéo để di chuyển.',
    'legend' => 'Chú giải', 'sideTitle' => 'Chi tiết node & liên kết',
    'searchResults' => 'Kết quả tìm', 'noResults' => 'Không có kết quả',
    'linked' => 'Liên kết', 'usedBy' => 'Màn hình dùng file này',
    'related' => 'Màn hình liên quan', 'noRelated' => 'Không có màn hình liên quan',
    'screensIn' => 'Màn hình bên trong',
    'home' => 'Trang chính', 'mainScreens' => 'Các trang chính',
    'homeLede' => 'Mỗi node lớn là một nhóm tính năng. Bấm vào để mở các controller bên trong.',
    'kinds' => { 'module' => 'Nhóm tính năng · Module', 'feature' => 'Màn hình · Screen',
                 'route' => 'Đường dẫn · Route', 'controller' => 'Controller · Bộ điều khiển',
                 'service' => 'Service · Nghiệp vụ', 'model' => 'Model · Bảng dữ liệu',
                 'view' => 'Giao diện · View / Vue', 'lib' => 'Thư viện · Lib / Job' },
    # Nhãn trên node nhánh của sơ đồ cây — song ngữ, vì tên kỹ thuật là tiếng Anh
    # còn người đọc bản đồ thì đọc tiếng Việt.
    # Nhánh của sơ đồ cây được đặt tên theo CÂU HỎI người đọc mang tới bản đồ,
    # không theo loại node. "Model" không trả lời được câu nào cả.
    # Nhãn nhánh nằm ngay cạnh node nên phải ngắn; câu hỏi đầy đủ đã có trong
    # phần "Đọc bản đồ này thế nào" ở bảng bên phải.
    'roles' => {
      'area'    => 'Thuộc nhóm',
      'areas'   => 'Nhóm tính năng',
      'entry'   => 'Đường dẫn',
      'runs'    => 'Code chạy',
      'data'    => 'Dữ liệu & nghiệp vụ',
      'screens' => 'Màn hình',
      'ctrls'   => 'Controller',
      'peers'   => 'Controller liên quan',
      'affects' => 'Ảnh hưởng theo'
    },
    'rolesEn' => {
      'area'    => 'module',
      'areas'   => 'feature areas',
      'entry'   => 'routes',
      'runs'    => 'code that runs',
      'data'    => 'data & logic',
      'screens' => 'screens',
      'ctrls'   => 'controllers',
      'peers'   => 'related controllers',
      'affects' => 'also affected'
    },
    'kindsBi' => { 'module' => 'Nhóm · Module', 'feature' => 'Màn hình · Screen',
                   'route' => 'Đường dẫn · Route', 'controller' => 'Controller',
                   'service' => 'Nghiệp vụ · Service', 'model' => 'Dữ liệu · Model',
                   'view' => 'Giao diện · View', 'lib' => 'Thư viện · Lib' },
    'modulesLede' => 'Mỗi module là một Packwerk pack. Số liệu lấy từ impact.json.',
    'sharedLede' => 'File được nhiều màn hình chạm tới nhất. Hub bị loại vì gần như màn nào cũng chạm.',
    'colScreens' => 'Màn hình', 'colFile' => 'File', 'colKind' => 'Loại',
    'mScreens' => 'màn hình', 'mFiles' => 'file',
    'tagUndefined' => 'action không định nghĩa', 'tagHub' => 'hub', 'tagRaw' => 'tên tự sinh',
    'reaches' => '{n} file được chạm', 'nScreens' => '{n} màn hình',
    'hubNote' => 'File hub — không tham gia tính độ liên quan, việc lần theo tham chiếu dừng ở đây.',
    'howTitle' => 'Đọc bản đồ này thế nào',
    'how' => [
      'Màn hình = controller#action. Nhiều đường dẫn trỏ cùng một action thì gộp làm một.',
      'Liên quan = mức độ cùng chạm tới file không phải hub (cosine). Hub không tính.',
      'Tham chiếu lấy bằng phân tích tĩnh theo tên: dispatch động bỏ sót, tên chung chung bắt thừa.',
      'Đường dẫn đọc từ DSL chứ không nạp Rails.'
    ],
    'statLabels' => ['màn hình', 'file được chạm', 'file/màn hình', 'không ai chạm', 'controller không tồn tại']
  },
  'ja' => {
    'title' => 'Impact Map',
    'sub' => 'Dependency tree: Module -> Screen -> Controller -> Model -> Vue',
    'langBtn' => 'EN', 'langAlt' => 'VI',
    'tabs' => { 'spider' => 'Tree', 'modules' => 'Modules' },
    'iUnresolved' => 'Routes pointing at a controller that does not exist',
    'iHubs' => 'Hub files — where the reference walk stops',
    'iUnreached' => 'Files no screen reaches',
    'iUnreachedNote' => 'Candidates, not proof of dead code: the walk stops at hubs and at max depth.',
    'fanIn' => 'referenced {n} times',
    'noRoute' => 'no URL of its own',
    'search' => 'Search screens, routes, files...',
    'filter' => 'Filter by module', 'showAll' => 'Show all',
    'panel' => 'Panel',
    'filterKinds' => 'Node kinds', 'hotspots' => 'High-impact files',
    'inGroup' => 'Controllers in module',
    'kindsShort' => { 'module' => 'Module', 'feature' => 'Screen', 'route' => 'Route', 'controller' => 'Ctrl',
                      'service' => 'Srv', 'model' => 'Model', 'view' => 'Vue', 'lib' => 'Lib' },
    'tip' => 'Root on the left, one branch per node kind, leaves are files. Click a node to highlight its network. Scroll to zoom, drag to pan.',
    'legend' => 'Legend', 'sideTitle' => 'Node & links',
    'searchResults' => 'Results', 'noResults' => 'No results',
    'linked' => 'Linked', 'usedBy' => 'Screens using this file',
    'related' => 'Related screens', 'noRelated' => 'No related screens',
    'screensIn' => 'Screens in module',
    'home' => 'Home', 'mainScreens' => 'Main screens',
    'homeLede' => 'Each node is a main screen. Click one to see its related screens and the code it reaches.',
    'kinds' => { 'module' => 'Module', 'feature' => 'Screen', 'route' => 'Route', 'controller' => 'Controller',
                 'service' => 'Service', 'model' => 'Model', 'view' => 'View / Vue', 'lib' => 'Lib / Job' },
    'roles' => {
      'area' => 'Module', 'areas' => 'Feature areas', 'entry' => 'Routes',
      'runs' => 'Code that runs', 'data' => 'Data & logic', 'screens' => 'Screens',
      'ctrls' => 'Controllers', 'peers' => 'Related controllers', 'affects' => 'Also affected'
    },
    'kindsBi' => { 'module' => 'Module', 'feature' => 'Screen', 'route' => 'Route', 'controller' => 'Controller',
                   'service' => 'Service', 'model' => 'Model', 'view' => 'View', 'lib' => 'Lib' },
    'modulesLede' => 'Each module is a Packwerk pack. Numbers come from impact.json.',
    'sharedLede' => 'Files reached by the most screens. Hubs are excluded.',
    'colScreens' => 'Screens', 'colFile' => 'File', 'colKind' => 'Kind',
    'mScreens' => 'screens', 'mFiles' => 'files',
    'tagUndefined' => 'action undefined', 'tagHub' => 'hub', 'tagRaw' => 'generated name',
    'reaches' => 'reaches {n} files', 'nScreens' => '{n} screens',
    'hubNote' => 'Hub file - excluded from relatedness; the reference walk stops here.',
    'howTitle' => 'How to read this map',
    'how' => [
      'A screen is controller#action; routes sharing an action are collapsed.',
      'Relatedness is shared non-hub files (cosine). Hubs do not contribute.',
      'References are name-based static analysis: dynamic dispatch is missed.',
      'Routes are parsed from the DSL, not loaded from Rails.'
    ],
    'statLabels' => ['screens', 'files reached', 'files/screen', 'unreached', 'missing controllers']
  }
}.freeze

# Both tables ship in the page. The Vietnamese names are the point of the map,
# but "Tồn kho" is a guess a reader cannot check, so the EN button swaps every
# label back to what the code actually calls it — controller#action, the class,
# the pack. Two tables in the payload cost a few KB; a second build does not.
prep = lambda do |t|
  out = t.dup
  out['stats'] = %w[screens files_reached avg_files_per_screen files_unreached unresolved_controllers]
                 .each_with_index.map { |k, i| [k, out['statLabels'][i], i >= 3] }
  out.delete('statLabels')
  out
end
strings = prep.call(STRINGS[options[:lang]])
strings_alt = prep.call(STRINGS[options[:lang] == 'vi' ? 'ja' : 'vi'])

# nil means "open on the overview"; --default-screen pins a single screen instead
default_screen = options[:default_screen] || CONFIG['default_screen']

# Controller rollup: label, its screens, and how strongly it relates to other
# controllers. The score between two controllers is the strongest link between
# any of their screens — a weaker rule would bury real coupling in averages.
# Every controller gets a function page — API controllers too, since a Vue page's
# button lands there — but only the product ones are listed on the overview.
controller_of = {}
screens.each_with_index do |s, i|
  (controller_of[s['controller']] ||= []) << i
end

ctrl_index = {}
controller_of.keys.sort.each_with_index { |c, i| ctrl_index[c] = i }

pairs = Hash.new(0.0)
controller_of.each do |ctrl, idxs|
  a = ctrl_index[ctrl]
  idxs.each do |i|
    (screens[i]['related_screens'] || []).each do |r|
      peer = screens[at[r['id']]] if at[r['id']]
      next unless peer

      b = ctrl_index[peer['controller']]
      next if b.nil? || a == b

      key = a < b ? [a, b] : [b, a]
      pairs[key] = r['score'] if r['score'] > pairs[key]
    end
  end
end

ctrl_rel = Hash.new { |h, k| h[k] = [] }
pairs.each do |(a, b), score|
  ctrl_rel[a] << [b, score]
  ctrl_rel[b] << [a, score]
end

# Chức năng theo cụm tên, không theo controller: Kiểm kê là `stocktakings`,
# `stocktakings/inventories`, `stocktakings/stocktaking_item_imports/preview`,
# `api/stocktakings/video_analysis`… — bỏ namespace kỹ thuật (api, v1, v2) phía trước
# rồi lấy đoạn đầu tiên làm khoá.
TECH_NS = %w[api v1 v2 v3 web internal].freeze
def feature_key(ctrl)
  segs = ctrl.split('/')
  segs.shift while segs.size > 1 && (TECH_NS.include?(segs.first) || segs.first =~ /\Av\d+\z/)
  segs.first.sub(/_v\d+\z/, '')
end

controllers = controller_of.keys.sort.map do |ctrl|
  idxs = controller_of[ctrl]
  sample = screens[idxs.first]
  noun, qualifier = controller_label(sample)
  {
    'c' => ctrl,
    'f' => feature_of(ctrl),
    'lb' => noun,
    'lq' => qualifier,
    'sc' => idxs,
    'nf' => idxs.map { |i| screens[i]['code_file_count'] || 0 }.max,
    'rel' => ctrl_rel[ctrl_index[ctrl]].sort_by { |r| -r[1] }.first(6),
    'hid' => HOME_CONTROLLER.call(sample) ? 0 : 1,
    'fk' => feature_key(ctrl),
    # Màn cũ: đã có controller cùng tên đuôi _v2 thay thế
    'old' => controller_of.key?("#{ctrl}_v2") ? 1 : 0
  }
end

payload = {
  'meta' => meta.merge('generated_at' => Time.now.utc.iso8601, 'lang' => options[:lang], 'default_screen' => default_screen,
                       'staging_url' => CONFIG['staging_url']),
  'stats' => impact['stats'] || {},
  'strings' => strings,
  'stringsAlt' => strings_alt,
  'areasEn' => AREAS_EN,
  'ui' => UI,
  'files' => files,
  'screens' => rows,
  'controllers' => controllers,
}

# Step 2's findings do not go into the page: three screens of file lists that
# nobody browses only pushed the map itself further away. They belong in
# summary.md, which is where they are read — in a terminal, in a review.
issues = {
  'unresolved' => impact['unresolved_controllers'] || [],
  'hubs' => (impact['hubs'] || []).map { |h| [h['file'], h['raw_fan_in']] },
  'unreached' => (impact['unreached_files'] || []).map { |u| [u['file'], bucket_for(u['file'], u['bucket']), u['pack']] }
}

# `</` inside the JSON would close the inline <script> early.
json = JSON.generate(payload).gsub('</', '<\\/')
bundle = File.read(File.join(HERE, '../assets/app.html'))
abort '[doc] assets/app.html has no /*__DATA__*/ placeholder' unless bundle.include?('/*__DATA__*/')

FileUtils.mkdir_p(File.dirname(OUT))
File.write(OUT, bundle.sub('/*__DATA__*/ null') { json })

# The page is for browsing; this file is for reading in a terminal, quoting in a
# review, and diffing between two scans. It carries the shape of the codebase and
# the findings — not the per-screen file lists, which only a graph makes usable.
def write_summary(path, meta, stats, controllers, screens, issues, strings, shared, files)
  groups = controllers.group_by { |c| c['f'] }.sort_by { |_, v| -v.size }
  lines = []
  lines << "# #{strings['title']}"
  lines << ''
  lines << "Sinh từ commit `#{meta['scan_commit']}` lúc #{meta['generated_at']}."
  lines << "Bản đồ xem được: `.ai/code-map/doc/index.html`."
  lines << ''
  lines << '## Tổng quan'
  lines << ''
  lines << '| Số | Nghĩa |'
  lines << '|---:|---|'
  lines << "| #{stats['routes'] || '-'} | route |"
  lines << "| #{screens.size} | màn hình (controller#action) |"
  lines << "| #{controllers.size} | controller có màn hình |"
  lines << "| #{groups.size} | nhóm tính năng |"
  lines << "| #{stats['files_reached'] || '-'} | file được ít nhất một màn dùng tới |"
  lines << "| #{issues['hubs'].size} | file hub (bị dùng quá rộng, không đi tiếp khi lần vết) |"
  lines << ''
  lines << '## Nhóm tính năng'
  lines << ''
  lines << '| Nhóm | Controller | Màn hình |'
  lines << '|---|---:|---:|'
  groups.each do |name, list|
    lines << "| #{name} | #{list.size} | #{list.sum { |c| c['sc'].size }} |"
  end
  lines << ''
  lines << '## Controller theo từng nhóm'
  lines << ''
  lines << 'Số trong ngoặc là số file mà màn hình nặng nhất của controller đó chạm tới.'
  lines << ''
  groups.each do |name, list|
    lines << "### #{name}"
    lines << ''
    list.sort_by { |c| -c['nf'] }.each do |c|
      label = [c['lb'], c['lq']].compact.reject(&:empty?).join(' · ')
      lines << "**#{label}** — `#{c['c']}`, #{c['sc'].size} màn"
      lines << ''
      c['sc'].map { |i| screens[i] }.sort_by { |sc| -sc['nf'] }.each do |sc|
        route = sc['ro'].first
        where = route ? "`#{route[0]} #{route[1]}`" : '_không có route_'
        vue = sc['vue'] >= 0 ? " · Vue `#{files[sc['vue']][0]}`" : ''
        missing = sc['ad'] == 1 ? '' : ' · **action không tồn tại**'
        lines << "- #{sc['lb']}#{sc['lq'] ? " · #{sc['lq']}" : ''} — #{where} · #{sc['nf']} file#{vue}#{missing}"
      end
      lines << ''
    end
  end
  lines << '## Nhóm nào dùng chung file với nhóm nào'
  lines << ''
  lines << 'Đếm số file non-hub mà hai nhóm cùng chạm tới. Cặp ở đầu bảng là chỗ'
  lines << 'sửa cho nhóm này dễ làm vỡ nhóm kia nhất.'
  lines << ''
  # Counted from each screen's own code_files, not from shared_files[].screens:
  # step 2 truncates that list at 20 entries, which would silently undercount
  # exactly the widest-reaching files.
  area_of = {}
  controllers.each { |c| c['sc'].each { |i| area_of[i] = c['f'] } }
  areas_by_file = Hash.new { |h, k| h[k] = {} }
  screens.each_with_index do |sc, i|
    area = area_of[i]
    next if area.nil?

    sc['cfs'].each { |fi, _depth, hub| areas_by_file[fi][area] = true if hub.zero? }
  end
  pair = Hash.new(0)
  areas_by_file.each_value do |areas|
    areas.keys.sort.combination(2) { |a, b| pair[[a, b]] += 1 }
  end
  lines << '| Nhóm A | Nhóm B | File chung |'
  lines << '|---|---|---:|'
  pair.sort_by { |_, n| -n }.first(20).each { |(a, b), n| lines << "| #{a} | #{b} | #{n} |" }
  lines << ''
  lines << '## File rủi ro nhất khi sửa'
  lines << ''
  lines << 'Xếp theo số màn hình chạm tới. Hub bị loại vì chúng ở khắp nơi nên'
  lines << 'không phân biệt được gì; những file dưới đây mới là chỗ sửa một nơi'
  lines << 'mà ảnh hưởng rộng.'
  lines << ''
  lines << '| File | Số màn chạm tới |'
  lines << '|---|---:|'
  shared.reject { |f| f['hub'] }.sort_by { |f| -f['screen_count'] }.first(25).each do |f|
    lines << "| `#{f['file']}` | #{f['screen_count']} |"
  end
  lines << ''
  lines << '## Màn hình chạm tới nhiều file nhất'
  lines << ''
  lines << '| Màn hình | controller#action | File |'
  lines << '|---|---|---:|'
  screens.sort_by { |s| -(s['nf'] || 0) }.first(20).each do |s|
    label = [s['lb'], s['lq']].compact.reject(&:empty?).join(' · ')
    lines << "| #{label} | `#{s['id']}` | #{s['nf']} |"
  end
  lines << ''
  lines << '## Chỗ cần xem lại'
  lines << ''
  lines << "### Route trỏ vào controller không tồn tại (#{issues['unresolved'].size})"
  lines << ''
  issues['unresolved'].each { |c| lines << "- `#{c}`" }
  lines << ''
  lines << "### File bị dùng chung rộng nhất (#{issues['hubs'].size} hub)"
  lines << ''
  issues['hubs'].sort_by { |_, n| -n }.first(15).each { |f, n| lines << "- `#{f}` — #{n} nơi gọi" }
  lines << ''
  lines << "### File không màn hình nào chạm tới (#{issues['unreached'].size})"
  lines << ''
  lines << 'Đây là ứng viên để rà, không phải bằng chứng code chết: job, rake task,'
  lines << 'console và metaprogramming đều không nằm trong đường đi từ route.'
  lines << ''
  by_dir = issues['unreached'].group_by { |u| u[0].split('/')[0, 3].join('/') }
                              .sort_by { |k, v| [-v.size, k] }
  by_dir.each do |dir, list|
    lines << "**#{dir}/** — #{list.size} file"
    lines << ''
    list.sort_by { |u| u[0] }.each { |u| lines << "- `#{u[0]}`" }
    lines << ''
  end
  lines << '## Dựng lại tài liệu này'
  lines << ''
  lines << '```'
  lines << '/map --refresh      quét lại code rồi dựng (khoảng 10 giây)'
  lines << '/map                chỉ dựng lại từ dữ liệu cũ (khoảng 1 giây)'
  lines << '```'
  lines << ''
  lines << 'Thay đổi chưa commit sẽ không thấy: bước quét so với commit ghi trong meta.'
  lines << ''
  File.write(path, lines.join("\n") + "\n")
end

summary = File.join(File.dirname(OUT), 'summary.md')
write_summary(summary, payload['meta'], payload['stats'], controllers, rows, issues, strings, impact['shared_files'] || [], files)

system('open', OUT) if options[:open]

unless options[:quiet]
  rel = OUT.sub("#{Dir.pwd}/", '')
  warn "[doc] page -> #{rel}  (#{(File.size(OUT) / 1024.0 / 1024).round(2)} MB)"
  warn "[doc] scan_commit=#{meta['scan_commit']} screens=#{rows.size} files=#{files.size} lang=#{options[:lang]}"
  groups = controllers.group_by { |c| c['f'] }
  warn "[doc] areas: #{groups.size} features, #{controllers.size} controllers, #{controllers.sum { |c| c['sc'].size }} screens"
  warn "[doc] largest: #{groups.sort_by { |_, v| -v.size }.first(4).map { |k, v| "#{k}(#{v.size})" }.join(' ')}"
  warn "[doc] names: #{exact}/#{rows.size} from the dictionary (#{(exact * 100.0 / rows.size).round}%)"
  warn "[doc] summary -> #{summary.sub("#{Dir.pwd}/", '')}"
end
