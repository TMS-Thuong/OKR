#!/usr/bin/env ruby
# frozen_string_literal: true

# Code-map scanner (KR1 step 1: read the code).
#
# Extracts RAW structural facts from a Rails codebase into a single JSON file.
# It does NOT group files into features and it does NOT write human docs --
# those are steps 2 and 3 of KR1.
#
# Ruby stdlib only. Written to run on Ruby >= 2.6 (no filter_map, no Hash#except,
# no endless methods) so it works on old host rubies without bundler/docker.
#
# Usage:
#   ruby scan_code_map.rb [--root DIR] [--out FILE] [--since SHA]
#                         [--routes-table FILE] [--quiet]

require 'json'
require 'optparse'
require 'set'
require 'time'

SCHEMA_VERSION = 1

options = {
  :root          => '.',
  :out           => nil,
  :since         => nil,
  :routes_table  => nil,
  :quiet         => false
}

OptionParser.new do |o|
  o.banner = 'Usage: scan_code_map.rb [options]'
  o.on('--root DIR', 'Repository root (default: .)') { |v| options[:root] = v }
  o.on('--out FILE', 'Output JSON path (default: <root>/.ai/code-map/raw/scan.json)') { |v| options[:out] = v }
  o.on('--since SHA', 'Incremental: only re-read files changed since SHA') { |v| options[:since] = v }
  o.on('--routes-table FILE', 'Optional `rails routes` text output, used as ground truth for routes') { |v| options[:routes_table] = v }
  o.on('--quiet', 'Suppress the summary printed to stderr') { options[:quiet] = true }
  o.on('-h', '--help') { puts o; exit 0 }
end.parse!

ROOT = File.expand_path(options[:root])
OUT  = File.expand_path(options[:out] || File.join(ROOT, '.ai', 'code-map', 'raw', 'scan.json'))

abort("not a directory: #{ROOT}") unless File.directory?(ROOT)

def rel(path)
  p = path.sub(/\A#{Regexp.escape(ROOT)}\/?/, '')
  p
end

def read_file(path)
  File.read(path, :encoding => 'UTF-8').scrub('?')
rescue StandardError
  ''
end

def git(root, *args)
  out = IO.popen([{ 'GIT_OPTIONAL_LOCKS' => '0' }, 'git', '-C', root, *args], :err => File::NULL, &:read)
  return nil unless $?.success?
  out
end

# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

APP_ROOTS = lambda do
  roots = []
  roots << File.join(ROOT, 'app') if File.directory?(File.join(ROOT, 'app'))
  Dir.glob(File.join(ROOT, 'packs', '*', 'app')).sort.each { |d| roots << d }
  roots
end.call

def pack_of(path)
  m = rel(path).match(%r{\Apacks/([^/]+)/})
  m ? m[1] : nil
end

def ruby_files(subdir)
  APP_ROOTS.flat_map { |r| Dir.glob(File.join(r, subdir, '**', '*.rb')) }.sort
end

def view_files
  APP_ROOTS.flat_map { |r| Dir.glob(File.join(r, 'views', '**', '*')) }
           .select { |f| File.file?(f) }.sort
end

# ---------------------------------------------------------------------------
# Generic Ruby source parsing (regex-based, intentionally shallow)
# ---------------------------------------------------------------------------

def strip_comments(src)
  src.gsub(/^\s*#.*$/, '').gsub(/=begin.*?=end/m, '')
end

# Returns [full_name, superclass, line, kind] where kind is 'class' or 'module'.
# Falls back to the module nesting when a file declares no class (helper modules,
# concerns, mixins) so that every readable file still gets a constant name.
def primary_class(src)
  stack = []
  first_module_line = nil
  line_no = 0
  src.each_line do |line|
    line_no += 1
    if (m = line.match(/^\s*module\s+([A-Z][\w:]*)\s*$/))
      stack.push(m[1])
      first_module_line ||= line_no
    elsif (m = line.match(/^\s*class\s+([A-Z][\w:]*)\s*(?:<\s*([A-Z][\w:.]*(?:\([^)]*\))?))?/))
      name = m[1]
      full = name.include?('::') ? name : (stack + [name]).join('::')
      return [full, m[2], line_no, 'class']
    end
  end
  return [stack.join('::'), nil, first_module_line, 'module'] unless stack.empty?
  [nil, nil, nil, nil]
end

# Public instance/class methods declared before the first private/protected marker.
def public_methods_of(src)
  methods = []
  src.each_line do |line|
    break if line =~ /^\s*(private|protected)\s*$/
    if (m = line.match(/^\s*def\s+(?:self\.)?([a-zA-Z_][\w]*[?!=]?)/))
      name = m[1]
      next if name.start_with?('_')
      methods << name
    end
  end
  methods.uniq
end

def includes_of(src)
  src.scan(/^\s*(?:include|extend|prepend)\s+([A-Z][\w:]*)/).flatten.uniq
end

CONST_RE = /\b((?:[A-Z][A-Za-z0-9_]*)(?:::[A-Z][A-Za-z0-9_]*)*)/.freeze

def constants_in(src)
  src.scan(CONST_RE).flatten.uniq
end

# Method bodies by name, found by indentation: a `def` at indent N ends at the
# first `end` back at indent N. The repo is rubocop-formatted, so this is exact
# for the code it is used on (the same rule pr-diff relies on).
# Returns { 'name' => 'body source' }. One-line and endless defs are kept too.
def method_bodies(src)
  bodies = {}
  lines = src.lines
  i = 0
  while i < lines.size
    m = lines[i].match(/^(\s*)def\s+(?:self\.)?([a-zA-Z_]\w*[?!=]?)/)
    unless m
      i += 1
      next
    end
    indent = m[1].size
    name = m[2]
    if lines[i] =~ /;\s*end\s*$/ || lines[i] =~ /^\s*def\s+[^=\s(]+(\([^)]*\))?\s*=\s*\S/
      bodies[name] = lines[i]
      i += 1
      next
    end
    j = i + 1
    j += 1 while j < lines.size && !(lines[j] =~ /^\s*end\b/ && lines[j][/^\s*/].size == indent)
    bodies[name] = lines[i..[j, lines.size - 1].min].join
    i = j + 1
  end
  bodies
end

# before_action :name[, only: [...] | except: [...]] -> [[name, only, except], ...]
def filter_rules(src)
  src.scan(/^\s*(?:prepend_)?(?:before|around)_action\s+([^\n]+)/).flatten.map do |rest|
    head = rest.split(/\b(?:only|except|if|unless|prepend):/).first.to_s
    names = head.scan(/(?:\A|,\s*):(\w+)/).flatten
    only = (rest[/only:\s*(\[[^\]]*\]|:\w+)/, 1] || '').scan(/:?(\w+)/).flatten
    except = (rest[/except:\s*(\[[^\]]*\]|:\w+)/, 1] || '').scan(/:?(\w+)/).flatten
    names.map { |n| [n, only, except] }
  end.flatten(1)
end

# The source an action actually runs: its own body, the filters that apply to it,
# and — transitively, a few hops deep — the private helpers those call.
def action_source(action, bodies, rules, max_hops = 3)
  names = [action]
  rules.each do |name, only, except|
    next if !only.empty? && !only.include?(action)
    next if except.include?(action)
    names << name
  end
  seen = Set.new
  frontier = names.select { |n| bodies.key?(n) }
  parts = []
  max_hops.times do
    nxt = []
    frontier.each do |n|
      next if seen.include?(n)
      seen << n
      body = bodies[n]
      parts << body
      body.scan(/\b([a-z_]\w*[?!]?)/).flatten.uniq.each { |w| nxt << w if bodies.key?(w) && !seen.include?(w) }
    end
    frontier = nxt
    break if frontier.empty?
  end
  parts.join("\n")
end

# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------

def scan_packs
  Dir.glob(File.join(ROOT, 'packs', '*', 'package.yml')).sort.map do |f|
    src = read_file(f)
    deps = src.scan(/^\s*-\s*["']?([\w\/.\-]+)["']?\s*$/).flatten
    {
      'name'                 => File.basename(File.dirname(f)),
      'path'                 => rel(File.dirname(f)),
      'enforce_dependencies' => !!(src =~ /enforce_dependencies:\s*true/),
      'enforce_privacy'      => !!(src =~ /enforce_privacy:\s*true/),
      'dependencies'         => deps
    }
  end
end

# ---------------------------------------------------------------------------
# Routes (static parse of the Rails routing DSL)
# ---------------------------------------------------------------------------

HTTP_METHODS = %w[get post put patch delete options head].freeze
REST_ACTIONS = {
  'index'   => ['GET',    ''],
  'create'  => ['POST',   ''],
  'new'     => ['GET',    'new'],
  'edit'    => ['GET',    ':id/edit'],
  'show'    => ['GET',    ':id'],
  'update'  => ['PATCH',  ':id'],
  'destroy' => ['DELETE', ':id']
}.freeze
SINGULAR_REST_ACTIONS = {
  'create'  => ['POST',   ''],
  'new'     => ['GET',    'new'],
  'edit'    => ['GET',    'edit'],
  'show'    => ['GET',    ''],
  'update'  => ['PATCH',  ''],
  'destroy' => ['DELETE', '']
}.freeze

# Routes Devise generates for each module, as HTTP method + path suffix + action.
# Only modules whose controller the app actually overrides are emitted, so we
# never invent routes for a module the model does not enable.
DEVISE_ROUTES = {
  'sessions' => [
    ['GET',    'sign_in',  'new'],
    ['POST',   'sign_in',  'create'],
    ['DELETE', 'sign_out', 'destroy']
  ],
  'registrations' => [
    ['GET',    'sign_up', 'new'],
    ['POST',   '',        'create'],
    ['GET',    'edit',    'edit'],
    ['PATCH',  '',        'update'],
    ['DELETE', '',        'destroy'],
    ['GET',    'cancel',  'cancel']
  ],
  'passwords' => [
    ['GET',   'password/new',  'new'],
    ['POST',  'password',      'create'],
    ['GET',   'password/edit', 'edit'],
    ['PATCH', 'password',      'update']
  ],
  'confirmations' => [
    ['GET',  'confirmation/new', 'new'],
    ['POST', 'confirmation',     'create'],
    ['GET',  'confirmation',     'show']
  ],
  'unlocks' => [
    ['GET',  'unlock/new', 'new'],
    ['POST', 'unlock',     'create'],
    ['GET',  'unlock',     'show']
  ]
}.freeze

class RoutesParser
  attr_reader :routes, :files_read

  def initialize(root)
    @root = root
    @routes = []
    @files_read = []
  end

  def parse_entrypoints
    entry = File.join(@root, 'config', 'routes.rb')
    parse_file(entry) if File.exist?(entry)
    @routes
  end

  # Files a caller must watch to know whether routes need re-parsing.
  def self.candidate_files(root)
    list = [File.join(root, 'config', 'routes.rb')]
    list += Dir.glob(File.join(root, 'config', 'routes', '**', '*.rb'))
    list += Dir.glob(File.join(root, 'packs', '*', 'config', 'routes', '**', '*.rb'))
    list.select { |f| File.exist?(f) }.sort
  end

  private

  def parse_file(path, stack = [])
    return if @files_read.include?(path)
    @files_read << path
    src = read_file(path)
    frames = stack.dup
    block_stack = []
    line_no = 0

    # A route statement may be spread over several lines when its options do not
    # fit on one (`scope '/x',` / newline / `module: 'y' do`). Join those into one
    # logical line first, otherwise the options are lost and, worse, the frame is
    # pushed without its matching `end` being recognised.
    logical = []
    pending = nil
    pending_line = nil
    src.each_line do |raw|
      line_no += 1
      stripped = strip_line_comment(raw).strip
      next if stripped.empty?
      if pending
        pending << ' ' << stripped
      else
        pending = stripped.dup
        pending_line = line_no
      end
      next if pending.end_with?(',') || unbalanced?(pending)
      logical << [pending_line, pending]
      pending = nil
    end
    logical << [pending_line, pending] if pending

    logical.each do |line_no, line|

      if line =~ /^end\b/
        frame = block_stack.pop
        frames.pop if frame == :scope
        next
      end

      # Anything that needs a matching `end` must be pushed, not just `... do`.
      # A bare `unless Rails.env.production?` around a few routes is common, and
      # missing it makes its `end` pop a real namespace frame instead.
      opens_block = !!(line =~ /\bdo\b(\s*\|[^|]*\|)?\s*$/) ||
                    !!(line =~ /^(if|unless|case|begin|while|until|for)\b/) ||
                    !!(line =~ /^(def|class|module)\s/)

      # A drawn file that opens its own `...routes.draw do` restarts at the root
      # scope in Rails, so any namespace/scope active at the `draw(...)` call
      # site must be dropped rather than inherited.
      if line =~ /\broutes\.draw\b/
        frames.clear
        block_stack.push(:other) if opens_block
        next
      end

      if (m = line.match(/^draw\(?\s*:([\w\/]+)\s*\)?/))
        target = resolve_draw(m[1])
        parse_file(target, frames) if target
        block_stack.push(:other) if opens_block
        next
      end

      handled = false

      if (m = line.match(/^devise_for\s+:(\w+)(.*)$/))
        emit_devise(m[1], m[2], frames, path, line_no, line)
        block_stack.push(:other) if opens_block
        next
      end

      if (m = line.match(/^namespace\s+:?["']?([\w\/]+)["']?(.*)$/))
        # `namespace :x, module: :y` keeps the path x but puts controllers under y/.
        ns_module = m[2][/module:\s*["':]([\w\/]+)["']?/, 1] || m[1]
        frames.push({ :kind => 'namespace', :path => m[1], :module => ns_module })
        block_stack.push(:scope)
        handled = true
      elsif (m = line.match(/^scope\s+(.*)$/))
        frames.push(scope_frame(m[1]))
        block_stack.push(:scope) if opens_block
        handled = true
      elsif (m = line.match(/^(?:resources|resource)\s+:([\w]+)(.*)$/))
        singular = line.start_with?('resource ')
        emit_resource(m[1], m[2], frames, path, line_no, singular)
        if opens_block
          frames.push({ :kind => 'resource',
                        :path => m[1],
                        :param => (singular ? nil : ":#{m[1].sub(/s\z/, '')}_id"),
                        :controller => resource_controller(m[1], m[2], singular),
                        :module => nil })
          block_stack.push(:scope)
        end
        handled = true
      elsif (mc = line.match(/^(member|collection)\b/))
        # member/collection add no path segment of their own; they change how the
        # enclosing `resources` frame renders its id parameter.
        if opens_block
          frames.push({ :kind => mc[1] })
          block_stack.push(:scope)
        end
        handled = true
      elsif (m = line.match(/^root\s+(.*)$/))
        target = extract_target(m[1])
        add_route('GET', join_path(frames, ''), target, frames, path, line_no, line) if target
        handled = true
      elsif (m = line.match(/^(#{HTTP_METHODS.join('|')}|match)\s+(.*)$/))
        emit_method(m[1], m[2], frames, path, line_no, line)
        handled = true
      end

      block_stack.push(:other) if opens_block && !handled
    end
  end

  # True while a statement still has an open brace or paren, so a declaration
  # spread over several lines (`devise_for :users, controllers: {` ... `}`) is
  # assembled before being matched.
  def unbalanced?(line)
    depth = 0
    quote = nil
    line.each_char do |ch|
      if quote
        quote = nil if ch == quote
      elsif ch == "'" || ch == '"'
        quote = ch
      elsif ch == '{' || ch == '('
        depth += 1
      elsif ch == '}' || ch == ')'
        depth -= 1
      end
    end
    depth > 0
  end

  # Quote-aware comment stripping. A naive /#.*$/ eats the `#` in
  # `to: 'home#index'`, which silently drops every explicitly targeted route.
  def strip_line_comment(line)
    quote = nil
    line.each_char.with_index do |ch, i|
      if quote
        quote = nil if ch == quote
      elsif ch == "'" || ch == '"'
        quote = ch
      elsif ch == '#'
        return line[0, i]
      end
    end
    line
  end

  def resolve_draw(name)
    candidates = [
      File.join(@root, 'config', 'routes', "#{name}.rb"),
      *Dir.glob(File.join(@root, 'packs', '*', 'config', 'routes', "#{name}.rb"))
    ]
    candidates.find { |f| File.exist?(f) }
  end

  def scope_frame(rest)
    # The positional argument is the path unless an explicit `path:` is given.
    # It must still be read when `module:` is present -- `scope '/v3', module: 'api/v3'`
    # contributes both.
    path = rest[/path:\s*["']([^"']+)["']/, 1] || rest[/^["']([^"']+)["']/, 1]
    mod  = rest[/module:\s*["':]([\w\/]+)["']?/, 1]
    # `scope 'x', controller: 'x'` makes every shorthand route inside it target
    # that controller.
    ctrl = rest[/controller:\s*["':]([\w\/]+)["']?/, 1]
    { :kind => 'scope', :path => path, :module => mod, :controller => ctrl }
  end

  # `devise_for :users, controllers: { sessions: 'users/sessions' }` generates
  # its routes inside Devise, so nothing in routes.rb names them. Without this
  # every sign-in / sign-up / password screen is missing from the map and its
  # controller looks unreachable.
  def emit_devise(name, rest, frames, file, line_no, raw)
    prefix  = rest[/path:\s*["']([\w\/]+)["']/, 1] || name
    skipped = (rest[/skip:\s*\[([^\]]*)\]/, 1] || rest[/skip:\s*:(\w+)/, 1] || '').scan(/\w+/)
    return if skipped.include?('all')

    overrides = {}
    rest.scan(/:?(\w+)\s*(?::|=>)\s*["']([\w\/]+)["']/) do |mod, ctrl|
      overrides[mod] = ctrl if DEVISE_ROUTES.key?(mod)
    end

    overrides.each do |mod, controller|
      next if skipped.include?(mod)
      next unless controller_exists?(controller)
      DEVISE_ROUTES[mod].each do |http_method, suffix, action|
        seg = [prefix, suffix].reject { |x| x.nil? || x.empty? }.join('/')
        # Devise takes `controllers:` as an absolute controller path, so the
        # surrounding namespace must not be prepended to it again.
        add_route(http_method, join_path(frames, seg), "#{controller}##{action}",
                  [], file, line_no, raw, 'devise')
      end
    end
  end

  def controller_exists?(controller)
    rel = "app/controllers/#{controller}_controller.rb"
    File.exist?(File.join(@root, rel)) ||
      !Dir.glob(File.join(@root, 'packs', '*', rel)).empty?
  end

  def emit_method(http_method, rest, frames, file, line_no, raw)
    http_method = http_method == 'match' ? (rest[/via:\s*\[?:(\w+)/, 1] || 'match').upcase : http_method.upcase
    on = rest[/on:\s*:(\w+)/, 1]
    path = rest[/^["']([^"']*)["']/, 1] || rest[/^:(\w+)/, 1] || ''
    target = extract_target(rest)

    if target.nil?
      # `get :status` inside a resources block: the action name is the path and
      # the controller comes from the enclosing resource. Dropping these loses
      # every custom member/collection route in the app.
      # Nearest frame that names a controller -- a resources block or a
      # `scope ..., controller: '...'`.
      res = frames.reverse.find { |f| f[:controller] }
      literal_path = rest[/^["']([^"']*)["']/, 1]
      # Inside a resources block the controller always comes from the resource;
      # the action is the leading symbol, an explicit `action:`, or the last
      # path segment (`post 'items/bulk_decide', action: :bulk_decide`).
      action = rest[/^:(\w+)/, 1] || rest[/action:\s*:(\w+)/, 1] ||
               (literal_path && literal_path.split('/').reject { |x| x.start_with?(':') }.last)
      if res && action
        target = "#{res[:controller]}##{action}"
      else
        # `get 'labels/show_qr_codes'` with no `to:`: Rails derives
        # controller#action from the path itself. Only safe when every segment
        # is a plain word -- a path with :params cannot be inferred.
        # `match :upload_file` is the symbol form of the same shorthand.
        literal_path ||= rest[/^:(\w+)/, 1]
        return unless literal_path && literal_path =~ %r{\A[\w/]+\z}
        segs = literal_path.split('/')
        mods = frames.map { |f| f[:module] }.compact
        if segs.size > 1
          target = "#{segs[0..-2].join('/')}##{segs[-1]}"
        elsif !mods.empty?
          # `namespace :inventory_imports do match 'upload_file' end` maps to
          # InventoryImportsController#upload_file: the namespace itself is the
          # controller, so it must not also be applied as a module prefix.
          add_route(http_method, join_path(frames, path, on), "#{mods.join('/')}##{segs[0]}",
                    [], file, line_no, raw)
          return
        else
          return
        end
      end
    end

    add_route(http_method, join_path(frames, path, on), target, frames, file, line_no, raw)
  end

  def extract_target(rest)
    rest[/(?:to:|=>)\s*["'](\/?[\w\/]+#\w+)["']/, 1] ||
      rest[/^["'][^"']*["']\s*=>\s*["'](\/?[\w\/]+#\w+)["']/, 1]
  end

  # Enough of Inflector#pluralize for route names, without pulling in ActiveSupport.
  def pluralize(word)
    case word
    when /(?:ss|us|is)\z/      then "#{word}es"   # status -> statuses
    when /s\z/                 then word          # settings / companies: already plural
    when /(?:x|z|ch|sh)\z/     then "#{word}es"
    when /[^aeiou]y\z/         then "#{word[0..-2]}ies"
    else                            "#{word}s"
    end
  end

  # `resource :draft` (singular) still routes to DraftsController, and
  # `module: :y` moves the controller under y/ without touching the path.
  def resource_controller(name, rest, singular)
    default_controller = singular ? pluralize(name) : name
    controller = rest[/controller:\s*["':]([\w\/]+)["']?/, 1] || default_controller
    res_module = rest[/module:\s*["':]([\w\/]+)["']?/, 1]
    res_module ? "#{res_module}/#{controller}" : controller
  end

  def emit_resource(name, rest, frames, file, line_no, singular)
    controller = resource_controller(name, rest, singular)
    # `only:` must be detected separately from its contents: `only: []` declares
    # zero REST routes, which is not the same as omitting `only:` entirely.
    has_only   = !!(rest =~ /only:/)
    has_except = !!(rest =~ /except:/)
    # Accepts [:a, :b], :a, and the %i[a b] / %w[a b] literals.
    only    = rest.scan(/only:\s*(?:%[iw]?\[([^\]]*)\]|\[([^\]]*)\]|:(\w+))/).flatten.compact.join(',')
    except  = rest.scan(/except:\s*(?:%[iw]?\[([^\]]*)\]|\[([^\]]*)\]|:(\w+))/).flatten.compact.join(',')
    table   = singular ? SINGULAR_REST_ACTIONS : REST_ACTIONS
    actions = table.keys
    actions = actions.select { |a| only.include?(a) }   if has_only
    actions = actions.reject { |a| except.include?(a) } if has_except
    base = singular ? name : name

    actions.each do |action|
      http_method, suffix = table[action]
      seg = [base, suffix].reject { |s| s.nil? || s.empty? }.join('/')
      add_route(http_method, join_path(frames, seg), "#{controller}##{action}", frames, file, line_no, "resources :#{name}")
    end
  end

  # `on` is the `on: :member` / `on: :collection` option of a one-line custom
  # route, which decides the same thing a member/collection block would.
  def join_path(frames, tail, on = nil)
    parts = []
    frames.each_with_index do |f, i|
      case f[:kind]
      when 'resource'
        parts << f[:path]
        nxt = frames[i + 1]
        if (nxt && nxt[:kind] == 'member') || (nxt.nil? && on == 'member')
          parts << ':id'
        elsif (nxt && nxt[:kind] == 'collection') || (nxt.nil? && on == 'collection')
          # collection routes hang off the bare resource path
        else
          parts << f[:param] if f[:param]
        end
      when 'member', 'collection'
        # no path segment of their own
      else
        parts << f[:path] if f[:path]
      end
    end
    parts << tail unless tail.nil? || tail.empty?
    '/' + parts.compact.reject(&:empty?).join('/').sub(%r{\A/+}, '')
  end

  def add_route(http_method, path, target, frames, file, line_no, raw, source = 'static-parse')
    # A leading slash means "absolute controller path" in Rails: the enclosing
    # namespace is NOT applied. Keeping the slash produced `api//foo`.
    if target.start_with?('/')
      target = target[1..-1]
      frames = []
    end
    mods = frames.map { |f| f[:module] }.compact
    ctrl, action = target.split('#')
    full_ctrl = (mods + [ctrl]).join('/')
    @routes << {
      'method'        => http_method,
      'path'        => path,
      'controller'  => full_ctrl,
      'action'      => action,
      'controller_class' => full_ctrl.split('/').map { |s| camelize(s) }.join('::') + 'Controller',
      'source_file' => rel(file),
      'source_line' => line_no,
      'source'      => source,
      'raw'         => raw[0, 200]
    }
  end

  def camelize(str)
    str.split('_').map { |s| s[0] ? s[0].upcase + s[1..-1].to_s : s }.join
  end
end

def camelize_path(str)
  str.split('/').map { |seg| seg.split('_').map { |s| s[0] ? s[0].upcase + s[1..-1].to_s : s }.join }.join('::')
end

# `rails routes` plain-text output -> authoritative route list.
def parse_routes_table(path)
  routes = []
  read_file(path).each_line do |line|
    m = line.match(/^\s*(\S+)?\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)(?:\|\S+)?\s+(\S+)\s+([\w\/]+)#(\w+)/)
    next unless m
    routes << {
      'method'             => m[2],
      'path'             => m[3],
      'controller'       => m[4],
      'action'           => m[5],
      'controller_class' => camelize_path(m[4]) + 'Controller',
      'name'             => m[1],
      'source_file'      => rel(File.expand_path(path)),
      'source_line'      => nil,
      'source'           => 'rails-routes'
    }
  end
  routes
end

# ---------------------------------------------------------------------------
# Per-file entry builders
# ---------------------------------------------------------------------------

def build_controller(file)
  raw = read_file(file)
  src = strip_comments(raw)
  klass, sup, line, kind = primary_class(src)
  {
    'file'          => rel(file),
    'pack'          => pack_of(file),
    'class'         => klass,
    'superclass'    => sup,
    'class_line'    => line,
    'kind'          => kind,
    'actions'       => public_methods_of(src),
    'before_actions' => src.scan(/^\s*(?:before_action|prepend_before_action|around_action|after_action)\s+:(\w+)/).flatten.uniq,
    'includes'      => includes_of(src),
    'renders'       => src.scan(/\brender\s+(?:template:\s*)?["']([\w\/.\-]+)["']/).flatten.uniq,
    'loc'           => raw.lines.size
  }
end

def build_model(file)
  raw = read_file(file)
  src = strip_comments(raw)
  klass, sup, line, kind = primary_class(src)
  assocs = src.scan(/^\s*(belongs_to|has_many|has_one|has_and_belongs_to_many)\s+:(\w+)(.*)$/).map do |kind, name, rest|
    { 'kind' => kind, 'name' => name, 'class_name' => rest[/class_name:\s*["']([\w:]+)["']/, 1] }
  end
  {
    'file'         => rel(file),
    'pack'         => pack_of(file),
    'class'        => klass,
    'superclass'   => sup,
    'class_line'   => line,
    'kind'         => kind,
    'table_name'   => src[/self\.table_name\s*=\s*["'](\w+)["']/, 1],
    'associations' => assocs,
    'scopes'       => src.scan(/^\s*scope\s+:(\w+)/).flatten.uniq,
    'enums'        => src.scan(/^\s*enum\s+:?(\w+)/).flatten.uniq,
    'includes'     => includes_of(src),
    'public_methods' => public_methods_of(src),
    'loc'          => raw.lines.size
  }
end

def build_plain(file)
  raw = read_file(file)
  src = strip_comments(raw)
  klass, sup, line, kind = primary_class(src)
  {
    'file'           => rel(file),
    'pack'           => pack_of(file),
    'class'          => klass,
    'superclass'     => sup,
    'class_line'     => line,
    'kind'           => kind,
    'public_methods' => public_methods_of(src),
    'includes'       => includes_of(src),
    'loc'            => raw.lines.size
  }
end

def build_view(file)
  raw = read_file(file)
  r = rel(file)
  m = r.match(%r{views/(.+?)/([^/]+?)\.[\w.]+\z})
  base = m ? m[2] : File.basename(r)
  {
    'file'           => r,
    'pack'           => pack_of(file),
    'controller_hint' => m ? m[1] : nil,
    'action_hint'    => base.start_with?('_') ? nil : base,
    'partial'        => base.start_with?('_'),
    'renders'        => raw.scan(/\brender\s*\(?\s*(?:partial:\s*)?["']([\w\/.\-]+)["']/).flatten.uniq,
    'loc'            => raw.lines.size
  }
end

def build_frontend(file)
  r = rel(file)
  # app.ts loads `pages/<body data-component>.vue`, and the layout fills that
  # from ApplicationHelper#vue_page_component_path_by_controller, which is
  # exactly "<controller path>/<action>". Recording the key here lets step 2
  # join a screen to its Vue page by convention instead of guessing.
  m = r.match(%r{javascripts?/pages/(.+)\.vue\z})
  {
    'file'     => r,
    'pack'     => pack_of(file),
    'kind'     => File.extname(file).sub('.', ''),
    'page_key' => m && m[1],
    'loc'      => read_file(file).lines.size
  }
end

# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------

def bucket_specs
  [
    ['controllers', 'controllers', :controller],
    ['models',      'models',      :model],
    ['services',    'services',    :plain],
    ['jobs',        'jobs',        :plain],
    ['mailers',     'mailers',     :plain],
    ['lib',         'lib',         :plain]
  ]
end

def build_for(kind, file)
  case kind
  when :controller then build_controller(file)
  when :model      then build_model(file)
  else                  build_plain(file)
  end
end

def bucket_for_path(relpath)
  case relpath
  when %r{(?:\A|packs/[^/]+/)app/controllers/} then ['controllers', :controller]
  when %r{(?:\A|packs/[^/]+/)app/models/}      then ['models',      :model]
  when %r{(?:\A|packs/[^/]+/)app/services/}    then ['services',    :plain]
  when %r{(?:\A|packs/[^/]+/)app/jobs/}        then ['jobs',        :plain]
  when %r{(?:\A|packs/[^/]+/)app/mailers/}     then ['mailers',     :plain]
  when %r{(?:\A|packs/[^/]+/)app/lib/}         then ['lib',         :plain]
  when %r{(?:\A|packs/[^/]+/)app/views/}       then ['views',       :view]
  when %r{app/frontend/javascripts?/pages/.*\.(vue|ts|js)\z} then ['frontend_pages', :frontend]
  end
end

def frontend_files
  APP_ROOTS.flat_map do |r|
    Dir.glob(File.join(r, 'frontend', 'javascript{,s}', 'pages', '**', '*.{vue,ts,js}'))
  end.sort
end

# ---------------------------------------------------------------------------
# Frontend → API: which page calls which endpoint
# ---------------------------------------------------------------------------
# A Vue page does not name a controller. It imports a composable, which calls
# `inventoriesV2Api.createInventory()`, which posts to `/api/inventories_v2.json`.
# Recording the three hops as facts lets step 2 connect a button on a page to the
# Rails action that handles it. Always re-read in full: it is regex over ~1k files.

FRONTEND_SKIP = %r{/(tests?|__tests__|mocks|typescript-client|locales)/|\.(test|spec|stories)\.}.freeze
API_CALL_RE = /\bapiClient\s*\.\s*(get|post|put|patch|delete)\s*(?:<(?:[^<>]|<[^<>]*>)*>)?\s*\(\s*(['"`])(\/[^'"`]*)\2/m.freeze

def frontend_js_root
  APP_ROOTS.map { |r| Dir.glob(File.join(r, 'frontend', 'javascript{,s}')).first }.compact.first
end

def resolve_import(spec, from_abs, js_root)
  base = if spec.start_with?('@/') then File.join(js_root, spec[2..-1])
         elsif spec.start_with?('.') then File.expand_path(spec, File.dirname(from_abs))
         end
  return nil unless base
  [base, "#{base}.ts", "#{base}.vue", "#{base}.js", File.join(base, 'index.ts'), File.join(base, 'index.js')].find { |c| File.file?(c) }
end

# Named functions in a TS/Vue file with their bodies, by brace matching:
# `const foo = async (...) => { ... }` and `function foo(...) { ... }`.
def js_functions(src)
  out = {}
  src.to_enum(:scan, /(?:\b(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::\s*[^=]+?)?=>\s*\{|\bfunction\s+(\w+)\s*\([^)]*\)[^{]*\{)/).each do
    m = Regexp.last_match
    name = m[1] || m[2]
    i = m.end(0)
    depth = 1
    while i < src.size && depth > 0
      c = src[i]
      depth += 1 if c == '{'
      depth -= 1 if c == '}'
      i += 1
    end
    out[name] ||= src[m.end(0)...i]
  end
  out
end

def scan_frontend_calls(data)
  js_root = frontend_js_root
  return data unless js_root

  api = []
  Dir.glob(File.join(js_root, 'api', 'endpoints', '*.{ts,js}')).sort.each do |f|
    src = read_file(f)
    obj = src[/export\s+const\s+(\w+Api)\s*=/, 1]
    next unless obj
    keys = src.to_enum(:scan, /^\s{2}(\w+)\s*:\s*(?:async\s*)?(?:\(|\w+\s*=>|<)/).map { Regexp.last_match }
    keys.each_with_index do |m, k|
      stop = k + 1 < keys.size ? keys[k + 1].begin(0) : src.size
      body = src[m.begin(0)...stop]
      call = body.match(API_CALL_RE)
      next unless call
      api << { 'object' => obj, 'fn' => m[1], 'method' => call[1].upcase, 'path' => call[3], 'file' => rel(f) }
    end
  end

  # The title a user actually reads on the page (在庫登録, 棚卸一覧), resolved from
  # the page header's i18n key, so the map can be searched by what is on screen.
  ja = {}
  ja_file = File.join(js_root, 'locales', 'translation.ja.json')
  ja = (JSON.parse(read_file(ja_file)) rescue {}) if File.file?(ja_file)
  resolve_ja = lambda do |key|
    key.split('.').reduce(ja) { |o, k| o.is_a?(Hash) ? o[k] : nil }.then { |v| v.is_a?(String) ? v : nil }
  end

  modules = []
  Dir.glob(File.join(js_root, '**', '*.{vue,ts,js}')).sort.each do |f|
    next if f =~ FRONTEND_SKIP
    src = read_file(f)
    imports = src.scan(/\bfrom\s+['"]([^'"]+)['"]|\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/).flatten.compact
                 .map { |spec| resolve_import(spec, f, js_root) }.compact.uniq.map { |x| rel(x) }
    calls = src.scan(/\b(\w+Api)\s*\.\s*(\w+)\s*\(/).uniq
    urls = src.to_enum(:scan, API_CALL_RE).map { Regexp.last_match }.map { |m| [m[1].upcase, m[3]] }.uniq
    next if imports.empty? && calls.empty? && urls.empty?
    # Per function: which API calls and which sibling functions it reaches. Lets a
    # page that only uses `registerSubmitForm` from a shared composable be told apart
    # from the edit page that uses `updateSubmitForm` from the same file.
    fns = {}
    bodies = js_functions(src)
    bodies.each do |name, body|
      # A composable's outer function contains every inner function. Count only
      # its own statements, or it would claim all of its children's API calls.
      bodies.each { |other, ob| body = body.sub(ob, '') if other != name && ob.size < body.size && body.include?(ob) }
      fns[name] = {
        'api_calls' => body.scan(/\b(\w+Api)\s*\.\s*(\w+)\s*\(/).uniq,
        'calls' => body.scan(/\b([a-zA-Z_]\w*)\s*\(/).flatten.uniq.select { |w| bodies.key?(w) && w != name }
      }
    end
    words = src.scan(/\b[a-zA-Z_]\w{2,}\b/).uniq
    title_key = src[/<z-page-header\b[^>]*?:title="\$t\(\s*'([^']+)'/m, 1]
    modules << { 'file' => rel(f), 'imports' => imports, 'api_calls' => calls, 'urls' => urls,
                 'page_title_key' => title_key, 'page_title_ja' => title_key && resolve_ja.call(title_key),
                 'functions' => fns.reject { |_, v| v['api_calls'].empty? && v['calls'].empty? }, '_words' => words }
  end
  # Which functions of each imported file this file actually names.
  by_file = {}
  modules.each { |m| by_file[m['file']] = m }
  modules.each do |m|
    words = m.delete('_words')
    uses = {}
    m['imports'].each do |imp|
      t = by_file[imp]
      next unless t && t['functions'] && !t['functions'].empty?
      used = t['functions'].keys & words
      uses[imp] = used unless used.empty?
    end
    m['import_uses'] = uses unless uses.empty?
  end
  modules.each { |m| m['functions'] = nil if m['functions'] && m['functions'].empty? }
  modules.each { |m| m.delete('functions') if m['functions'].nil? }

  data['frontend_api'] = api
  data['frontend_modules'] = modules
  data
end

# ---------------------------------------------------------------------------
# Cross-file references (edges), computed after a class index exists
# ---------------------------------------------------------------------------

# Method names Ruby, ActiveRecord or plain objects use constantly. A model that
# happens to share one (Index, Status, Setting…) must not be matched by `.index`.
ASSOC_STOPWORDS = %w[
  index name names type types status statuses count first last find where order orders group groups
  update create delete destroy save value values data params errors error file files path paths size
  time date dates each list lists item items key keys text body title code codes state states sort
  result results page pages limit offset join joins select includes present blank empty
  settings setting option options config configs content contents detail details parent children
  history histories log logs message messages request response token tokens account accounts
].to_set.freeze

def attach_references(data)
  index = {}
  shorts = Hash.new { |h, k| h[k] = [] }
  %w[controllers models services jobs mailers lib].each do |bucket|
    (data[bucket] || []).each do |e|
      next unless e['class']
      index[e['class']] = [bucket, e['file']]
      short = e['class'].split('::').last
      shorts[short] << [bucket, e['file']] if short != e['class']
    end
  end
  # A bare `Client` or `Service` exists under dozens of namespaces. Guessing one
  # attaches an unrelated file to the screen, so a short name only resolves when
  # exactly one class carries it.
  shorts.each { |short, hits| index[short] = hits.first if hits.size == 1 && !index.key?(short) }

  # `current_company.categories.find(id)` names no class, yet it is how a Rails
  # controller most often reaches a model. Map association-style method names
  # (plural table name and singular) to the model file — only when unambiguous
  # and only for app/ models, so `.name` or `.count` never match anything.
  assoc_models = {}
  seen_assoc = Hash.new(0)
  (data['models'] || []).each do |m|
    next unless m['class'] && m['file'].start_with?('app/models/', 'packs/')
    snake = m['class'].split('::').last.gsub(/([a-z\d])([A-Z])/, '\\1_\\2').downcase
    [snake, m['table_name'], "#{snake}s", snake.sub(/y\z/, 'ies')].compact.uniq.each do |w|
      seen_assoc[w] += 1
      assoc_models[w] = m['file']
    end
  end
  assoc_models.delete_if { |w, _| seen_assoc[w] > 1 || w.length < 4 || ASSOC_STOPWORDS.include?(w) }

  %w[controllers models services jobs mailers lib].each do |bucket|
    (data[bucket] || []).each do |e|
      path = File.join(ROOT, e['file'])
      next unless File.exist?(path)
      src = strip_comments(read_file(path))
      # Words inside string literals ("Service unavailable") are not references.
      src = src.gsub(/"(?:\\.|[^"\\])*"/, '""').gsub(/'(?:\\.|[^'\\])*'/, "''")
      own = e['class']
      refs = constants_in(src).select { |c| index.key?(c) && c != own }
      e['references'] = refs.map { |c| index[c][1] }.uniq - [e['file']]
      e['references_classes'] = refs.uniq

      # Controllers: which classes each action reaches, so a screen can be told
      # apart from its siblings instead of inheriting the whole file's references.
      next unless bucket == 'controllers'

      bodies = method_bodies(src)
      rules = filter_rules(src)
      via_assoc = lambda do |body|
        body.scan(/\.([a-z][a-z0-9_]{3,})\b/).flatten.uniq.map { |w| assoc_models[w] }.compact
      end
      e['filter_references'] = rules.map do |name, only, except|
        next unless bodies.key?(name)
        fsrc = action_source(name, bodies, [])
        frefs = constants_in(fsrc).select { |c| index.key?(c) && c != own }
        { 'name' => name, 'only' => only, 'except' => except, 'references' => (frefs.map { |c| index[c][1] } + via_assoc.call(fsrc)).uniq - [e['file']] }
      end.compact
      e['action_references'] = {}
      (e['actions'] || []).each do |action|
        next unless bodies.key?(action)
        body = action_source(action, bodies, rules)
        arefs = constants_in(body).select { |c| index.key?(c) && c != own }
        e['action_references'][action] = (arefs.map { |c| index[c][1] } + via_assoc.call(body)).uniq - [e['file']]
      end
    end
  end
  data
end

# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

def full_scan(routes_table)
  data = {}
  data['packs'] = scan_packs

  if routes_table
    data['routes'] = parse_routes_table(routes_table)
    data['routes_files'] = [rel(File.expand_path(routes_table))]
  else
    parser = RoutesParser.new(ROOT)
    data['routes'] = parser.parse_entrypoints
    data['routes_files'] = parser.files_read.map { |f| rel(f) }
  end

  bucket_specs.each do |key, subdir, kind|
    data[key] = ruby_files(subdir).map { |f| build_for(kind, f) }
  end
  data['views'] = view_files.map { |f| build_view(f) }
  data['frontend_pages'] = frontend_files.map { |f| build_frontend(f) }

  attach_references(data)
  scan_frontend_calls(data)
  data
end

# ---------------------------------------------------------------------------
# Incremental scan
# ---------------------------------------------------------------------------

def incremental_scan(previous, since, routes_table)
  diff = git(ROOT, 'diff', '--name-status', "#{since}..HEAD")
  abort("cannot diff from #{since} (commit missing? rebased?). Re-run a full scan.") if diff.nil?

  changed = []
  diff.each_line do |line|
    parts = line.strip.split("\t")
    next if parts.size < 2
    status = parts[0]
    if status.start_with?('R')
      changed << ['D', parts[1]] << ['M', parts[2]]
    else
      changed << [status[0], parts[1]]
    end
  end

  data = previous
  touched = []
  routes_dirty = false
  packs_dirty = false

  changed.each do |status, relpath|
    routes_dirty = true if relpath =~ %r{\Aconfig/routes} || relpath =~ %r{\Apacks/[^/]+/config/routes}
    packs_dirty  = true if relpath =~ %r{\Apacks/[^/]+/package\.yml\z}

    spec = bucket_for_path(relpath)
    next unless spec
    bucket, kind = spec
    data[bucket] ||= []
    data[bucket].reject! { |e| e['file'] == relpath }
    next if status == 'D'

    abs = File.join(ROOT, relpath)
    next unless File.exist?(abs)
    entry = case kind
            when :view     then build_view(abs)
            when :frontend then build_frontend(abs)
            else                build_for(kind, abs)
            end
    data[bucket] << entry
    touched << relpath
  end

  data['packs'] = scan_packs if packs_dirty

  if routes_dirty
    if routes_table
      data['routes'] = parse_routes_table(routes_table)
      data['routes_files'] = [rel(File.expand_path(routes_table))]
    else
      parser = RoutesParser.new(ROOT)
      data['routes'] = parser.parse_entrypoints
      data['routes_files'] = parser.files_read.map { |f| rel(f) }
    end
  end

  attach_references(data)
  scan_frontend_calls(data)
  %w[controllers models services jobs mailers lib views frontend_pages].each do |b|
    (data[b] || []).sort_by! { |e| e['file'] }
  end
  [data, touched, routes_dirty]
end

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

head = (git(ROOT, 'rev-parse', 'HEAD') || '').strip
head = nil if head.empty?

previous = File.exist?(OUT) ? (JSON.parse(read_file(OUT)) rescue nil) : nil
mode = 'full'
touched = nil

if options[:since]
  abort("--since needs an existing scan at #{rel(OUT)}; run a full scan first.") unless previous
  data, touched, routes_dirty = incremental_scan(previous, options[:since], options[:routes_table])
  mode = 'incremental'
else
  data = full_scan(options[:routes_table])
end

meta = {
  'schema_version'  => SCHEMA_VERSION,
  'generated_at'    => Time.now.utc.iso8601,
  'commit'          => head,
  'previous_commit' => options[:since],
  'mode'            => mode,
  'root'            => rel(ROOT).empty? ? '.' : rel(ROOT),
  'routes_source'   => options[:routes_table] ? 'rails-routes' : 'static-parse',
  'files_reparsed'  => touched ? touched.size : nil
}

out = { 'meta' => meta }.merge(data)

require 'fileutils'
FileUtils.mkdir_p(File.dirname(OUT))
File.open(OUT, 'w') { |f| f.write(JSON.pretty_generate(out)) }

unless options[:quiet]
  counts = %w[packs routes controllers models services jobs mailers lib views frontend_pages]
           .map { |k| "#{k}=#{(out[k] || []).size}" }.join(' ')
  warn "[code-map] #{mode} scan -> #{rel(OUT)}"
  warn "[code-map] commit=#{head} #{counts}"
  warn "[code-map] reparsed #{touched.size} file(s)" if touched
  warn "[code-map] routes source: #{meta['routes_source']}"
end
