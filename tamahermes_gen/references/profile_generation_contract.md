# TamaHermes Profile Generation Contract

This contract replaces the old prompt-parsing helper. Codex interprets the
user's prose prompt, authors a small profile JSON object, and then Python only
validates and renders the catalog.

## Agent Responsibilities

- Extract the intended companion name and source concept from the user prompt.
- Choose `family` from `toast`, `mais`, `duck`, or `generic`.
- Use a built-in family only when the concept genuinely matches that silhouette.
- Pick identity colors as final pawn body colors. M6 keeps the M2-style body
  inks, adds dark LCD face ink, and clips the pawn through the screen mask.
- M6.1 also expects clean compact pawn silhouettes: visible hands and feet,
  1px contours, sparse intentional detail pixels, and no random speckle fields.
- Avoid brand-specific shell shapes, screen layouts, mascots, or logos.
- Write only the JSON object to the requested path.

## Required Profile Shape

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
  },
  "personality": {
    "defaultAdult": "calm",
    "branchBias": ["calm", "worker", "quiet"],
    "notes": "Cheerful and readable at 24x26."
  }
}
```

Optional fields:

- `palette.secondary`
- `palette.mainShade`
- `stageAccents` with keys such as `egg`, `teen.focused`, or `adult.worker`
- `artDirection` for human notes; the validator adds the M6 LCD integration
  rule block and M6.1 clean pawn style automatically.

## Validation

Validate an agent-authored profile:

```bash
python tamahermes_gen/scripts/prompt_to_profile.py \
  --input tamahermes_gen/profiles/ducky.json \
  --output tamahermes_gen/profiles/ducky.json
```

Start from a user prompt by writing an agent brief:

```bash
python tamahermes_gen/scripts/prompt_to_profile.py \
  --prompt "生成一个叫 Ducky 的 tamahermes，灵感来自鸭子" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output tamahermes_gen/profiles/ducky.json
```
