#!/usr/bin/env ruby
# frozen_string_literal: true

# Impact analyser (KR1 step 2: compare and correlate).
#
# Input:  .ai/code-map/raw/scan.json      (produced by the code-map skill)
# Output: .ai/code-map/analysis/impact.json
#
# Turns the raw structural facts into the two answers KR1 asks for:
#   1. which code files each screen (controller#action) actually reaches
#   2. which files are shared across many screens
#
# It reads NO source files — everything comes from scan.json. That is what
# makes it cheap enough to regenerate in full every time.
#
# Ruby stdlib only, >= 2.6.

require 'json'
require 'optparse'
require 'set'
require 'time'
require 'fileutils'

SCHEMA_VERSION = 1
EXPECTED_SCAN_SCHEMA = 1

options = {
  :scan          => nil,
  :out           => nil,
  :max_depth     => 3,
  :hub_threshold => 40,
  :quiet         => false
}

OptionParser.new do |o|
  o.banner = 'Usage: analyze_code_map.rb [options]'
  o.on('--scan FILE', 'Input scan.json (default: .ai/code-map/raw/scan.json)') { |v| options[:scan] = v }
  o.on('--out FILE', 'Output impact.json (default: .ai/code-map/analysis/impact.json)') { |v| options[:out] = v }
  o.on('--max-depth N', Integer, 'Reference hops to follow from a controller (default: 3)') { |v| options[:max_depth] = v }
  o.on('--hub-threshold N', Integer, 'Fan-in above which a file is a hub and is not traversed through (default: 40)') { |v| options[:hub_threshold] = v }
  o.on('--quiet', 'Suppress the stderr summary') { options[:quiet] = true }
  o.on('-h', '--help') { puts o; exit 0 }
end.parse!

SCAN = File.expand_path(options[:scan] || '.ai/code-map/raw/scan.json')
OUT  = File.expand_path(options[:out]  || '.ai/code-map/analysis/impact.json')

unless File.exist?(SCAN)
  abort("scan not found: #{SCAN}\nRun the code-map skill first.")
end

scan = JSON.parse(File.read(SCAN))
scan_meta = scan['meta'] || {}

if scan_meta['schema_version'] != EXPECTED_SCAN_SCHEMA
  abort("scan.json schema_version=#{scan_meta['schema_version'].inspect}, expected #{EXPECTED_SCAN_SCHEMA}. Re-run a full code-map scan.")
end

RUBY_BUCKETS = %w[controllers models services jobs mailers lib].freeze

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

entry_by_file  = {}
bucket_by_file = {}
RUBY_BUCKETS.each do |bucket|
  (scan[bucket] || []).each do |e|
    entry_by_file[e['file']] = e
    bucket_by_file[e['file']] = bucket
  end
end

controller_file_by_class = {}
controller_file_by_path  = {}
(scan['controllers'] || []).each do |c|
  controller_file_by_class[c['class']] = c['file'] if c['class']
  # Path-based fallback. Custom inflections (PDFTemplatesController for
  # pdf_templates) mean the camelised class name from a route does not always
  # match, but the file path convention still holds.
  m = c['file'].match(%r{app/controllers/(.+)_controller\.rb\z})
  controller_file_by_path[m[1]] = c['file'] if m
end

# Deliberately no fuzzy suffix matching: `orders` would match both
# sales_orders/orders and purchase_orders/orders, and guessing wrong would
# silently attribute files to the wrong screen. Unresolved is reported instead.
def resolve_controller_file(route, by_class, by_path)
  by_class[route['controller_class']] || by_path[route['controller']]
end

# Raw fan-in: how many scanned files name this file. Used to spot hubs.
fan_in = Hash.new(0)
RUBY_BUCKETS.each do |bucket|
  (scan[bucket] || []).each do |e|
    (e['references'] || []).each { |ref| fan_in[ref] += 1 }
  end
end

hub_files = Set.new(fan_in.select { |_f, n| n >= options[:hub_threshold] }.keys)

# Vue pages, keyed by the convention app.ts uses: the layout sets
# <body data-component="<controller path>/<action>"> and app.ts loads
# pages/<that>.vue. Step 1 records the key, so the join here is exact.
vue_page_by_key = {}
(scan['frontend_pages'] || []).each do |f|
  vue_page_by_key[f['page_key']] = f['file'] if f['page_key']
end

# Views, grouped by their controller hint so a screen can claim its templates.
views_by_controller = Hash.new { |h, k| h[k] = [] }
view_by_file = {}
(scan['views'] || []).each do |v|
  view_by_file[v['file']] = v
  views_by_controller[v['controller_hint']] << v if v['controller_hint']
end

# dir -> template basename -> files, so partial lookup is a hash hit rather than
# a scan over every view on every screen.
view_lookup = Hash.new { |h, k| h[k] = Hash.new { |h2, k2| h2[k2] = [] } }
(scan['views'] || []).each do |v|
  next unless v['controller_hint']
  name = File.basename(v['file']).sub(/\..*\z/, '')
  view_lookup[v['controller_hint']][name.sub(/\A_/, '')] << v['file']
end

# `render 'shared/foo'` -> the partial file, so a screen also claims its partials.
def resolve_partial(target, controller_hint, view_lookup)
  if target.include?('/')
    dir  = File.dirname(target)
    base = File.basename(target)
  else
    dir  = controller_hint
    base = target
  end
  return [] if dir.nil?
  return [] unless view_lookup.key?(dir)
  view_lookup[dir][base]
end

def collect_views(controller_hint, action, views_by_controller, view_by_file, view_lookup)
  direct = views_by_controller[controller_hint].select { |v| v['action_hint'] == action }
  seen = Set.new(direct.map { |v| v['file'] })
  queue = direct.dup

  until queue.empty?
    v = queue.shift
    (v['renders'] || []).each do |target|
      resolve_partial(target, controller_hint, view_lookup).each do |file|
        next if seen.include?(file)
        seen << file
        queue << view_by_file[file] if view_by_file[file]
      end
    end
  end
  seen.to_a.sort
end

# Same walk, kept as a tree: each view with the partials it renders under it.
def view_tree(controller_hint, action, views_by_controller, view_by_file, view_lookup)
  seen = Set.new
  build = lambda do |v, depth|
    kids = []
    if depth < 4
      (v['renders'] || []).each do |target|
        resolve_partial(target, controller_hint, view_lookup).each do |file|
          next if seen.include?(file)
          seen << file
          child = view_by_file[file]
          node = { 'file' => file }
          sub = child ? build.call(child, depth + 1) : []
          node['renders'] = sub unless sub.empty?
          kids << node
        end
      end
    end
    kids
  end
  views_by_controller[controller_hint].select { |v| v['action_hint'] == action }.map do |v|
    seen << v['file']
    node = { 'file' => v['file'] }
    sub = build.call(v, 1)
    node['renders'] = sub unless sub.empty?
    node
  end
end

# ---------------------------------------------------------------------------
# Reachability from one controller file
# ---------------------------------------------------------------------------

# Breadth-first over `references`. Hub files are recorded as reached but are not
# expanded, otherwise every screen transitively reaches the whole application.
def reachable_files(start_file, entry_by_file, hub_files, max_depth)
  depths = { start_file => 0 }
  queue = [start_file]

  until queue.empty?
    file = queue.shift
    depth = depths[file]
    next if depth >= max_depth
    next if depth > 0 && hub_files.include?(file)

    entry = entry_by_file[file]
    next unless entry

    (entry['references'] || []).each do |ref|
      next if depths.key?(ref)
      depths[ref] = depth + 1
      queue << ref
    end
  end

  depths
end

# ---------------------------------------------------------------------------
# Call flow of one screen (who calls whom)
# ---------------------------------------------------------------------------

class_to_file = {}
entry_by_file.each_value { |e| class_to_file[e['class']] = e['file'] if e['class'] }

# Files that are only inherited from (ApplicationRecord, BaseService,
# SalesOrders::BaseController…) are plumbing, not a step in the story.
def superclass_file(entry, class_to_file)
  sup = entry && entry['superclass']
  return nil unless sup
  class_to_file[sup] || class_to_file[sup.split('::').last]
end

# Kinds that are expanded further. A model is where the story ends ("which data
# does it touch"); following a model's own references would wander into every
# association in the schema.
FLOW_EXPAND = %w[services jobs lib controllers].freeze
FLOW_MAX_DEPTH = 4
FLOW_MAX_CHILDREN = 40

# Tree rooted at one action. Each file appears once, at its shallowest position,
# so the tree reads top-down without repeating itself.
def call_flow(ctrl_entry, action, entry_by_file, bucket_by_file, hub_files, class_to_file)
  roots = (ctrl_entry['action_references'] || {})[action]
  if roots.nil?
    # The action is inherited (Devise's sessions#new, a base controller's index):
    # nothing in this file runs for it except the filters that apply to it. Falling
    # back to the whole file would hand the screen every sibling action's work.
    roots = (ctrl_entry['filter_references'] || []).select do |f|
      (f['only'].empty? || f['only'].include?(action)) && !f['except'].include?(action)
    end.flat_map { |f| f['references'] }.uniq
  end
  own_sup = superclass_file(ctrl_entry, class_to_file)
  placed = Set.new([ctrl_entry['file']])

  build = lambda do |refs, parent_entry, depth|
    sup = superclass_file(parent_entry, class_to_file)
    refs.reject { |r| r == sup || r == own_sup || placed.include?(r) || !bucket_by_file[r] || r =~ %r{(\A|/)errors?\.rb\z} }
        .first(FLOW_MAX_CHILDREN)
        .map do |r|
          placed << r
          node = { 'file' => r, 'bucket' => bucket_by_file[r] }
          node['hub'] = true if hub_files.include?(r)
          entry = entry_by_file[r]
          if entry && depth < FLOW_MAX_DEPTH && !node['hub'] && FLOW_EXPAND.include?(bucket_by_file[r])
            kids = build.call(entry['references'] || [], entry, depth + 1)
            node['calls'] = kids unless kids.empty?
          end
          node
        end
  end

  build.call(roots, ctrl_entry, 1)
end

# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

routes_by_screen = Hash.new { |h, k| h[k] = [] }
(scan['routes'] || []).each do |r|
  next unless r['controller'] && r['action']
  routes_by_screen["#{r['controller']}##{r['action']}"] << r
end

# Controllers provided by Rails itself or by a gem's engine. A route pointing at
# one of these is normal, so keeping them in `unresolved_controllers` would bury
# the real finding (routes pointing at controllers that do not exist).
FRAMEWORK_CONTROLLER_PREFIXES = %w[
  rails/ devise/ doorkeeper/ active_storage/ action_mailbox/ turbo/
].freeze

def framework_controller?(controller)
  FRAMEWORK_CONTROLLER_PREFIXES.any? { |p| controller.to_s.start_with?(p) }
end

screens = []
unresolved_controllers = Set.new
# Routes handled by Rails or a gem are expected to have no file of ours. They
# are counted only so the summary can say the number is accounted for, not
# silently dropped.
framework_route_count = 0

routes_by_screen.each do |screen_id, routes|
  first = routes.first
  ctrl_class = first['controller_class']
  ctrl_file  = resolve_controller_file(first, controller_file_by_class, controller_file_by_path)

  if ctrl_file.nil?
    if framework_controller?(first['controller'])
      framework_route_count += 1
    else
      unresolved_controllers << ctrl_class
    end
    next
  end

  ctrl_entry = entry_by_file[ctrl_file]
  action     = first['action']
  depths     = reachable_files(ctrl_file, entry_by_file, hub_files, options[:max_depth])
  views      = collect_views(first['controller'], action, views_by_controller, view_by_file, view_lookup)

  code_files = depths.keys.sort.map do |f|
    {
      'file'   => f,
      'bucket' => bucket_by_file[f],
      'depth'  => depths[f],
      'hub'    => hub_files.include?(f)
    }
  end

  screens << {
    'id'              => screen_id,
    'controller'      => first['controller'],
    'action'          => action,
    'controller_class' => ctrl_class,
    'controller_file' => ctrl_file,
    'pack'            => ctrl_entry && ctrl_entry['pack'],
    'action_defined'  => !!(ctrl_entry && (ctrl_entry['actions'] || []).include?(action)),
    'routes'          => routes.map { |r| { 'method' => r['method'], 'path' => r['path'], 'source_file' => r['source_file'], 'source_line' => r['source_line'] } },
    'views'           => views,
    'vue_page'        => vue_page_by_key["#{first['controller']}/#{action}"],
    'code_files'      => code_files,
    'code_file_count' => code_files.size,
    # Who calls whom, starting from this action alone. code_files above stays as
    # the flat, whole-controller reach that KR2's blast radius is built on.
    'view_tree'       => view_tree(first['controller'], action, views_by_controller, view_by_file, view_lookup),
    'flow'            => ctrl_entry ? call_flow(ctrl_entry, action, entry_by_file, bucket_by_file, hub_files, class_to_file) : []
  }
end

screens.sort_by! { |s| s['id'] }

# ---------------------------------------------------------------------------
# Frontend → API: page → composable/component → api function → route → action
# ---------------------------------------------------------------------------

def route_regex(path)
  p = path.to_s.gsub(/\(\/[^)]*\)/, '').gsub(/\(\.:format\)/, '').gsub(%r{/{2,}}, '/')
  p = Regexp.escape(p).gsub(/:\w+/, '[^/]+')
  /\A#{p}\z/
end

def normalize_api_path(path)
  path.to_s.sub(/[?#].*\z/, '').gsub(/\$\{[^}]*\}/, 'X').sub(/\.json\z/, '').gsub(%r{/{2,}}, '/')
end

route_matchers = (scan['routes'] || []).select { |r| r['controller'] && r['action'] }.map do |r|
  [r['method'].to_s.upcase.split('|'), route_regex(r['path']), "#{r['controller']}##{r['action']}"]
end
screen_ids = Set.new(screens.map { |s| s['id'] })

match_route = lambda do |method, path|
  np = normalize_api_path(path)
  m = method.upcase
  hit = route_matchers.find { |ms, re, id| (ms.include?(m) || (m == 'PUT' && ms.include?('PATCH')) || (m == 'PATCH' && ms.include?('PUT'))) && re =~ np && screen_ids.include?(id) }
  hit && hit[2]
end

api_fn = {}
(scan['frontend_api'] || []).each { |a| api_fn["#{a['object']}.#{a['fn']}"] = a }
modules = {}
(scan['frontend_modules'] || []).each { |m| modules[m['file']] = m }

FRONT_MAX_DEPTH = 4
# Shared layout / UI kit files call their own APIs (notifications, sidebar); following
# them would attach the same calls to every page.
FRONT_SHARED = %r{/components/(layouts|parts|base|common)/|/stores/|/utils/|/api/}.freeze

by_id = {}
screens.each { |s| by_id[s['id']] = s }
screens.each do |s|
  page = s['vue_page']
  next unless page && modules[page]
  s['title_ja'] = modules[page]['page_title_ja'] if modules[page]['page_title_ja']

  calls = {}
  # queue item: [file, via, names] — names = the functions of `file` the caller uses
  # (nil = the whole file: the page itself, or a module with no function table)
  queue = [[page, [page], nil]]
  seen = Set.new([page])
  until queue.empty?
    file, via, names = queue.shift
    m = modules[file]
    next unless m
    api_list = m['api_calls'] || []
    if names && m['functions']
      reach = Set.new
      stack = names.dup
      until stack.empty?
        n = stack.pop
        next if reach.include?(n) || !m['functions'][n]
        reach << n
        stack.concat(m['functions'][n]['calls'] || [])
      end
      api_list = reach.flat_map { |n| m['functions'][n]['api_calls'] || [] }.uniq
    end
    api_list.each do |obj, fn|
      a = api_fn["#{obj}.#{fn}"]
      next unless a
      target = match_route.call(a['method'], a['path'])
      key = "#{obj}.#{fn}"
      calls[key] ||= { 'method' => a['method'], 'path' => normalize_api_path(a['path']), 'fn' => key, 'screen' => target, 'via' => via }
    end
    (m['urls'] || []).each do |meth, path|
      target = match_route.call(meth, path)
      key = "#{meth} #{normalize_api_path(path)}"
      calls[key] ||= { 'method' => meth, 'path' => normalize_api_path(path), 'fn' => nil, 'screen' => target, 'via' => via }
    end
    next if via.size > FRONT_MAX_DEPTH
    (m['imports'] || []).each do |imp|
      next if seen.include?(imp) || imp =~ FRONT_SHARED
      seen << imp
      used = (m['import_uses'] || {})[imp]
      queue << [imp, via + [imp], used]
    end
  end
  s['api_calls'] = calls.values
  calls.values.each do |c|
    t = c['screen'] && by_id[c['screen']]
    next unless t
    (t['called_from'] ||= []) << { 'screen' => s['id'], 'vue_page' => page, 'fn' => c['fn'], 'via' => c['via'] }
  end
end

# ---------------------------------------------------------------------------
# Reverse index: file -> screens that reach it
# ---------------------------------------------------------------------------

screens_by_file = Hash.new { |h, k| h[k] = [] }
screens.each do |s|
  s['code_files'].each { |cf| screens_by_file[cf['file']] << s['id'] }
  s['views'].each      { |v|  screens_by_file[v] << s['id'] }
  screens_by_file[s['vue_page']] << s['id'] if s['vue_page']
end

total_screens = screens.size

shared_files = screens_by_file.map do |file, ids|
  uniq = ids.uniq
  {
    'file'          => file,
    'bucket'        => bucket_by_file[file] || (view_by_file.key?(file) ? 'views' : nil),
    'screen_count'  => uniq.size,
    'screen_ratio'  => total_screens.zero? ? 0.0 : (uniq.size.to_f / total_screens).round(4),
    'hub'           => hub_files.include?(file),
    'raw_fan_in'    => fan_in[file],
    'screens'       => uniq.sort.first(20),
    'screens_truncated' => uniq.size > 20
  }
end
shared_files.sort_by! { |f| [-f['screen_count'], f['file']] }

# ---------------------------------------------------------------------------
# Screen <-> screen relatedness
# ---------------------------------------------------------------------------
# 2つの画面が「関連している」とは、同じ *具体的な* ファイルに到達していること。
# ハブはこの判定に何も寄与しない（ApplicationRecord は 1180 画面が参照する）ため、
# RELATED_FILE_CAP 画面を超えるファイルはペアを生成せず、重みは到達画面数で減衰させる。
# 正規化はコサイン相当。スコアは 0.0〜1.0。

RELATED_FILE_CAP   = 150
RELATED_PER_SCREEN = 12
RELATED_MIN_SCORE  = 0.05

file_idf = {}
screens_by_file.each do |file, ids|
  next if hub_files.include?(file)
  n = ids.uniq.size
  next if n > RELATED_FILE_CAP
  file_idf[file] = 1.0 / Math.log(2.0 + n)
end

screen_norm = Hash.new(0.0)
screens_by_file.each do |file, ids|
  w = file_idf[file]
  next unless w
  ids.uniq.each { |id| screen_norm[id] += w * w }
end

pair_score = Hash.new { |h, k| h[k] = Hash.new(0.0) }
screens_by_file.each do |file, ids|
  w = file_idf[file]
  next unless w
  uniq = ids.uniq
  next if uniq.size < 2
  contrib = w * w
  uniq.each_with_index do |a, i|
    ((i + 1)...uniq.size).each do |j|
      b = uniq[j]
      pair_score[a][b] += contrib
      pair_score[b][a] += contrib
    end
  end
end

screens.each do |s|
  norm_a = screen_norm[s['id']]
  peers = pair_score[s['id']]
  related = []
  if norm_a > 0 && !peers.empty?
    peers.each do |other, raw|
      norm_b = screen_norm[other]
      next if norm_b <= 0
      score = raw / Math.sqrt(norm_a * norm_b)
      next if score < RELATED_MIN_SCORE
      related << { 'id' => other, 'score' => score.round(4) }
    end
    related.sort_by! { |r| [-r['score'], r['id']] }
  end
  s['related_screens'] = related.first(RELATED_PER_SCREEN)
end

# ---------------------------------------------------------------------------
# Files no screen reaches — candidates for step 3 to flag, not proof of dead code
# ---------------------------------------------------------------------------

reached = Set.new(screens_by_file.keys)
unreached = []
RUBY_BUCKETS.each do |bucket|
  (scan[bucket] || []).each do |e|
    next if reached.include?(e['file'])
    unreached << { 'file' => e['file'], 'bucket' => bucket, 'class' => e['class'], 'pack' => e['pack'] }
  end
end
unreached.sort_by! { |e| e['file'] }

# ---------------------------------------------------------------------------
# Per-pack rollup
# ---------------------------------------------------------------------------

pack_rollup = Hash.new { |h, k| h[k] = { 'screens' => 0, 'files' => 0 } }
screens.each { |s| pack_rollup[s['pack'] || '(root)']['screens'] += 1 }
RUBY_BUCKETS.each do |bucket|
  (scan[bucket] || []).each { |e| pack_rollup[e['pack'] || '(root)']['files'] += 1 }
end

# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

out = {
  'meta' => {
    'schema_version' => SCHEMA_VERSION,
    'generated_at'   => Time.now.utc.iso8601,
    'scan_commit'    => scan_meta['commit'],
    'scan_generated_at' => scan_meta['generated_at'],
    'scan_mode'      => scan_meta['mode'],
    'routes_source'  => scan_meta['routes_source'],
    'max_depth'      => options[:max_depth],
    'hub_threshold'  => options[:hub_threshold],
    'screen_unit'    => 'controller#action',
    'related_file_cap'   => RELATED_FILE_CAP,
    'related_per_screen' => RELATED_PER_SCREEN,
    'related_min_score'  => RELATED_MIN_SCORE
  },
  'stats' => {
    'screens'                => total_screens,
    'routes'                 => (scan['routes'] || []).size,
    'hub_files'              => hub_files.size,
    'screens_with_vue_page'  => screens.count { |s| s['vue_page'] },
    'files_reached'          => reached.size,
    'files_unreached'        => unreached.size,
    'unresolved_controllers' => unresolved_controllers.size,
    'framework_routes'       => framework_route_count,
    'avg_files_per_screen'   => total_screens.zero? ? 0 : (screens.sum { |s| s['code_file_count'] }.to_f / total_screens).round(1),
    'screens_with_related'   => screens.count { |s| !s['related_screens'].empty? }
  },
  'packs'   => pack_rollup,
  'hubs'    => hub_files.to_a.sort.map { |f| { 'file' => f, 'raw_fan_in' => fan_in[f] } }
                       .sort_by { |h| -h['raw_fan_in'] },
  'screens' => screens,
  'shared_files' => shared_files,
  'unreached_files' => unreached,
  'unresolved_controllers' => unresolved_controllers.to_a.sort
}

FileUtils.mkdir_p(File.dirname(OUT))
File.write(OUT, JSON.pretty_generate(out))

unless options[:quiet]
  rel = OUT.sub("#{Dir.pwd}/", '')
  s = out['stats']
  warn "[impact] analysis -> #{rel}"
  warn "[impact] scan_commit=#{scan_meta['commit']} screens=#{s['screens']} avg_files_per_screen=#{s['avg_files_per_screen']}"
  warn "[impact] related: #{s['screens_with_related']}/#{s['screens']} screens have peers (cap=#{RELATED_FILE_CAP} min_score=#{RELATED_MIN_SCORE})"
  warn "[impact] hubs=#{s['hub_files']} reached=#{s['files_reached']} unreached=#{s['files_unreached']} unresolved_controllers=#{s['unresolved_controllers']} framework_routes=#{s['framework_routes']}"
  top = shared_files.reject { |f| f['hub'] }.first(3)
  warn "[impact] most shared (non-hub): #{top.map { |f| "#{f['file']}(#{f['screen_count']})" }.join(' ')}" unless top.empty?
end
