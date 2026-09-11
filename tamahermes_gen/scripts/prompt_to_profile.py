#!/usr/bin/env python3
"""Validate a Codex-agent-authored TamaHermes profile JSON.

Natural-language interpretation belongs to the Codex agent/LLM workflow. This
script only normalizes a profile object, writes a reusable JSON artifact, and
can emit a brief for the agent when a user starts from a prose prompt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


KNOWN_FAMILIES = {"toast", "mais", "duck", "generic"}
KNOWN_STAGE_ACCENTS = {
    "egg",
    "hatchling",
    "child",
    "teen.focused",
    "teen.resilient",
    "teen.restless",
    "adult.calm",
    "adult.resilient",
    "adult.worker",
    "adult.quiet",
    "adult.sleepy",
    "hibernation",
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "tamahermes"


def titleize(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("_") if part) or "TamaHermes"


def load_json_arg(value: str) -> dict[str, Any]:
    if value.startswith("@"):
        return load_json_path(Path(value[1:]).expanduser())
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("profile JSON must be an object")
    return parsed


def load_json_path(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"profile JSON in {path} must be an object")
    return parsed


def normalize_hex(value: Any, field: str) -> str:
    if not isinstance(value, str) or not HEX_RE.match(value):
        raise ValueError(f"{field} must be a #RRGGBB color")
    return value.lower()


def normalize_palette(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("palette must be an object")
    if "main" not in raw:
        raise ValueError("palette.main is required")
    if "shade" not in raw and "secondary" not in raw:
        raise ValueError("palette.shade or palette.secondary is required")

    palette = {"main": normalize_hex(raw["main"], "palette.main")}
    if "secondary" in raw:
        palette["secondary"] = normalize_hex(raw["secondary"], "palette.secondary")
        palette["shade"] = normalize_hex(raw.get("shade", raw["secondary"]), "palette.shade")
    else:
        palette["shade"] = normalize_hex(raw["shade"], "palette.shade")
    if "mainShade" in raw:
        palette["mainShade"] = normalize_hex(raw["mainShade"], "palette.mainShade")
    return palette


def normalize_stage_accents(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("stageAccents must be an object")
    accents: dict[str, str] = {}
    for key, value in raw.items():
        if key not in KNOWN_STAGE_ACCENTS:
            raise ValueError(f"unknown stage accent key {key!r}")
        accents[key] = normalize_hex(value, f"stageAccents.{key}")
    return accents


def normalize_profile(data: dict[str, Any]) -> dict[str, Any]:
    profile_id = slugify(str(data.get("id") or data.get("displayName") or "tamahermes"))
    display_name = str(data.get("displayName") or titleize(profile_id)).strip() or titleize(profile_id)
    inspiration = str(data.get("inspiration") or "").strip()
    if not inspiration:
        raise ValueError("inspiration is required; the Codex agent should preserve the user's core concept")

    family = str(data.get("family", "generic")).strip().lower() or "generic"
    if family not in KNOWN_FAMILIES:
        raise ValueError(f"family must be one of {sorted(KNOWN_FAMILIES)}")

    description = str(data.get("description") or f"A {inspiration}-inspired TamaHermes companion.").strip()
    profile: dict[str, Any] = {
        "id": profile_id,
        "displayName": display_name,
        "inspiration": inspiration,
        "description": description,
        "family": family,
        "palette": normalize_palette(data.get("palette")),
    }

    stage_accents = normalize_stage_accents(data.get("stageAccents"))
    if stage_accents:
        profile["stageAccents"] = stage_accents

    personality = data.get("personality")
    if isinstance(personality, dict):
        profile["personality"] = personality
    else:
        profile["personality"] = {
            "defaultAdult": "calm",
            "branchBias": ["calm", "worker", "quiet"],
            "notes": "Authored by a Codex agent from the user concept; renderer applies M6 screen integration and M6.1 clean pawn silhouettes.",
        }

    art_direction = data.get("artDirection") if isinstance(data.get("artDirection"), dict) else {}
    profile["artDirection"] = {
        **art_direction,
        "lcdIntegration": {
            "screenStyle": "low-contrast reflective LCD",
            "inkPolicy": "Renderer keeps M2 identity colors for pawn bodies and uses dark LCD ink for faces.",
            "avoid": [
                "brand-specific shell shapes",
                "extra unmasked color effects inside the screen",
                "unmasked sprite edges outside the LCD viewport",
            ],
        },
        "pawnStyle": {
            "id": "m6.1-clean-limbed-pawn-v1",
            "silhouette": "compact mascot with visible hands and feet, clean 1px contour lines, and sparse intentional detail pixels",
            "avoid": [
                "random dither",
                "speckled texture fields",
                "blob silhouettes without limbs",
            ],
        },
    }
    return profile


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def agent_brief(prompt: str, destination: str | None) -> str:
    destination_line = destination or "the requested profile JSON path"
    return f"""# TamaHermes Profile Agent Brief

Create one TamaHermes profile JSON object for this user prompt:

```text
{prompt}
```

Write the JSON object to `{destination_line}`. Do not wrap it in prose.

Required fields:

```json
{{
  "id": "lowercase_slug",
  "displayName": "Human Name",
  "inspiration": "short source concept",
  "description": "one concise sentence",
  "family": "toast | mais | duck | generic",
  "palette": {{
    "main": "#rrggbb",
    "shade": "#rrggbb"
  }},
  "personality": {{
    "defaultAdult": "calm",
    "branchBias": ["calm", "worker", "quiet"],
    "notes": "short behavior/art note"
  }}
}}
```

Rules:

- Interpret the user's naming and inspiration with the Codex agent's language ability.
- Use `toast`, `mais`, or `duck` only when the concept genuinely matches the renderer's built-in silhouette; otherwise use `generic`.
- Keep colors as identity inks. The renderer keeps M2-style pawn body colors, applies M6 LCD screen integration, and uses M6.1 clean pawn silhouettes with hands, feet, and sparse detail pixels.
- Do not copy a branded virtual-pet shell, screen layout, mascot, or logo.
- After writing the JSON, validate it with:

```bash
python tamahermes_gen/scripts/prompt_to_profile.py --input {destination_line} --output {destination_line}
```
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a Codex-agent-authored TamaHermes profile JSON.")
    parser.add_argument("--prompt", help="User prose prompt, used only when emitting an agent brief.")
    parser.add_argument("--brief-output", help="Write a Codex agent brief Markdown file for --prompt.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", help="Codex-agent-authored profile JSON file.")
    source.add_argument("--profile-json", help="Codex-agent-authored profile JSON object, or @path.")
    parser.add_argument("--output", help="Destination normalized profile JSON path.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.brief_output:
        if not args.prompt:
            parser.error("--brief-output requires --prompt")
        brief_path = Path(args.brief_output).expanduser().resolve()
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(agent_brief(args.prompt, args.output), encoding="utf-8")
        if not args.input and not args.profile_json:
            print(json.dumps({"ok": True, "needsAgentProfile": True, "brief": str(brief_path)}, indent=2))
            return

    if not args.output:
        parser.error("--output is required when validating a profile")
    if not args.input and not args.profile_json:
        parser.error("profile JSON is required; use --input or --profile-json after the Codex agent authors it")

    data = load_json_path(Path(args.input).expanduser().resolve()) if args.input else load_json_arg(args.profile_json)
    profile = normalize_profile(data)
    output = Path(args.output).expanduser().resolve()
    write_json(output, profile)
    print(json.dumps({"ok": True, "output": str(output), "id": profile["id"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"prompt_to_profile: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
