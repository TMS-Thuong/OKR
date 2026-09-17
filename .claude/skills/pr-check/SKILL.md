---
name: pr-check
description: >-
  Check the code a pull request changed for three problems: does it break code
  elsewhere (removed/renamed methods, classes, actions, exports or translation keys
  still in use; changed signatures with callers not updated; rewritten methods in
  widely shared files; new methods without specs), does it add duplicate or dead
  code (copied blocks, methods identical to existing ones, unused new methods,
  commented-out code), and does it break the team's rules (.claude/rules and
  CLAUDE.local.md: Ruby 3.3, no mocks in specs, no queries in controllers, no
  business logic in models, no calculation in SQL, no invariant checks inside loops,
  i18n, GTM, Packwerk, commit format). Step 3 of KR2: a script collects
  evidence-backed suspicions, then Claude reads the source to confirm each one.
  Use for "does this PR break anything", "is there duplicate code", "does it follow
  our rules", or after pr-diff and blast-radius.
---

# pr-check — KR2 step 3: phá chỗ khác / code lặp / sai quy chuẩn

| Step | Skill | Output |
|---|---|---|
| 1 | `pr-diff` | `.ai/code-map/raw/pr-diff.json` — dòng nào đổi |
| 2 | `blast-radius` | `.ai/code-map/analysis/blast-radius.json` — đụng bao nhiêu màn hình |
| 3 | **pr-check** | `.ai/code-map/analysis/pr-check.json` — có sai không |

## Run

```bash
.claude/skills/pr-diff/scripts/scan-pr-diff.sh
.claude/skills/blast-radius/scripts/analyze-blast-radius.sh   # optional: ranks findings by file_risk
.claude/skills/pr-check/scripts/check-pr.sh
```

Python 3 + git. ~10 s (loads every code file once to search callers and duplicates).
Head = working tree, base = `pr-diff.json` `meta.base`. If HEAD moved since step 1,
`meta.diff_possibly_stale` is true — re-run step 1.

## Output

`findings[]`, sorted by severity → `file_risk` → file → line. Each has `id`, `group`,
`check`, `severity` (`error` breaks / forbidden · `warning` likely wrong · `info`
human decision), `file`, `line` (`null` when something was removed), `message.vi/en`,
`rule`, `evidence` (`call_sites`, `before`/`after`, `duplicate_of`, `code`).

## Checks

**breaks** (`checks_breaks.py`) — base vs head, then search the repo. Lines this PR added are treated as already updated.

| check | when |
|---|---|
| `removed_but_still_called` | Ruby method or TS export removed, still used |
| `signature_changed` | Ruby params changed so old calls fail, callers not updated |
| `removed_class_still_used` | class/module deleted or renamed, still referenced |
| `removed_action_still_routed` | controller action removed, routes still `ctrl#action` |
| `removed_i18n_key_still_used` | locale key removed (not present elsewhere in same language), still used in `$t('…')` |
| `shared_method_rewritten` | existing method edited in a `critical`/`high` file (step 2) — list to re-read |
| `untested_change` | new public method in models/services/jobs/forms, spec missing or never mentions it |

`evidence.match`: `exact` (found as `Owner.method`) → error; `name_only` → warning, the call may be another class's method.

**duplicate** (`checks_duplicate.py`)

| check | when |
|---|---|
| `duplicate_method` | new Ruby method body identical to an existing method |
| `duplicate_code` | ≥6 added lines identical to code elsewhere (`info` for specs) |
| `unused_new_method` | new method referenced nowhere |
| `commented_out_code` | ≥3 added lines of commented-out code |

**rules** (`checks_rules.py`) — added lines only. `pr-check:ignore <reason>` skips a line.

| check | rule |
|---|---|
| `ruby33_incompatible`, `debug_left`, `focused_or_skipped_test`, `ts_any`, `vue_v_html`, `packwerk`, `hardcoded_text`, `gtm`, `commit_message_format` | `.claude/rules/*` |
| `spec_mock_stub` (error) | CLAUDE.local.md — no mock/stub, use FactoryBot records |
| `controller_business_logic` | CLAUDE.local.md — controllers only call Service + render |
| `logic_in_model` | CLAUDE.local.md — no business logic (settings, other models, SQL) in models |
| `service_calls_service` | CLAUDE.local.md — share via Concern, not Service → Service |
| `sql_calculation`, `string_sql_condition` | CLAUDE.local.md — SQL only fetches; use AR DSL |
| `query_in_loop`, `loop_invariant_condition` | CLAUDE.local.md — decide outside the loop |

`CLAUDE.local.md` is git-ignored; the rules above are its checkable parts.

## After the script: confirm by reading

For each `error` / `warning`, open `file:line` and `evidence.call_sites`, then mark
**confirmed**, **dismissed** (one clause why) or **needs author**. Do not fix.
Common false positives: `name_only` matches on another class; methods called via
`send` / templates; spec setup duplicated on purpose.

For `shared_method_rewritten`: compare `git show <base>:<file>` with the current
method and say in one sentence what existing callers now see differently.

Report in chat grouped by `group`, errors first, with `file:line` and `id`.

## Limits

- grep, not a type system: `send`, `constantize`, dynamic `$t` keys are invisible.
  No `breaks` finding means "no static use found", not "safe".
- No lint / type-check / tests — CI does that.
- Logic mistakes (`>` → `>=`) are the reading step's job.
