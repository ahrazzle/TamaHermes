"""Static render evidence for the restored (pre-liquid-glass) EvoPet HUD.

No browser is assumed: this runs the real renderer with a representative
snapshot and writes each panel variant to a standalone HTML file, then checks
the locked render contracts against the generated text. The output is EVIDENCE
for human review only — it is not visual QA, and it proves nothing about the
live desktop composite.

Usage:
    uv run python scripts/render_hud_evidence.py [output-dir]   # default /tmp/evopet-hud-evidence

Variants: the one LCD panel in plain (system), opaque (Reduce Transparency),
high-contrast (Increase Contrast) and dark-mirror forms — on the restored skin
the a11y/appearance data attributes are inert mirrors (no CSS reacts to them
until an a11y follow-up reintroduces styling without the glass) — plus the
hidden-state (panel-less) config manifest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tamahermes.overlay import (  # noqa: E402
    DEFAULT_MIN_HEIGHT,
    DEFAULT_MIN_WIDTH,
    DEFAULT_PANEL_HEIGHT,
    DEFAULT_PANEL_WIDTH,
    native_overlay_config_payload,
    render_expanded_overlay_html,
    render_native_overlay_html,
)

SNAPSHOT: dict[str, object] = {
    "displayName": "TamaHermes",
    "lineId": "toast",
    "machineId": "aurora",
    "formId": "toast",
    "lastCodexState": "running",
    "level": 12,
    "lifeStage": "adult",
    "branch": None,
    "xp": 1420,
    "progress": {"percent": 72, "xpIntoLevel": 140, "xpToNextLevel": 195},
    "stats": {"energy": 54, "health": 88, "bond": 41, "mood": 66, "mess": 22},
    "traits": {"focus": 14, "resilience": 9, "restlessness": 2, "care": 3},
    "visual": {"alert": "calm", "satiety": "ok", "energy": "ok", "health": "ok"},
    "counters": {"workRuns": 96, "completedRuns": 81, "failedRuns": 7, "reviews": 14, "totalTokens": 43210, "tokenSamples": 220, "idleMinutes": 3},
    "latestEvent": {"event": "task_success", "at": "2026-09-17T06:40:00Z"},
}

VARIANTS: dict[str, dict[str, object] | None] = {
    "expanded": None,
    "expanded-opaque": {"reduceTransparency": True},
    "expanded-contrast": {"increaseContrast": True, "darkMode": False},
    "expanded-dark-mirror": {"darkMode": True},
}


def render_variant(name: str, a11y: dict[str, object] | None) -> str:
    html_text = render_native_overlay_html(SNAPSHOT, a11y=dict(a11y) if a11y else None)
    header = (
        "<!-- EvoPet HUD static render evidence — variant: {name}. "
        "The restored skin is a single opaque HTML LCD panel; no native "
        "material sits behind it. -->\n"
    ).format(name=name)
    return header + html_text


def contracts() -> list[str]:
    problems: list[str] = []
    for name, a11y in VARIANTS.items():
        html_text = render_variant(name, a11y)
        label = f"[{name}]"
        if "background: transparent" not in html_text:
            problems.append(f"{label} missing background: transparent")
        if "backdrop-filter: blur" in html_text:
            problems.append(f"{label} contains CSS blur (forbidden)")
        body_open = html_text.split("<body", 1)[1].split(">", 1)[0]
        if "data-a11y" in body_open and "opaque" not in name:
            problems.append(f"{label} leaked a data-a11y attribute")
        if "data-contrast" in body_open and "contrast" not in name:
            problems.append(f"{label} leaked a data-contrast attribute")
    plain = render_expanded_overlay_html(SNAPSHOT)
    # The no-glass lock: none of the liquid-glass experiment's surfaces or
    # tokens may appear in the page text.
    for forbidden in (
        "--chrome-wash", "--chrome-specular", "--chrome-border", "--chrome-shadow",
        "--ink-primary", "--opaque-bg", "--focus-ring", "prefers-color-scheme",
        'data-event="collapse"', ">PILL<", "border-radius: 19px",
    ):
        if forbidden in plain:
            problems.append(f"[expanded] glass-era residue: {forbidden}")
    if 'class="lcd"' not in plain:
        problems.append("[expanded] lost the full panel")
    if "--lcd: #092a2f" not in plain or "--ink: #d8f8aa" not in plain:
        problems.append("[expanded] lost the baseline LCD palette")
    if 'aria-label="Hide HUD (re-show with: tamahermes overlay show)"' not in plain:
        problems.append("[expanded] lost the baseline HIDE affordance copy")
    scale_row = plain.split('<div class="scale-controls"', 1)[1].split("</div>", 1)[0]
    if scale_row.count("<button") != 3:
        problems.append("[expanded] the scale-control row must carry exactly three buttons")
    for control in ("care", "feed", "clean", "play", "rest", "hide", "scale-down", "scale-up"):
        if f'data-event="{control}"' not in plain:
            problems.append(f"[expanded] missing the {control} control")
    # One surface: a caller still passing a legacy mode gets the same LCD page.
    if render_native_overlay_html(SNAPSHOT, mode="collapsed") != plain:
        problems.append("[expanded] the legacy collapsed mode must render the same panel")
    if render_native_overlay_html(SNAPSHOT, expanded=True) != render_expanded_overlay_html(SNAPSHOT):
        problems.append("[expanded] dispatcher and direct render disagree")
    return problems


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, a11y in VARIANTS.items():
        (out_dir / f"{name}.html").write_text(render_variant(name, a11y), encoding="utf-8")

    # Hidden mode has no panel; the evidence is the exact config the loop writes
    # so the reviewer can see visible=false with the last geometry kept.
    hidden_state = {"hudCollapsed": False, "hudHidden": True}
    (out_dir / "hidden-config-manifest.json").write_text(
        json.dumps(
            {
                "note": "hidden mode draws no panel; this is the native config payload",
                "state": hidden_state,
                "config": native_overlay_config_payload(out_dir, visible=False, mode="expanded"),
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "geometry.json").write_text(
        json.dumps(
            {
                "panel": {
                    "width": DEFAULT_PANEL_WIDTH,
                    "height": DEFAULT_PANEL_HEIGHT,
                    "floors": [DEFAULT_MIN_WIDTH, DEFAULT_MIN_HEIGHT],
                },
                "panelRadiusCssPx": 12,
                "lcdInset": {"left": 58, "right": 58, "top": 30, "bottom": 18},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    problems = contracts()
    manifest = {
        "generatedBy": "scripts/render_hud_evidence.py",
        "variants": sorted(VARIANTS),
        "contractProblems": problems,
        "visualQA": "NOT performed here; static HTML only",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for path in sorted(out_dir.iterdir()):
        print(path)
    if problems:
        print("\nCONTRACT PROBLEMS:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"\nall render contracts OK ({len(VARIANTS)} variants + hidden manifest + geometry)")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/evopet-hud-evidence")
    raise SystemExit(main(target))
