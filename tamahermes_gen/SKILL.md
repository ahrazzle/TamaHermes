---
name: tamahermes_gen
description: Generate complete TamaHermes milestone pawn/tamago asset catalogs from one or more short character prompts or profile JSON files, including lifecycle evolution forms, manifests, previews, and QA reports.
---

# TamaHermes Gen

Use this skill when asked to create or update a TamaHermes character catalog from
a concept such as “generate a TamaHermes named Ducky inspired by a duck”.

## Workflow

1. Convert the user prompt into a profile JSON with Codex agent judgment.
   - Read `references/profile_generation_contract.md`.
   - Do not rely on Python regex or keyword parsing for prose understanding.
   - Use `scripts/prompt_to_profile.py` only to emit an agent brief or validate
     the JSON after the agent has authored it.
2. Render the catalog with `scripts/render_catalog.py`.
3. Inspect `qa/build_report.json`, `qa/pet_contact_sheet.png`, and
   `qa/catalog_matrix.png`.
4. Update milestone docs with the shipped form ids, palettes, and handoff notes.

For M2.1/M6/M6.1, render with `--asset-version m2.1`. This keeps the M2
visible shell and pawn colors while enabling the M6 screen/QA policy and the
M6.1 clean pawn silhouette policy:

- identity `main`;
- identity `secondary` (or legacy `shade`);
- `mainShade` as dark LCD face/detail ink;
- one `accent` ink per stage/form for readable lifecycle accessories.

M6.1 adds `artDirection: m6.1-clean-limbed-pawn-v1` to generated manifests.
Toast and Mais should read as compact mascots with visible hands and feet,
1px contours, and sparse intentional detail strokes. Avoid dense dither,
random speckles, muddy multi-color patches, or body shapes that read as blobs.

## Profile

Keep profiles small. Required fields:

```json
{
  "id": "ducky",
  "displayName": "Ducky",
  "inspiration": "a duck",
  "description": "A duck-inspired TamaHermes companion.",
  "family": "generic",
  "palette": {
    "main": "#fff4a8",
    "shade": "#d59a3a"
  }
}
```

Known `family` values are `toast`, `mais`, `duck`, and `generic`. The renderer
has custom silhouettes for `toast`, `mais`, and `duck`; `generic` uses a compact
mascot silhouette and should be refined into a custom family before a polished
release. M2.1 profiles may optionally provide `palette.secondary`,
`palette.mainShade`, or `stageAccents` overrides; otherwise the renderer keeps
M2 body inks, chooses dark LCD face ink, and derives stage accessories
deterministically.

## Commands

Render a milestone:

```bash
python tamahermes_gen/scripts/render_catalog.py \
  --milestone M2.1 \
  --asset-version m2.1 \
  --output-dir milestones/M2.1 \
  --profile tamahermes_gen/profiles/toast.json \
  --profile tamahermes_gen/profiles/mais.json
```

Parse a simple prompt into a profile:

```bash
python tamahermes_gen/scripts/prompt_to_profile.py \
  --prompt "基于 tamahermes_gen 下的内容，生成一个叫做 Ducky 的 tamahermes，它的灵感来自鸭子" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output tamahermes_gen/profiles/ducky.json
```

Validate a Codex-authored profile:

```bash
python tamahermes_gen/scripts/prompt_to_profile.py \
  --input tamahermes_gen/profiles/ducky.json \
  --output tamahermes_gen/profiles/ducky.json
```

## Contract Reference

Read `references/asset_contract.md` when changing form ids, stage coverage, or
palette policy, and `references/profile_generation_contract.md` when turning a
user prompt into a profile. The renderer is deterministic: do not hand-edit
generated PNGs unless the generator is updated to reproduce the change.
