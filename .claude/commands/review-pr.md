# Review a PR by blast radius

Run KR2 end to end: read the diff, cross-check it against KR1's impact map, look for
breaks / duplicate code / rule violations, then write one report with a risk verdict.

| Output | What it is |
|---|---|
| `.ai/code-map/doc/pr-report.md` | the report — verdict, blast radius, findings, reading order |
| `.ai/code-map/analysis/pr-report.json` | same content, machine-readable |
| `.ai/code-map/raw/pr-diff.json`, `analysis/blast-radius.json`, `analysis/pr-check.json` | the three datasets it is built from |

## Usage

```
/review-pr                    review the current branch against its merge base
/review-pr <base>             review against a specific base ref (commit, branch, tag)
/review-pr --lang en          write the report in English (default is Vietnamese)
```

## What to do

1. Run the four steps in order. Stop and report if one fails — later steps read the
   earlier files and will otherwise describe a stale PR.

   ```bash
   .claude/skills/pr-diff/scripts/scan-pr-diff.sh [--base <ref>]
   .claude/skills/blast-radius/scripts/analyze-blast-radius.sh
   .claude/skills/pr-check/scripts/check-pr.sh
   .claude/skills/pr-report/scripts/build-pr-report.sh [--lang en]
   ```

   `blast-radius` needs `.ai/code-map/analysis/impact.json`. If it is missing, run
   `/map` first. If it exists but was scanned at another commit, the report says so —
   pass that on, do not silently treat the screen counts as exact.

2. **Confirm every `error` and `warning` by reading the source.** This is the part no
   script can do. For each one, open `file:line` and `evidence.call_sites` in
   `pr-check.json`, then decide:

   | Verdict | Meaning |
   |---|---|
   | `confirmed` | the problem is real at that line |
   | `dismissed` | a false positive — give the reason in one clause |
   | `needs author` | only the author knows if it is intended |

   Check whether the line actually came from this PR before confirming it: a rule
   violation on a line that already existed at the base is not this PR's problem.
   Use `git show <base>:<file>` to tell them apart.

   `info` findings are human decisions — leave them, do not confirm them one by one.

3. Write the verdicts to a JSON file and rebuild, so the report carries them:

   ```bash
   cat > /tmp/verdicts.json <<'JSON'
   { "<finding id>": { "verdict": "confirmed", "note": "<one clause>" } }
   JSON
   .claude/skills/pr-report/scripts/build-pr-report.sh --verdicts /tmp/verdicts.json
   ```

   Never hand-edit `pr-report.md` — the next run overwrites it.

4. Report in chat: the verdict line, then the confirmed problems with `file:line`,
   then what needs the author. Point at `pr-report.md` for the rest. Do not fix code.

## Always state these limits when reporting

- No finding means "no static use was found", not "safe". `send`, `constantize` and
  dynamic i18n keys are invisible to grep.
- Screen counts come from name-based static analysis of the KR1 map, and are an
  estimate whenever the map was scanned at a different commit.
- No lint, test or type-check is run here — that is CI's job.
- The risk level describes reach and rule violations, not correctness. A low-risk PR
  can still be wrong.

## Related

| For | See |
|---|---|
| Reading the diff (step 1) | `.claude/skills/pr-diff/SKILL.md` |
| Cross-checking the map (step 2) | `.claude/skills/blast-radius/SKILL.md` |
| Finding problems (step 3) | `.claude/skills/pr-check/SKILL.md` |
| Writing the report (step 4) | `.claude/skills/pr-report/SKILL.md` |
| Building the map KR2 reads | `.claude/commands/map.md` |

## FAQ

**The risk level feels wrong.**
The whole rule is the table in `.claude/skills/pr-report/SKILL.md`. Change
`verdict()` and `WIDE_RISKS` in `.claude/skills/pr-report/scripts/build_pr_report.py`.

**A rule check fires on a line that is intentionally written that way.**
Put `pr-check:ignore <reason>` on the line. The reason is required — it is what the
next reader sees instead of the warning.

**Screen counts look stale.**
`/map --refresh`, then re-run `/review-pr`.
