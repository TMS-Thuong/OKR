---
name: impact-analysis
description: >-
  Correlate the raw code-map dataset into an impact graph: which code files each
  screen (controller#action) actually reaches, which files are shared across many
  screens, which files no screen reaches, and which routes point at controllers
  that do not exist. This is step 2 of KR1 "impact map" — it reads only
  scan.json, never source files, and does not write human-facing documents.
  Use when asked "what does this screen touch", "which screens use this file",
  "what is shared across the app", "what breaks if I change X", or when step 3
  (doc generation), KR2 (PR review scoping) or KR3 (test scoping) needs the
  impact graph.
---

# Impact analysis — step 2: compare and correlate

## Position in KR1

| Step | Skill | Input | Output |
|---|---|---|---|
| 1 | code-map | source tree | `.ai/code-map/raw/scan.json` |
| 2 | **impact-analysis** (this one) | `scan.json` | `.ai/code-map/analysis/impact.json` |
| 3 | doc generation | `impact.json` | human-readable map |

**Hard boundary:** this skill reads no source files and edits nothing. Every
fact comes from `scan.json`. If the answer needs something the scan does not
contain, fix or extend step 1 — do not read source here, or the two steps drift
apart and results stop being reproducible.

It also writes no Markdown, tables, or diagrams. That is step 3.

## Running it

```bash
.claude/skills/impact-analysis/scripts/analyze-impact.sh
```

Requires `.ai/code-map/raw/scan.json`. If it is missing or stale, run the
`code-map` skill first. The analyser aborts when `scan.json` carries a
`schema_version` it does not expect.

| Flag | Meaning |
|---|---|
| `--scan FILE` | Input scan (default `.ai/code-map/raw/scan.json`) |
| `--out FILE` | Output (default `.ai/code-map/analysis/impact.json`) |
| `--max-depth N` | Reference hops followed from a controller (default 3) |
| `--hub-threshold N` | Fan-in above which a file is a hub (default 40) |
| `--quiet` | Suppress the stderr summary |

**Always regenerates in full — there is no incremental mode, by design.** The
input is one small JSON file, the run takes under a second, and a full rebuild
cannot go stale. Incremental refresh belongs to step 1, which is the only step
that pays to read source.

## The two modelling decisions

Both change the numbers, so state them when reporting results.

**A screen is a `controller#action`, not a route.** Several routes commonly hit
the same action (HTTP method variants, aliases, legacy paths); collapsing them is what
makes "screen" mean a screen. Every route that reaches an action is still listed
under `screens[].routes` with its `source_file:source_line`.

**Hub files are recorded but not traversed through.** In any Rails app,
`ApplicationRecord`, `Company` and `BaseService` are named by almost everything;
following references through them makes every screen reach the whole codebase
and the output becomes worthless. A file whose fan-in reaches `--hub-threshold`
is marked `hub: true`, still counted as used by the screen that reaches it, but
the walk stops there. Combined with `--max-depth`, this keeps a screen's file
list to the order of tens rather than thousands.

Raising `--max-depth` or `--hub-threshold` widens coverage and lowers signal.
Change one at a time and compare `stats.avg_files_per_screen`.

## Output

`.ai/code-map/analysis/impact.json`:

```
meta          scan_commit, scan_mode, routes_source, max_depth, hub_threshold,
              screen_unit
stats         screens, hub_files, screens_with_vue_page, files_reached,
              files_unreached, unresolved_controllers, framework_routes,
              avg_files_per_screen
packs         per-pack screen and file counts
hubs[]        file, raw_fan_in            (sorted, most-referenced first)
screens[]     id, controller, action, controller_class, controller_file, pack,
              action_defined, routes[], views[], vue_page, code_files[],
              code_file_count
shared_files[] file, bucket, screen_count, screen_ratio, hub, raw_fan_in,
              screens[] (first 20), screens_truncated
unreached_files[]        file, bucket, class, pack
unresolved_controllers[] controller class names no scanned file provides
```

`screens[].code_files[]` carries `depth` (hops from the controller) and `hub`,
so a consumer can weight a direct dependency differently from a distant one.

`screens[].views[]` includes partials reached through `render`, resolved
recursively, so a shared partial shows up under every screen that renders it.

`screens[].vue_page` is the Vue component the screen mounts, joined on the
`page_key` step 1 records. On `zaico_web` 172 of 1279 screens have one; the rest
are ERB-only or API endpoints, which is expected rather than a gap.

`screens[].action_defined` is false when a route names an action the controller
does not define — usually an inherited or generated action, occasionally a
genuinely broken route.

## Reading the results

- **`shared_files` sorted by `screen_count` is the "used in many places" answer**
  KR1 asks for. Filter out `hub: true` first — hubs are shared by construction
  and say nothing interesting.
- **`unresolved_controllers` is a real finding, not a defect of this skill.** On
  `zaico_web` all 13 were verified by hand as routes pointing at controllers
  that do not exist: `app/controllers/api/v1/migrations/` (10 routes),
  `api/credit_cards_controller.rb`, `charges_controller.rb`,
  `live_chats_controller.rb`. Routes handled by Rails or a gem are excluded from
  the list so they do not bury the real finding; `stats.framework_routes` counts
  them, so the number is accounted for rather than silently dropped.
  Still verify a sample by hand: the same symptom appears when step 1's route
  parser mis-resolves a namespace.
- **`unreached_files` is NOT dead code.** Jobs, rake tasks, console-only helpers
  and anything invoked dynamically have no route to reach them. Treat it as a
  list to review, and say so.

## Known limitations — state these when reporting results

- **Accuracy is capped by `scan.json`.** `meta.routes_source` is copied through:
  with `static-parse`, routes come from reading `config/routes.rb` rather than
  from Rails, so a missed namespace becomes a wrong screen here. `references`
  are name-based, so a screen's file list over-reports on common names and
  misses dynamic dispatch.
- **Depth and hub limits are heuristics**, not the true dependency set. A file
  omitted at depth 4 is not proven unreachable.
- **View matching is convention-based** (`app/views/<controller>/<action>.*`).
  Templates rendered by an explicit non-conventional path are only picked up
  when they appear in the controller's or a view's `render` list.
- **Non-route entry points are not modelled**: jobs, rake tasks, mailers and
  Lambda processors have no screen, so their dependencies are absent from
  `screens[]` and their files land in `unreached_files`.

## Call flow per screen

`screens[].flow` is who-calls-whom for that one action, as a tree:

```json
{ "file": "…/internal_ordering_util.rb", "bucket": "services",
  "calls": [ { "file": "…/settings/get_service.rb", "bucket": "services", "calls": [ … ] } ] }
```

Roots are the action's `action_references`. Services, jobs, lib and concerns are
expanded along their own references; models are leaves (the story ends at "which data").
Hub files are listed with `"hub": true` but not expanded. A file appears once, at its
shallowest position. Plain superclasses (ApplicationRecord, BaseService…) and
`errors.rb` exception holders are left out as plumbing.

`code_files` is unchanged — the flat whole-controller reach KR2's blast radius reads.

## View tree per screen

`screens[].view_tree` is the action's own views with the partials each one renders
nested under it (`{file, renders:[…]}`), up to four levels. `views` stays the flat list.
An inherited action's flow falls back to the filters that apply to it — never to the
whole controller's references.
