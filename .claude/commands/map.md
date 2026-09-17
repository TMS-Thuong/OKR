# Open the impact map

Build two files and open the first: a self-contained HTML spider map that starts
on the **feature areas** of the system (Tồn kho, Bán hàng & giao hàng, Thanh toán,
…) and drills through controller and screen down to the code files each one
reaches, plus `summary.md` — the same shape written out for reading in a
terminal, quoting in a review, or diffing between two scans.

| Output | What it is |
|---|---|
| `.ai/code-map/doc/index.html` | the map, click to drill in |
| `.ai/code-map/doc/summary.md` | every area, controller and screen; the areas that share code; the files that are riskiest to change |

## Usage

```
/map                  build and open (about 1 second; runs all three steps
                      by itself the first time, when .ai/ does not exist yet)
/map --refresh        re-scan the code first (about 10 seconds)
/map --lang ja        build the UI in Japanese (default is Vietnamese)
/map <search>         open straight into the screen matching <search>
```

## What to do

1. Steps 1 and 2 of KR1 run when `--refresh` is passed, and also when
   `impact.json` is missing. `build.rb` handles both itself, so just forward
   the flag:
   - `.claude/skills/code-map/scripts/scan-code-map.sh` (incremental from the
     commit recorded in `scan.json`)
   - `.claude/skills/impact-analysis/scripts/analyze-impact.sh`
2. Run the builder, forwarding every flag the user gave:

   ```bash
   .claude/skills/code-map-doc/scripts/build.rb --open [flags]
   ```

3. If the user passed a search term rather than a flag, find the screen: read
   `.ai/code-map/analysis/impact.json` and substring-match `screens[].id`. Pass
   the first match as `--default-screen`. **If nothing matches, say so** and
   build the default view instead of guessing.
4. Report the summary. **Always include the dictionary hit rate** (for example
   `1137/1470 = 77%`): the rest are machine-composed names and some read oddly.
   Mention `summary.md` too — it is the half of the output a reader can grep.

## Always state these limits when reporting

- Routes are parsed from the Rails DSL, not loaded from Rails. Exotic DSL is
  missed, and `meta.routes_source` records which path was used.
- References are name-based static analysis: dynamic dispatch is missed, and
  generic constant names are over-reported.
- "Related screens" means *shares non-hub files*, not a human's idea of a
  feature. Reliable within a controller, weak evidence across distant ones.

## Related

| For | See |
|---|---|
| Builder, name dictionary, look and density | `.claude/skills/code-map-doc/SKILL.md` |
| Reading the source (step 1) | `.claude/skills/code-map/SKILL.md` |
| Computing relatedness (step 2) | `.claude/skills/impact-analysis/SKILL.md` |

## FAQ

**A screen name is wrong.**
Edit `LABELS` in `.claude/skills/code-map-doc/scripts/build.rb` and re-run
`/map`. No rebuild.

**I want different sections or wording in `summary.md`.**
Edit `write_summary` in the same file and re-run. No rebuild.

**I want different colours, node sizes or fewer files per screen.**
Edit the `UI` constant in the same file (`colors`, `sizes`, `force`, `maxFiles`,
`labelMinZoom`) and re-run. No rebuild.

**A screen is missing from the landing view.**
The landing view is `index` actions outside `api/` and `zaico_admin`. Change
`HOME_SCREEN` in the same file.

**I want to change the panels, tabs or interaction.**
That is the only change that needs the toolchain:

```bash
cd .ai/code-map/ui && npm install && npm run build
cp dist/index.html ../../../.claude/skills/code-map-doc/assets/app.html
```
