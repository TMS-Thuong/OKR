---
name: pr-diff
description: >-
  Read the code a pull request actually changed, and record it as a machine-readable
  dataset: which files changed, which line ranges changed, and which Ruby class /
  module / def each changed line sits inside. This is step 1 of KR2 "PR review by
  blast radius" — it makes no judgement about whether the change is good, and it
  does not compare anything against the impact map. Use when asked "what did this
  PR change", "which methods were touched", "read the diff", or when a later KR2
  step (cross-checking against KR1's impact.json, or writing the review report)
  needs the changed set.
---

# pr-diff — step 1 of KR2: read what changed

## Position in KR2

| Step | Skill | Input | Output |
|---|---|---|---|
| 1 | **pr-diff** (this one) | git history / working tree | `.ai/code-map/raw/pr-diff.json` |
| 2 | `blast-radius` | `pr-diff.json` + KR1 `impact.json` | which screens the change can reach |
| 3 | `pr-check` | steps 1–2 + source | breakages, duplication, rule violations |
| 4 | review report | step 3's output | risk-ranked report |

KR1 answers "screen → files". KR2 walks it backwards: "these files changed →
which screens are at risk". That reversal only works if step 1 records the change
in the same vocabulary KR1 uses — hence `qualified` names like
`Web::ItemsController#create`, which step 2 matches against KR1's `controller#action`.

## Hard boundary

This skill **describes**, it does not **judge**. No "this looks risky", no
"missing test", no style opinions. Those come from later steps that have the
impact map in hand; a judgement made here would be made blind.

It reads source, but **only files that appear in the diff**. It never follows a
reference out to another file — that walk is KR1's job, already done, and
redoing it here would let the two drift apart.

It writes no Markdown and no tables. That is step 4.

## Running it

```bash
.claude/skills/pr-diff/scripts/scan-pr-diff.sh            # PR-equivalent (default)
.claude/skills/pr-diff/scripts/scan-pr-diff.sh --working  # not yet committed
.claude/skills/pr-diff/scripts/scan-pr-diff.sh --staged   # git add'ed, not committed
.claude/skills/pr-diff/scripts/scan-pr-diff.sh --pr 8415  # attach PR title/state via gh
.claude/skills/pr-diff/scripts/scan-pr-diff.sh --base v1.2.3
```

Needs Python 3 and git, nothing else. `gh` is only consulted for `--pr`, and its
absence degrades to "diff without PR metadata", never an error.

### Which range, and why merge-base

Default is everything the branch added since it forked from master:

```
git merge-base origin/master HEAD  ..  HEAD
```

**Do not substitute `git diff master HEAD`.** Once master moves ahead, that form
reports other people's commits as if this branch deleted them, and every later
step inherits the false positives. The script resolves the fork point itself and
falls back through `origin/master → origin/main → master → main`; if none exist
(shallow clone, unusual remote), pass `--base` explicitly.

`--working` and `--staged` exist so a change can be checked *before* it is pushed
— that is the cheapest moment to find out the edit lands in a file 400 screens
share. They are not substitutes for the default: both go empty the moment you
commit, so using them to review a PR would silently skip almost all of it.

## Output

`.ai/code-map/raw/pr-diff.json`:

| Key | Meaning |
|---|---|
| `meta` | mode, resolved `base`/`head` sha, branch, `pr` block when `--pr` was used |
| `stats` | `files_changed`, `files_added/modified/deleted/renamed`, `lines_added`, `lines_removed`, `symbols_touched` |
| `files[]` | `path`, `old_path`, `status`, `lang`, `binary`, `added`, `removed`, `symbols` |

`added` and `removed` are `[{from, to}, …]`, both ends inclusive:

```json
"added":   [{ "from": 74, "to": 78 }],
"removed": [{ "from": 74, "to": 74 }]
```

**They are in different coordinate systems.** `added` numbers lines in the
post-change file; `removed` numbers lines in the pre-change file. Matching numbers
across the two mean nothing — a removed line has no position in the post-change
file, which is exactly why the two cannot be merged into one list.

There is no per-file line total, because counting `to - from + 1` gives it back
exactly. The totals in `stats` are summed that way.

An edit reads as a removal plus an addition: git has no notion of a changed line.
`+5 -1` above is one old line replaced by five new ones.

`symbols` holds the qualified name of every definition whose body overlaps a changed
range, innermost and outermost both listed, grouped by `class` / `module` / `def` /
`new_def`:

```json
"symbols": {
  "class":   ["Web::ItemsController"],
  "def":     ["Web::ItemsController#create"],
  "new_def": ["Web::ItemsController#duplicate"]
}
```

**`new_def` did not exist at the base commit; `def` did.** The distinction is the
difference between reach and danger. A file every screen loads scores `critical` in
step 2 on reach alone, but a change that only *adds* methods cannot break a single
existing caller — nothing calls them yet. Only step 1 can tell the two apart, since
only step 1 has both versions of the file in hand; by step 3 the information is gone
unless it is recorded here.

The pre-change file is read from the base commit (`HEAD` in `--working` /
`--staged`). An added file is all `new_def` without a second read. A deleted file
gets neither label — "new" is meaningless for something that no longer exists — so
its methods stay under `def`.

**Step 2 matches on `def` and `new_def`.** A screen's key is `Class#action`, so a
bare class name can never match one; grouping lets the consumer read the two def
lists instead of filtering. The other groups are for a human reading the report.

`self.` methods qualify with a dot (`Foo.build`), instance methods with a hash
(`Foo#build`). A file edited outside any method — a new association, a constant —
has no `def` group at all, only `class`. That is the honest answer, not an empty
result: something in the class body did change.

A group that did not occur is absent, so read with a default of `[]`. A file with no
symbols at all is `{}`.

Only the name is kept. The definition's own line bounds are what `enclosing()` tests
the changed ranges against while scanning, but they say nothing a reader needs
afterwards: `added` already points at the changed lines, and more precisely.

## Ruby only, on purpose

`symbols` is populated for `.rb` / `.rake` and nothing else. `meta.symbol_langs`
records this so a later step can tell "no symbols" from "not analysed".

Ruby is safe to parse by indentation here because this repo's Ruby is rubocop-formatted,
so matching `end` against the opening indent gives exact body bounds (verified against
`app/models/inventory.rb`, a 4400-line file, boundaries landed on the right lines).
Vue and TS have no such guarantee — brace style varies, and an indentation guess there
would attach the *wrong* function name to a change. A later step would then report
risk for a function nobody touched, which is worse than reporting none: KR1 only maps
frontend down to `vue_page` anyway, so file-level is the honest resolution.

Everything else (`.erb`, `.vue`, `.ts`, `.yml`, `.sql`, `.md`, binaries) is still
recorded at file level with full `added` / `removed` ranges.

## Things that bite

- **Renames.** `--find-renames` is on, so a moved file arrives as `status: renamed`
  with `old_path` set, not as delete+add. Step 2 must match on `old_path` when
  looking the file up in KR1's map, since the map was built before the rename.
- **Deleted files** read their symbols from the base commit. A deletion has no
  post-change content, and skipping it would hide the highest-risk change there is.
- **`added` is post-change, `impact.json` is a snapshot.** If KR1's map is older
  than the branch, line numbers still line up (they are per-file), but a file added
  by this branch will be absent from the map. That is expected, and step 2 should
  report it as `new_file` rather than `unused`.
- **A pure insertion has an empty `removed`, a pure deletion an empty `added`.**
  Read both with a default of `[]`.
- **`--unified=0` is used**, so the ranges cover genuinely changed lines only, with no
  context padding. A 3-line edit spread over a file yields 3 separate ranges, not one.
- **Deleted files carry their lines in `removed`**, and symbol matching uses that list,
  since the file being read is the pre-change one.
- **`stats` is a plain counter map**, so a status that never occurred is simply
  absent — read it with a default of 0, not with `fetch`.

## Rebuilding

Nothing is cached; every run re-reads git. Re-run it after each push. If output
looks stale, check `meta.head_sha` against `git rev-parse HEAD` before suspecting
the script.
