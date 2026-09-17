---
name: code-map-doc
description: >-
  Turn the impact graph into one self-contained HTML page: a tree where a screen
  is the root and its routes, views, related screens and reached code files hang
  off one branch per question — entered from, handled by, touches data, also
  affected — with a module filter, a search box and a detail panel.
  Screens are named in Vietnamese from a dictionary instead of being shown as
  controller#action. This is step 3 of KR1 "impact map" — it reads only
  impact.json, never source files, and performs no analysis. Use when asked to
  "build the code map", "open the impact map", "show me what login touches", or
  to refresh the map after a code change.
---

# Code map doc — step 3: render it

## Position in KR1

| Step | Skill | Input | Output |
|---|---|---|---|
| 1 | code-map | source tree | `.ai/code-map/raw/scan.json` |
| 2 | impact-analysis | `scan.json` | `.ai/code-map/analysis/impact.json` |
| 3 | **code-map-doc** (this one) | `impact.json` | `.ai/code-map/doc/index.html` |

**Hard boundary:** every number on the page comes from `impact.json`. This skill
reads no source files, computes no relatedness, and derives no findings. If the
page needs a fact the analysis does not contain, extend step 1 or step 2 — the
moment this skill starts computing, the page stops being reproducible.

## Running it

```bash
.claude/skills/code-map-doc/scripts/build.rb --open              # ~1 s
.claude/skills/code-map-doc/scripts/build.rb --refresh --open    # ~10 s, re-reads the code
```

Or `/map` — see `.claude/commands/map.md`.

Ruby stdlib only. **No npm, no toolchain, no installed app.** The host runs
Ruby 2.6, so the script stays on 2.6-compatible syntax.

| Flag | Meaning |
|---|---|
| `--refresh` | Run steps 1 and 2 first (incremental scan from `meta.commit`) |
| `--open` | Open the page when it is written |
| `--lang vi\|ja` | UI language (default `vi`) |
| `--default-screen ID` | Screen selected on load (default `users/sessions#new`) |
| `--config FILE` | Project config (default `.claude/code-map.json`) |
| `--impact FILE` / `--out FILE` / `--root DIR` / `--quiet` | Paths and noise |

## The three files

```
SKILL.md
scripts/build.rb      names the screens, builds the payload, injects it
assets/app.html       pre-built bundle, 399 KB, not hand-edited
```

`assets/app.html` is a React + `react-force-graph-2d` bundle with a
`/*__DATA__*/` placeholder. `build.rb` substitutes the dataset into it, which is
why nobody needs a toolchain to get a page. The canvas, the zoom and the pan are the
library's job; roughly 380 lines of glue are ours. There is one layout, a
**tree**: every view is a star around one centre, so the arms are grouped under
branch nodes and pinned by depth (x = level, y = row, a parent at the midpoint
of its children), with elbowed links so every line arrives perpendicular. The
force and radial layouts were removed — a second way to draw the same data is
one more thing to explain.

The branches are named after the question, not the node type: *Vào bằng đường
dẫn nào · Entered from*, *Bấm vào thì code nào chạy · Handled by*, *Đụng tới dữ
liệu / nghiệp vụ nào · Touches data*, *Sửa vào đây thì màn nào ảnh hưởng · Also
affected*. Edit `STRINGS[...]['roles']` to change them.

Two tabs: the tree and the module list. Step 2's findings (unresolved
controllers, hubs, unreached files) are in `summary.md`, not in the page.

**Everything worth tuning lives in `build.rb`, not in the bundle:**

| Change | Where | Rebuild needed |
|---|---|---|
| Data after a code change | `--refresh` | no |
| A wrong Vietnamese name | `LABELS` in `build.rb` | no |
| Colours, node sizes, tree spacing (`UI['tree']`), branch names (`roles`), files drawn per screen | `UI` / `STRINGS` in `build.rb` | no |
| Panels, tabs, interaction | `.ai/code-map/ui/src/App.tsx` | **yes** |

Rebuilding the bundle (only for that last row):

```bash
cd .ai/code-map/ui && npm install && npm run build
cp dist/index.html ../../../.claude/skills/code-map-doc/assets/app.html
```

The Vite project lives under `.ai/` on purpose: it is a build-time tool, not
part of the skill, and `.ai/` is git-ignored.

## Reusing this on another project

The scripts know Rails, not zaico. Everything repo-specific is in one file,
`.claude/code-map.json`:

| Key | What it does |
|---|---|
| `labels` | the screen-name dictionary (`overrides`, `namespaces`, `actions`, `verbs`, `nouns`, `terms`) |
| `features` / `other_area` | feature areas as `{name, match}`, first match wins; everything else lands in `other_area` |
| `areas_en` | English name per area — the only labels with no original in the code |
| `hidden_controllers` | regexes for controllers kept off the overview (zaico: `\Aapi/`, `zaico_admin`) |
| `default_screen` | screen selected on load |

To use these skills in another Rails repo: copy `.claude/skills/code-map`,
`impact-analysis` and `code-map-doc`, then write that JSON. **Every key is
optional.** With no config file the map still builds — screens keep their
`controller#action` names and every controller lands in one area. Adding
dictionary entries raises the named share; nothing else has to change, and no
script is edited.

Non-Rails projects are out of scope: step 1 parses the Rails routing DSL, and
screens are defined as `controller#action`.

## Names

`controller#action` is precise and unreadable, so screens are named from the
`LABELS` table at the top of `build.rb`. The rule is visible on purpose:
**{verb} {noun}**, with `overrides` winning outright for names everyone already
says (`users/sessions#new` → `Đăng nhập`).

The action vocabulary has a long tail — 427 of the 477 distinct actions appear
on one or two screens — so an action with no entry of its own is composed from
its leading `verb` plus the `terms` after it, and `join_phrase` drops whatever
the verb and the noun repeat (`download_account_statement` on
`account_statements` would otherwise end "sao kê sao kê").

**A segment with no entry keeps its original name.** Inventing a translation
would be worse than showing the truth.

On `zaico_web` that covers **1137 of 1470 screens (77%)**. Screens outside that
carry a `tên tự sinh` tag in the detail panel. To raise it, add to `nouns` /
`terms` / `overrides` and re-run — no code change, no rebuild.

## Known limitations — repeat these when sharing the map

- **Routes are statically parsed**, not loaded from Rails (`meta.routes_source`
  records which path was used). Exotic DSL can be missed.
- **References are name-based**, so generic constant names over-report and
  dynamic dispatch under-reports.
- **Relatedness comes from shared non-hub files**, not from anyone's idea of a
  feature. Reliable within a controller, good across closely coupled ones; a low
  score is weak evidence either way.
- **The 23% of names that are not dictionary-backed are machine-composed** and
  read oddly in places.

## Output location

`.ai/` is a generated-artifact directory and is git-ignored. Never commit
`index.html`; regenerate it.

## Page layout

Three levels, entered from the left navigation or by clicking cards:

1. **Overview** — the large feature areas (Tồn kho, Bán hàng…).
2. **Area** — its functions, one card per controller, with counts of actions, services,
   models and views.
3. **Function** — the Rails flow of that whole function, all actions merged:
   actions (route) → controller#action → services / lib (one column per call depth) →
   side effects (jobs, mailers, API / AWS clients) → models → views with their partials
   and the Vue page. Clicking an action lights only the path it runs; clicking a file
   lights what calls it and what it calls, and the side panel traces **backwards** —
   every function whose flow passes through that file, grouped by area.

Cards size to their text; shared core files are dashed and not expanded. File names are
shown in Vietnamese from `.claude/code-map.json` where the dictionary can name them.
