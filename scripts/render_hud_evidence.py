"""Static render evidence for the HUD liquid-glass work.

No browser is assumed: this runs the real renderer with a representative
snapshot and writes each panel variant to a standalone HTML file, then checks
the locked render contracts against the generated text. The output is EVIDENCE
for human review only — it is not visual QA, and it proves nothing about the
native glass material (which is AppKit chrome behind the WebView).

Usage:
    uv run python scripts/render_hud_evidence.py [output-dir]   # default /tmp/evopet-hud-evidence

Variants: expanded, collapsed pill; each in plain (system), opaque (Reduce
Transparency), high-contrast (Increase Contrast), dark-mirror and hidden-state
(a panel-less status manifest) forms.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tamahermes.overlay import (  # noqa: E402
    COLLAPSED_HEIGHT,
    COLLAPSED_WIDTH,
    native_overlay_config_payload,
    render_collapsed_overlay_html,
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
    "collapsed": None,
    "collapsed-opaque": {"reduceTransparency": True},
    "collapsed-contrast": {"increaseContrast": True, "darkMode": False},
    "collapsed-dark-mirror": {"darkMode": True},
}


def render_variant(name: str, a11y: dict[str, object] | None) -> str:
    mode = "collapsed" if name.startswith("collapsed") else "expanded"
    html_text = render_native_overlay_html(SNAPSHOT, mode=mode, a11y=a11y)
    header = (
        "<!-- EvoPet HUD static render evidence — variant: {name}. "
        "Native glass is AppKit chrome behind the WebView and is NOT represented here; "
        "open against a real desktop or rely on the live check. -->\n"
    ).format(name=name)
    return header + html_text


def contracts() -> list[str]:
    problems: list[str] = []
    for name, a11y in VARIANTS.items():
        html_text = render_variant(name, dict(a11y) if a11y else None)
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
    expanded = render_native_overlay_html(SNAPSHOT, expanded=True)
    collapsed = render_collapsed_overlay_html(SNAPSHOT)
    if 'class="lcd"' not in expanded:
        problems.append("[expanded] lost the full panel")
    if 'class="lcd"' in collapsed:
        problems.append("[collapsed] must not contain the full panel")
    if 'data-event="expand"' not in collapsed or 'aria-label="Expand HUD"' not in collapsed:
        problems.append("[collapsed] missing the expand affordance")
    if "const threshold = 3;" not in collapsed:
        problems.append("[collapsed] missing the 3 px click/drag threshold")
    for control in ("care", "feed", "clean", "play", "rest", "hide"):
        if f'data-event="{control}"' not in expanded:
            problems.append(f"[expanded] missing the {control} control")
    if render_native_overlay_html(SNAPSHOT, expanded=True) != render_expanded_overlay_html(SNAPSHOT):
        problems.append("[expanded] dispatcher and direct render disagree")
    return problems


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, a11y in VARIANTS.items():
        (out_dir / f"{name}.html").write_text(render_variant(name, dict(a11y) if a11y else None), encoding="utf-8")

    # Hidden mode has no panel; the evidence is the exact config the loop writes
    # so the reviewer can see visible=false with the last shape kept.
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
                "expanded": {"width": 376, "height": 226, "floors": [120, 80]},
                "collapsed": {"width": COLLAPSED_WIDTH, "height": COLLAPSED_HEIGHT, "floors": [COLLAPSED_WIDTH, COLLAPSED_HEIGHT]},
                "pillRadius": 19,
                "panelRadius": 16,
                "clickVsDragThresholdPx": 3,
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
        "visualQA": "NOT performed here; static HTML only, native material unrepresented",
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
