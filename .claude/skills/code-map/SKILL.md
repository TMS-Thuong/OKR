---
name: code-map
description: >-
  Read a codebase in any language and extract raw structural facts (routes and
  the handlers behind them, models, services, jobs, and the references between
  files) into a single machine-readable JSON file. Rails gets a dedicated deep
  reader (routes DSL, filters, views, Packwerk packs, Vue pages); every other
  stack — Laravel, Express, NestJS, Spring, Django, FastAPI, Flask, Go, ASP.NET,
  Ktor, Sinatra, axum, or an in-house framework via a route rule in the config —
  goes through the generic reader. This is
  step 1 of KR1 "impact map": it only reads and records — it does NOT group
  files into features and does NOT write human-facing documents. Use when asked
  to "scan the codebase", "read routes/controllers/models", "build the code
  map", "refresh the code map", or when a later step (impact analysis, doc
  generation, PR review scoping, test scoping) needs the structural dataset.
  Supports a fast incremental refresh via `--since <commit>`.
---

# Code map — step 1: read the code

## What this skill is for

KR1 ("impact map: Module/Screen ↔ Logic ↔ Code file") is three separate
skills. This one is the **first** and the only one that touches source files:

| Step | Skill | Input | Output |
|---|---|---|---|
| 1 | **code-map** (this one) | source tree | `.ai/code-map/raw/scan.json` — raw facts |
| 2 | impact analysis | `scan.json` | feature clusters, shared/hot files, reverse deps |
| 3 | doc generation | step-2 output | human-readable map |

Keeping the read step isolated is deliberate: KR2 (PR review by impact scope)
and KR3 (RSpec generation by impact scope) both consume the same dataset, so it
must be produced by one reusable step rather than baked into a doc generator.

**Hard boundary — do not cross it in this skill:**

- Do NOT infer "these 5 controllers are one feature". That is step 2.
- Do NOT write Markdown reports, tables, or diagrams. That is step 3.
- Do NOT edit application code.

If a request needs those, produce the scan first, then hand off.

## Two readers, one output

`scan-code-map.sh` picks the reader, and both write the **same `scan.json` schema**,
so steps 2 and 3 never need to know which one ran.

| Reader | When | How deep |
|---|---|---|
| `scan_code_map.py` (**rails**) | `config/routes.rb` and `app/controllers/` exist | Full routes DSL (`resources`, `namespace`, `member`), `before_action`, views and partials, Packwerk packs, Vue page → API calls |
| `scan_generic.py` (**generic**) | anything else | Routes from declarative rules, files linked by imports and class names |

Force one with `--scanner rails|generic`, or `"scanner": "generic"` in
`.claude/code-map.json`.

### How the generic reader works

1. **Files.** Every file whose extension is in `LANGS`, skipping vendored,
   generated and test code (`node_modules`, `vendor`, `dist`, `test/`,
   `*_test.go`, `*.spec.ts`, …). Narrow it with `scan.include` / `scan.exclude`
   globs in the config.
2. **Facts per file.** One tokenizer for every language blanks comments and
   string bodies without moving any character, then per-language regexes read the
   namespace, the classes (and what they extend), the functions and the imports.
3. **Links between files**, from three sources:
   - imports that resolve to a file: relative paths and `tsconfig` aliases
     (JS/TS, Ruby, Dart), dotted modules and `from pkg import submodule`
     (Python), fully-qualified names (Java, Kotlin, Scala, PHP `use`), the
     module path in `go.mod` (Go), `mod x;` (Rust);
   - class names used in the code, when exactly one file declares that name — or,
     when several do, the one nearest the caller if it is clearly nearest
     (`Features/Articles/List.cs` over `Features/Comments/List.cs`);
   - injected fields: `private final ArticleService articleService;`,
     `constructor(private articleService: ArticleService)`, `Type $name` — so a
     handler that calls `this.articleService.create()` still reaches the service.
4. **Routes**, from `ROUTE_RULES` in the script plus `routes.rules` in the config.
   A rule is either a *call* (`Route::get('x', 'A@b')`, `router.get('/x', h)`,
   `path('x', view)`, `r.GET("/x", h)`) or a *decorator* on the handler
   (`@GetMapping`, `@Get()`, `[HttpGet]`, `@router.get`), whose class-level
   annotation supplies the path prefix. Prefixes added in another file are
   followed too: FastAPI `include_router(prefix=)`, Flask
   `register_blueprint(url_prefix=)`, Express `app.use('/api', r)`, Gin
   `r.Group("/api")` passed into a register function.
5. **No routes at all** (a CLI, a worker, a library with a `main`): each entry
   point — `if __name__ == "__main__"`, `func main`, `static void main`,
   `package.json` `main` / `bin` — becomes one screen, so the map still has roots.

Built-in rules, each verified on the RealWorld app of that framework (19 API
endpoints, same spec everywhere) — every endpoint found, every handler resolved,
no unresolved controllers:

| Language | Frameworks |
|---|---|
| PHP | Laravel (`Route::get/match/resource/apiResource`), Symfony `#[Route]` |
| JS / TS | Express, Koa-router, Fastify, Hono; NestJS decorators |
| Java / Kotlin | Spring (`@GetMapping`, `@RequestMapping`), Ktor |
| C# | ASP.NET Core attributes and minimal APIs |
| Python | FastAPI, Flask, Starlette; Django `path/re_path/url` with class-based views expanded to their `get/post/list/retrieve…` methods; DRF routers and viewsets |
| Go | net/http, Gin, Echo, Chi, Fiber, Gorilla |
| Ruby | Sinatra (Rails has its own reader) |
| Rust | axum `.route()`, actix `#[get]` |

Languages read for links: Ruby, Python, JavaScript/TypeScript (+ Vue, Svelte),
PHP, Java, Kotlin, C#, Go, Rust, Swift, Dart, Scala. Another language is one
row in `LANGS`.

### A project whose routes the generic reader does not find

This is expected for an in-house framework or an unusual style. **Do not
give up and do not edit the script** — write a rule into the project's
`.claude/code-map.json`:

1. Run the scan and read the summary line: `routes=0` or a count far below what
   the app obviously has means the rules did not match.
2. Find how the project declares one route: `grep -rn` for a URL you know
   (`"/orders"`) and read the lines around it.
3. Write a rule. Named groups: `method`, `path`, and either `handler` (a name
   such as `ordersHandler.list`) or `controller` + `action`. Leave out
   `handler` and the last argument of the call is taken as the handler.

   ```json
   {
     "routes": {
       "rules": [
         { "name": "mount", "lang": "javascript", "kind": "call",
           "pattern": "\\bmount\\(\\s*\"(?P<method>[A-Z]+)\\s+(?P<path>/[^\"]*)\"\\s*,\\s*(?P<handler>[\\w.]+)" }
       ]
     }
   }
   ```

   Rule keys: `name`, `lang` (one or a list, from `LANGS`), `kind`
   (`call` | `decorator`), `pattern` (Python `re`, matched against the code
   with comments removed and strings kept), optional `files` (regex on the
   path), `prefix` (decorator on the class that gives the path prefix),
   `file_prefix`, `methods_in`, `inline` (the handler is the `{ … }` block
   right after the match). `routes.disable: ["express"]` turns a built-in rule
   off when it matches something that is not a route.
4. Re-run and compare the route count and a few `controller#action` names
   against the code. Report the rule you added.

## Running it

```bash
.claude/skills/code-map/scripts/scan-code-map.sh --root .
```

`scan-code-map.sh` is a thin entrypoint over `scan_code_map.py`. The real work
is in Python because it parses the Rails routing DSL with a nesting stack and
emits and merges a nested 4 MB JSON document — bash driving `jq` per file would
be slower and far harder to read. It uses the standard library only and runs on
Python 3.8+, so it needs **no pip, no bundler, no docker, and does not boot
Rails**. The KR2 skills already need `python3`, so KR1 adds no new dependency.

It reads Ruby source with regexes, and Ruby's regex rules differ from Python's in
one way that matters here: Ruby's `\w` is ASCII-only but its `\b` treats Japanese
as word characters. `R()` / `_rubyish()` in the script reproduce that, so a class
name glued to a Japanese comment is read the same way Rails developers expect.

Options:

| Flag | Meaning |
|---|---|
| `--root DIR` | Repository root (default `.`) |
| `--out FILE` | Output path (default `<root>/.ai/code-map/raw/scan.json`) |
| `--since SHA` | Incremental refresh — re-read only files changed since `SHA` |
| `--routes-table FILE` | Use saved `rails routes` text output as the authoritative route list |
| `--quiet` | Suppress the stderr summary |

It prints a summary to stderr and writes JSON to stdout's target file. Read the
summary line to confirm the scan is sane before using the data.

Reference numbers on `zaico_web`: full scan ~7–18s and ~2 MB of JSON — 6 packs,
1542 routes, 462 controllers, 341 models, 708 services, 259 lib classes,
964 views, 1207 frontend page files. An incremental refresh over 8 commits
(41 changed files) takes ~3s.

Downstream: the `impact-analysis` skill (KR1 step 2) consumes this output.

## Refresh strategy — full vs incremental

**This skill owns the refresh decision. Steps 2 and 3 always regenerate in
full.** The reasoning matters, so follow it rather than re-deciding per task:

- Reading source is the only expensive step. Steps 2 and 3 read a small JSON
  file, so a full regenerate there is cheap and deterministic — no stale-merge
  bugs in the human-facing output.
- Only this step has a valid anchor for "what changed": the git commit that was
  read. A doc generator would have to duplicate this step's file-classification
  logic to know what went stale.
- Merging at the data layer is keyed on `file`, which is exact. Merging at the
  document layer is prose surgery, which is not.

So:

```bash
# first time, or whenever meta.commit is far behind HEAD
.claude/skills/code-map/scripts/scan-code-map.sh --root .

# routine refresh after pulling / after a branch of work
.claude/skills/code-map/scripts/scan-code-map.sh --root . \
  --since "$(python3 -c "import json; print(json.load(open('.ai/code-map/raw/scan.json'))['meta']['commit'])")"
```

The previous commit is recorded in `meta.commit`, so an incremental refresh
never needs the user to remember a SHA.

**When a full scan is required anyway:**

- No existing `scan.json` (the script aborts and tells you).
- `meta.commit` no longer exists in the repo — rebased branch, fresh clone.
  The script aborts with that message; do not retry with `--since`.
- `meta.schema_version` differs from the script's `SCHEMA_VERSION`.
- After a large refactor, or roughly every 20–30 incremental runs — see the
  known limitation below.

## Output

Single file, `.ai/code-map/raw/scan.json`:

```
meta            schema_version, generated_at, commit, previous_commit, mode,
                routes_source, files_reparsed
packs[]         name, path, enforce_dependencies, enforce_privacy, dependencies
routes[]        method, path, controller, action, controller_class,
                source_file, source_line, source, raw
controllers[]   file, pack, class, kind, superclass, class_line, actions,
                before_actions, includes, renders, loc, references,
                references_classes
models[]        file, pack, class, kind, superclass, table_name, associations,
                scopes, enums, includes, public_methods, loc, references
services[]      file, pack, class, kind, superclass, public_methods, includes,
                loc, references
jobs[] lib[]    same shape as services
views[]         file, pack, controller_hint, action_hint, partial, renders, loc
frontend_pages[] file, pack, kind, page_key, loc   (app/ and packs/*/app/)
```

`references` is the edge list step 2 needs: for every Ruby file, the other
scanned files whose constants appear in its source. `references_classes` keeps
the constant names for cases where a name maps to several files.

`controller_hint` / `action_hint` on views, plus `controller_class` on routes,
are the join keys that let step 2 walk
`route → controller → action → view / model / service` without re-reading code.

`page_key` on a frontend page is the Vue half of that walk. The layout renders
`<body data-component="<controller path>/<action>">` from
`ApplicationHelper#vue_page_component_path_by_controller`, and `app.ts` loads
`pages/<that>.vue`. Recording the key here lets step 2 attach a screen to its
Vue page by convention rather than by guesswork.

## Known limitations — state these when reporting results

**Generic reader** (everything that is not Rails):

- Links are name-based: two classes with the same name far apart are dropped
  rather than guessed, and calls made through reflection, string names or a
  DI container configured in XML/YAML are invisible.
- A route whose path is built at runtime (`"/" + name`) or whose prefix comes
  from a constant (`prefix=API_PREFIX`) keeps only the literal part.
- No views, templates or frontend-to-API links — those are Rails-reader only.
- `--since` is accepted but the generic reader always scans in full (about
  4 seconds per 1,000 files; zaico's 5,900 files take 24 s).

**Rails reader:**

- **Routes are statically parsed**, not loaded from Rails. `resources` blocks
  are expanded; `namespace` / `scope` / `draw` are followed, including their
  `module:` and `controller:` options; `devise_for` is expanded for the modules
  whose controller the app overrides; shorthand routes with no `to:` are
  resolved from the enclosing resource, scope, or namespace. Still missed:
  `use_doorkeeper` and other engine-generated routes (which is why
  `Oauth::AuthorizationsController` looks unreachable), `concern`, `direct` /
  `resolve`, conditional routes. Route helper names are absent. When exact routes matter, capture
  `bin/rails routes` output once and pass `--routes-table FILE`; `meta.routes_source`
  records which path was used.
- **`references` is name-based**, computed by intersecting capitalised tokens
  with the scanned constant index. It over-reports on generic names and
  under-reports on dynamic dispatch (`const_get`, string-built class names).
  Treat it as a candidate edge list for step 2 to refine, not as ground truth.
- **Incremental runs do not re-scan unchanged files' references.** A brand-new
  class will not appear in the `references` of files that were not themselves
  touched. This is why a periodic full scan is required.
- **`public_methods` / `actions` stop at the first bare `private`/`protected`.**
  Methods after a re-opened public section are not listed.
- Only `app/` and `packs/*/app/` are scanned. `lib/` at the repo root, engines,
  and `config/initializers` are out of scope for schema v1.

## Output location

`.ai/` is a generated-artifact directory and is git-ignored. Never commit
`scan.json`; regenerate it instead.

## Per-action references

`controllers[].action_references` maps each action to the files *that action* reaches:
its own body, the `before_action` / `around_action` filters that apply to it (honouring
`only:` / `except:`), and up to three hops of private helpers it calls. Method bodies
are found by indentation, which is exact on this rubocop-formatted repo.

`references` stays the whole-file list. Use `action_references` when the question is
"what does *this screen* run" — `inventory_imports#show` reaches nothing, `#execute`
reaches six models, and the file-level list cannot tell them apart.

Methods pulled in from an included concern are not followed into the concern file.

## Mailers, filters, association access

- `mailers` is scanned like services (`app/mailers`, `packs/*/app/mailers`), so mail sent
  from an action shows up as a side effect.
- `controllers[].filter_references` lists each `before_action` / `around_action` with its
  `only:` / `except:` and what it reaches — used for actions inherited from a parent
  (Devise `sessions#new`), which have no body in the file.
- A model reached as `current_company.categories` counts as a reference to `Category`:
  association-style method names map to model files when unambiguous, minus a stoplist of
  ordinary method names (`index`, `status`, `name`…).
- A bare short constant (`Client`, `Service`) only resolves when exactly one class has it.
