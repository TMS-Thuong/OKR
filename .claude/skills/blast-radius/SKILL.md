---
name: blast-radius
description: >-
  Cross-check the code a pull request changed against KR1's impact map, and record
  the blast radius as a machine-readable dataset: which screens can reach each
  changed file, which changed files are hubs the whole app shares, which screens
  were rewritten outright, and which changed files the map knows nothing about.
  This is step 2 of KR2 "PR review by blast radius" — it reads only pr-diff.json
  and impact.json, never source and never git, and it makes no judgement about
  whether the change is correct. Use when asked "what does this PR put at risk",
  "which screens does this change reach", "is this touching something shared", or
  when the review-report step needs the risk-ranked changed set.
---

# blast-radius — step 2 of KR2: match the change against the map

## Position in KR2

| Step | Skill | Input | Output |
|---|---|---|---|
| 1 | `pr-diff` | git history / working tree | `.ai/code-map/raw/pr-diff.json` |
| 2 | **blast-radius** (this one) | `pr-diff.json` + KR1 `impact.json` | `.ai/code-map/analysis/blast-radius.json` |
| 3 | `pr-check` | `pr-diff.json` + `blast-radius.json` + source | breakages, duplication, rule violations |
| 4 | review report | `pr-check.json` | risk-ranked report |

KR1 answers "screen → files". This walks it backwards: "these files changed →
which screens are at risk". The reversal is a pure lookup over what KR1 already
published.

**Invert `screens[]`, not `shared_files[]`.** `shared_files[]` looks like the
ready-made inverted index and is not one: its `screens` list is cut off at the
first 20 entries, which KR1 declares with `screens_truncated`. Only `screen_count`
survives the cut. Reading the list instead of rebuilding it under-reports the
blast radius by exactly the files that matter most — the 358 truncated entries are
the most widely shared files in the repo (`application_record.rb` reaches 1189
screens, `company.rb` 925).

The index is rebuilt here from `screens[]`, unioning three fields per screen:
`code_files[].file`, `views[]`, and `vue_page`. `controller_file` needs no separate
pass, arriving as `code_files` depth 0. Verified against all 2338 `shared_files`
entries: every `screen_count` matches.

## Hard boundary

This skill **matches**, it does not **judge**. No "this looks risky to me", no
"missing test", no style opinions, no reading the changed code. It answers one
question — *how far can this change be felt* — and hands the answer to step 3,
which is the step that opens the source and forms an opinion.

It reads two JSON files. It does not run `git`, does not read source, and does not
re-derive anything KR1 already computed. If the two inputs disagree, it reports the
disagreement rather than silently picking a side.

## Running it

```bash
.claude/skills/pr-diff/scripts/scan-pr-diff.sh              # step 1 first
.claude/skills/blast-radius/scripts/analyze-blast-radius.sh # then this
```

Options: `--diff PATH`, `--impact PATH`, `--out PATH`, `--root PATH`.
Needs Python 3 and nothing else. Runs in well under a second — it is a hash lookup per
changed file, not a graph walk.

`pr-diff.json` must come from a matching major version of step 1 — the run aborts
rather than misread an older shape.

If `impact.json` is missing, run KR1 first
(`.claude/skills/impact-analysis/scripts/analyze-impact.sh`). The map does not need
to be regenerated per PR; it only needs to be newer than the last structural change.

## Output

`.ai/code-map/analysis/blast-radius.json`:

| Key | Meaning |
|---|---|
| `meta` | both inputs' provenance, `map_possibly_stale` |
| `stats` | counts per map status, `screens_at_risk`, `screen_risk_ratio`, `packs_at_risk`, `pr_risk` |
| `files[]` | each changed file + what the map says about it |
| `screens[]` | each screen the change can reach, and via which files |

`stats.packs_at_risk` names the Packwerk packages the *screens* belong to, not the
packages the changed files live in — a one-line edit to a shared model under `app/`
puts screens in three packs at risk while touching no `packs/` file at all. It is
who to ask for review, not where the diff is.

### `files[].map_status` — five answers, not two

| Status | Meaning | Risk |
|---|---|---|
| `mapped` | the map knows this file; `screens_reached` screens reach it | scored |
| `unused` | in the map, but no screen reaches it — **suspected** dead code | `none` |
| `new_file` | added by this branch, in scannable territory | `unknown` |
| `check_manually` | scannable territory, not in the map — stale map, or a blind spot (below) | `unknown` |
| `not_scanned` | `spec/`, `doc/`, `db/`, `.github/`… — KR1 never scans these | `unknown` |

The statuses are named for what the reader should do with them, because the one
that matters is the one that is easiest to skim past. `not_scanned` can be ignored:
the reason it is missing from the map is known and uninteresting. `check_manually`
is everything else that is missing, with no known reason — so it cannot be ignored,
and the name says so.

Splitting `not_scanned` out of `check_manually` matters. On a 757-file range,
lumping them together gives 512 files needing a look and the number means nothing;
split, it is 153 and every one is worth a glance.

### `files[].risk` — by share of the app, not by raw count

```
critical  screens_reached_ratio >= 0.30
high      screens_reached_ratio >= 0.05, or hub == true
medium    screens_reached_ratio >= 0.01
low       reached by at least one screen
none      reached by none
```

Ratios rather than counts, so the thresholds keep their meaning as the repo grows.
`hub` promotes to `high` on its own: KR1 flags a file as a hub by raw fan-in, which
catches files with an enormous number of *referrers* even when few screens reach them.

`stats.file_risks` counts only the files that could be scored. `unknown` is left
out of it: it means "no score is possible", not "checked and found safe", and a
histogram reading `critical: 1, unknown: 15` invites the opposite reading. The
files behind it are counted by `files_not_scanned`, `files_check_manually` and
`files_new`, which together with `file_risks` sum to `files_changed`.

`stats.pr_risk` is the highest file risk present. It is a **reach** measure, not a
danger measure — a one-word comment change in `application_controller.rb` scores
`critical`. That is correct and deliberate: this step reports reach, and step 3
decides whether the reach matters.

Step 1 hands over one signal that narrows this cheaply: a file whose `symbols` carry
only `new_def` and no `def` gained methods without touching any that already
existed, so none of the screens counted against it can be running the changed code
yet. `company.rb` in the sample run is exactly that — 925 screens reached,
`critical`, and a single added method.

### `screens[]` — sorted by what was rewritten

`rewritten: true` means a changed `def` **is** that screen's action — step 1's
qualified name (`PackingSlipsController#export_invoice_csv`) matched the map's
`controller_class` + `action`, and `rewritten_methods` names which ones. Those
screens were rewritten, not merely reached, so they sort first.

`changed_files[]` lists every one of the PR's files this screen loads, uncapped.

**Empty keys are omitted, not written as null** — read every field but `screen` and
`changed_files` with a default. A screen that was merely reached carries just those
two; `route`, `pack` and `vue_page` appear only where they have a value, and
`rewritten` only when true. `route` is further kept only on rewritten screens, the
ones someone will actually open: the screen id names the screen on its own.

The output is a thousand-odd screens, so per-entry key overhead dominates it —
trimming those keys took this PR's output from 357 KB to 187 KB without dropping a
single screen.

**This is the only complete file ↔ screen mapping in the output**, and inverting it
reproduces every `files[].screens_reached` exactly. `files[]` therefore carries the
count alone and no screen list: a 20-entry alphabetical sample of 925 screens
duplicated this while reading as if it were a ranking.

It is uncapped because a cap here costs more than it saves. Measured on a
1227-file range: capping at 8 truncated 356 of 1342 screens and shrank the output
by 14% (1.55 MB → 1.36 MB). The size is driven by the screen count, not by these
lists, so the cap paid for a rounding error with the completeness the inversion
depends on.

## Blind spots worth stating

- **Shared Vue components are invisible.** KR1 maps frontend down to `vue_page`
  only, so `z-inventory-selector.vue` lands in `check_manually`, not in `mapped` with the
  40 screens that embed it. A change to a shared component reads as low-risk here
  and is not. Step 3 must treat a `check_manually` `.vue` under `components/` as unknown
  risk, never as no risk.
- **`unused` means suspected, not confirmed.** KR1 follows constant names, so
  `send(:"#{x}_service")` and constants built from strings are invisible to it.
- **`map_possibly_stale`** is set whenever `impact.json`'s `scan_commit` differs
  from the diff's `head_sha`. Usually harmless — the map is only wrong about files
  whose *structure* changed since it was built. It is a hint for step 3, not an error.
- **Reach is not blame.** A screen appearing here means the change is somewhere in
  the set of files it loads. Whether the changed line runs on that screen's path is
  a question only step 3, reading the code, can answer.

## Rebuilding

Nothing is cached. Re-run step 1 then this after every push. `meta.diff_head_sha`
and `meta.map_scan_commit` record exactly what was compared — check them before
suspecting the script.
