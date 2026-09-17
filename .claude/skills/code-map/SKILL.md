---
name: code-map
description: >-
  Read a Rails codebase and extract raw structural facts (routes, controllers,
  models, services, jobs, views, Packwerk packs, Vue pages, and the constant
  references between them) into a single machine-readable JSON file. This is
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

## Running it

```bash
.claude/skills/code-map/scripts/scan-code-map.sh --root .
```

`scan-code-map.sh` is a thin entrypoint over `scan_code_map.rb`, the same shape
`affected-tests.sh` uses over `script/affected_tests/*.rb`. The real work is in
Ruby because it parses the Rails routing DSL with a nesting stack and emits and
merges a nested 2 MB JSON document — bash driving `jq` per file would be slower
and far harder to read. Ruby is already a hard dependency of this repo and the
script uses stdlib only, so it runs on the host with **no bundler, no docker,
and without booting Rails**.

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
  --since "$(ruby -rjson -e "puts JSON.parse(File.read('.ai/code-map/raw/scan.json'))['meta']['commit']")"
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
