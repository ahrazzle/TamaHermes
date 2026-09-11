# TamaHermes Asset Contract

Use this reference when generating milestone pawn assets with `tamahermes_gen`.

## Renderable Form

Each form lives under:

```text
assets/pawn/pets/<form-id>/
  poses.txt
  pose_manifest.json
  runtime_motion.json
  poses_atlas.png
  poses_atlas_x4.png
  poses/*.png
```

Rules:

- source pose size is always `24x26`;
- M2 poses use only transparent pixels plus `palette.main` and `palette.shade`;
- M2.1 poses use transparent pixels plus M2-visible `palette.main`,
  `palette.secondary` (or legacy `palette.shade`), `palette.mainShade` for dark
  LCD face ink, and one readable stage/form `palette.accent` accessory;
- all required poses are present for every shipped form;
- M2 face details are transparent cutouts; M2.1/M6 face details are dark LCD
  ink so they stay readable on the pale screen plane;
- M6.1 records `artDirection: m6.1-clean-limbed-pawn-v1` and requires Toast
  and Mais pawns to use visible hands, visible feet, 1px contours, and sparse
  intentional detail pixels instead of noisy texture fields;
- `poses_atlas.png` is source scale and `poses_atlas_x4.png` is nearest-neighbor QA scale.

## Evolution

M2 ships complete line-specific paths:

```text
<line>_egg
<line>_hatchling
<line>
<line>_teen_focused
<line>_teen_resilient
<line>_teen_restless
<line>_adult_calm
<line>_adult_resilient
<line>_adult_worker
<line>_adult_quiet
<line>_adult_sleepy
<line>_hibernation
```

Child form ids are intentionally short (`toast`, `mais`) because those are the
first user-facing companions. Other stages include their stage and branch in
the immutable form id.

## Prompt To Profile

For one-sentence generation, Codex interprets the prompt and writes a profile
JSON first. Python validates the object; it does not infer names, palettes, or
concepts from prose. At minimum provide:

```json
{
  "id": "ducky",
  "displayName": "Ducky",
  "inspiration": "a duck",
  "description": "A tidy duck-inspired TamaHermes companion.",
  "family": "duck",
  "palette": {
    "main": "#fff4a8",
    "shade": "#d59a3a"
  }
}
```

Then render with:

```bash
python tamahermes_gen/scripts/render_catalog.py \
  --milestone M2.1 \
  --asset-version m2.1 \
  --output-dir milestones/M2.1 \
  --profile tamahermes_gen/profiles/toast.json \
  --profile tamahermes_gen/profiles/mais.json
```

To generate an agent brief or validate a profile, use
`references/profile_generation_contract.md` and
`scripts/prompt_to_profile.py`.

## M2.1 Palette Symbols

`poses.txt` maps palette roles to compact ASCII symbols:

```text
. transparent
# palette.main
+ palette.secondary or legacy palette.shade
= palette.mainShade
* palette.accent
```

The M2.1 renderer keeps `main` and `secondary` visually aligned with M2, uses
`mainShade` as dark face/detail ink, and uses `accent` for meaningful lifecycle
accessories such as headbands, flowers, ties, scarves, caps, and blankets.
`stageAccents` can override any lifecycle slot with keys such as `egg`,
`teen.focused`, or `adult.worker`.

M6.1 keeps the same palette symbols but tightens how they are used: secondary
and detail pixels should read as contour, limb, prop, face, or accessory. Do
not use them as random dither or dense corn-kernel speckle fields.
