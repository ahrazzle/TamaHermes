#!/usr/bin/env python3
"""Render a complete layered Tamacodex catalog from character profiles."""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


CELL = (192, 208)
SCALE = 4
HI = (CELL[0] * SCALE, CELL[1] * SCALE)
PAWN_SOURCE = (24, 26)
PAWN_SCALE = 3
SCREEN = {"x": 35, "y": 47, "width": 122, "height": 104, "radius": 11}
INK_MAIN = 1
INK_SECONDARY = 2
INK_MAIN_SHADE = 3
INK_STAGE_ACCENT = 4
GRID_SYMBOLS = {
    0: ".",
    INK_MAIN: "#",
    INK_SECONDARY: "+",
    INK_MAIN_SHADE: "=",
    INK_STAGE_ACCENT: "*",
}
M2_PALETTE_POLICY = "transparent plus two fully opaque semantic inks per form"
M21_PALETTE_POLICY = (
    "transparent plus M2 identity main/shade inks, dark LCD face ink, "
    "and one readable lifecycle accessory accent ink"
)
LCD_RULESET_ID = "m6-lcd-screen-integration-v1"
ART_DIRECTION_ID = "m6.1-clean-limbed-pawn-v1"
LCD_FACE_INK = "#26372c"
EGG_PATH_D = (
    "M96 8 C142 8 176 43 181 104 "
    "C186 171 151 202 96 202 "
    "C41 202 6 171 11 104 "
    "C16 43 50 8 96 8 Z"
)
EGG_PATH_CUBICS = [
    ((96, 8), (142, 8), (176, 43), (181, 104)),
    ((181, 104), (186, 171), (151, 202), (96, 202)),
    ((96, 202), (41, 202), (6, 171), (11, 104)),
    ((11, 104), (16, 43), (50, 8), (96, 8)),
]


MACHINES: dict[str, dict[str, Any]] = {
    "aurora": {
        "displayName": "Aurora",
        "description": "Cool cyan-violet glass shell with a shared low-contrast LCD plane.",
        "palette": {
            "top": "#b8fff6",
            "mid": "#7c5cff",
            "bottom": "#00bbf9",
            "accent": "#00f5d4",
            "accent2": "#f15bb5",
            "screen": "#aab892",
            "screenInk": "#26372c",
            "screenGhost": "#66755f",
            "trim": "#fffdf5",
        },
        "pattern": "ribbons",
    },
    "pulse": {
        "displayName": "Pulse",
        "description": "Warm pink-mint glass shell with the same LCD screen rules.",
        "palette": {
            "top": "#fff7a8",
            "mid": "#f15bb5",
            "bottom": "#00f5d4",
            "accent": "#fee440",
            "accent2": "#00bbf9",
            "screen": "#aab892",
            "screenInk": "#26372c",
            "screenGhost": "#66755f",
            "trim": "#fffdf5",
        },
        "pattern": "dots",
    },
}


POSE_NAMES = [
    "idle_0",
    "idle_1",
    "blink",
    "squash",
    "wave_0",
    "wave_1",
    "wave_2",
    "jump_0",
    "jump_1",
    "jump_2",
    "failed_0",
    "failed_1",
    "failed_2",
    "wait_0",
    "wait_1",
    "wait_2",
    "work_0",
    "work_1",
    "work_2",
    "review_0",
    "review_1",
    "review_2",
    "step_right_0",
    "step_right_1",
    "step_right_2",
    "step_left_0",
    "step_left_1",
    "step_left_2",
]


RUNTIME_STATES: dict[str, list[dict[str, Any]]] = {
    "idle": [
        {"pose": "idle_0", "durationMs": 280, "offset": [0, 0]},
        {"pose": "idle_1", "durationMs": 180, "offset": [0, 0]},
        {"pose": "blink", "durationMs": 110, "offset": [0, 0]},
        {"pose": "idle_0", "durationMs": 320, "offset": [0, 0]},
    ],
    "work": [
        {"pose": "work_0", "durationMs": 130, "offset": [0, 0]},
        {"pose": "work_1", "durationMs": 130, "offset": [0, 1]},
        {"pose": "work_2", "durationMs": 130, "offset": [0, 0]},
        {"pose": "work_1", "durationMs": 150, "offset": [0, -1]},
    ],
    "waiting": [
        {"pose": "wait_0", "durationMs": 220, "offset": [0, 0]},
        {"pose": "wait_1", "durationMs": 190, "offset": [0, -1]},
        {"pose": "wait_2", "durationMs": 190, "offset": [1, -1]},
        {"pose": "wait_0", "durationMs": 260, "offset": [0, 0]},
    ],
    "review": [
        {"pose": "review_0", "durationMs": 160, "offset": [0, 0]},
        {"pose": "review_1", "durationMs": 170, "offset": [0, -1]},
        {"pose": "review_2", "durationMs": 130, "offset": [0, -1]},
        {"pose": "review_1", "durationMs": 220, "offset": [0, 0]},
    ],
    "failed": [
        {"pose": "failed_0", "durationMs": 160, "offset": [0, 1]},
        {"pose": "failed_1", "durationMs": 180, "offset": [0, 2]},
        {"pose": "failed_2", "durationMs": 220, "offset": [0, 2]},
        {"pose": "failed_1", "durationMs": 180, "offset": [0, 1]},
    ],
    "success": [
        {"pose": "jump_0", "durationMs": 120, "offset": [0, 2]},
        {"pose": "jump_1", "durationMs": 120, "offset": [0, -2]},
        {"pose": "jump_2", "durationMs": 130, "offset": [0, -4]},
        {"pose": "jump_1", "durationMs": 130, "offset": [0, -1]},
        {"pose": "squash", "durationMs": 220, "offset": [0, 2]},
    ],
    "greeting": [
        {"pose": "wave_0", "durationMs": 180, "offset": [0, 0]},
        {"pose": "wave_1", "durationMs": 160, "offset": [0, 0]},
        {"pose": "wave_2", "durationMs": 160, "offset": [0, 0]},
        {"pose": "wave_1", "durationMs": 220, "offset": [0, 0]},
    ],
    "drag_right": [
        {"pose": "step_right_0", "durationMs": 120, "offset": [0, 0]},
        {"pose": "step_right_1", "durationMs": 120, "offset": [2, 0]},
        {"pose": "step_right_2", "durationMs": 140, "offset": [4, 1]},
        {"pose": "step_right_1", "durationMs": 170, "offset": [2, 0]},
    ],
    "drag_left": [
        {"pose": "step_left_0", "durationMs": 120, "offset": [0, 0]},
        {"pose": "step_left_1", "durationMs": 120, "offset": [-2, 0]},
        {"pose": "step_left_2", "durationMs": 140, "offset": [-4, 1]},
        {"pose": "step_left_1", "durationMs": 170, "offset": [-2, 0]},
    ],
}


TEEN_BRANCHES = ["focused", "resilient", "restless"]
ADULT_BRANCHES = ["calm", "resilient", "worker", "quiet", "sleepy"]

DEFAULT_STAGE_ACCENTS = {
    "egg": "#f6a6cf",
    "hatchling": "#8fd2ff",
    "child": "#ffb45c",
    "teen.focused": "#4f7cff",
    "teen.resilient": "#2fbf8f",
    "teen.restless": "#e45ab8",
    "adult.calm": "#7fc69a",
    "adult.resilient": "#cf615e",
    "adult.worker": "#6b4a2f",
    "adult.quiet": "#9a8bd8",
    "adult.sleepy": "#6371bf",
    "hibernation": "#89a6ff",
}

FAMILY_STAGE_ACCENTS = {
    "toast": {
        "egg": "#e9a1c8",
        "hatchling": "#77c9ef",
        "child": "#ffb15e",
        "teen.focused": "#4d7ed8",
        "teen.resilient": "#6cbf79",
        "teen.restless": "#e85aa6",
        "adult.calm": "#8bbb75",
        "adult.resilient": "#c75f5f",
        "adult.worker": "#6b4a2f",
        "adult.quiet": "#9987ca",
        "adult.sleepy": "#5f6faf",
        "hibernation": "#8aa6d9",
    },
    "mais": {
        "egg": "#f0a6ce",
        "hatchling": "#78c9f0",
        "child": "#f2a14a",
        "teen.focused": "#4d8ad8",
        "teen.resilient": "#2caa82",
        "teen.restless": "#d95ab8",
        "adult.calm": "#86bd6d",
        "adult.resilient": "#c75f5f",
        "adult.worker": "#5f6fab",
        "adult.quiet": "#947fc3",
        "adult.sleepy": "#6a72b8",
        "hibernation": "#8aa6d9",
    },
    "duck": {
        "egg": "#f0a6ce",
        "hatchling": "#84d7ff",
        "child": "#5ab7e8",
        "teen.focused": "#527ee6",
        "teen.resilient": "#31b68d",
        "teen.restless": "#e05ab1",
        "adult.calm": "#80bf82",
        "adult.resilient": "#c65d5d",
        "adult.worker": "#6b4a2f",
        "adult.quiet": "#9a8bd8",
        "adult.sleepy": "#6572bd",
        "hibernation": "#8ca7e8",
    },
}


@dataclass(frozen=True)
class Profile:
    id: str
    display_name: str
    inspiration: str
    description: str
    family: str
    palette: dict[str, str]
    stage_accents: dict[str, str]
    source_path: str


@dataclass(frozen=True)
class FormSpec:
    id: str
    display_name: str
    description: str
    line_id: str
    line_display_name: str
    inspiration: str
    family: str
    stage: str
    branch: str | None
    palette: dict[str, str]


Grid = list[list[int]]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def hex_color(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def tone_hex(value: str, lightness_delta: float, saturation_delta: float = 0.0) -> str:
    red, green, blue = (component / 255 for component in rgb(value))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    shaded = colorsys.hls_to_rgb(
        hue,
        clamp(lightness + lightness_delta),
        clamp(saturation + saturation_delta),
    )
    return hex_color(tuple(int(round(component * 255)) for component in shaded))


def lcd_ink_hex(value: str, lightness: float, saturation_scale: float = 0.34) -> str:
    red, green, blue = (component / 255 for component in rgb(value))
    hue, _lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    ink = colorsys.hls_to_rgb(hue, clamp(lightness), clamp(saturation * saturation_scale + 0.03))
    return hex_color(tuple(int(round(component * 255)) for component in ink))


def rgba(value: str, alpha: int) -> tuple[int, int, int, int]:
    return (*rgb(value), alpha)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(lerp(a[0], b[0], t)),
        int(lerp(a[1], b[1], t)),
        int(lerp(a[2], b[2], t)),
    )


def cubic_point(
    curve: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]],
    t: float,
) -> tuple[float, float]:
    p0, p1, p2, p3 = curve
    mt = 1 - t
    x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
    y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def egg_path_points(scale: int = SCALE, samples_per_curve: int = 64) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for curve_index, curve in enumerate(EGG_PATH_CUBICS):
        start = 0 if curve_index == 0 else 1
        for step in range(start, samples_per_curve + 1):
            x, y = cubic_point(curve, step / samples_per_curve)
            points.append((int(round(x * scale)), int(round(y * scale))))
    return points


def goose_mask(scale: int = SCALE) -> Image.Image:
    width, height = CELL[0] * scale, CELL[1] * scale
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(egg_path_points(scale), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=0.42 * scale))


def screen_mask() -> Image.Image:
    mask = Image.new("L", CELL, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (
            SCREEN["x"],
            SCREEN["y"],
            SCREEN["x"] + SCREEN["width"],
            SCREEN["y"] + SCREEN["height"],
        ),
        radius=SCREEN["radius"],
        fill=255,
    )
    return mask


def gradient_body(mask: Image.Image, palette: dict[str, str]) -> Image.Image:
    top, mid, bottom = rgb(palette["top"]), rgb(palette["mid"]), rgb(palette["bottom"])
    accent = rgb(palette["accent"])
    layer = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    pxs = layer.load()
    mx = mask.load()
    width, height = mask.size
    for y in range(height):
        t = y / max(1, height - 1)
        if t < 0.48:
            base = lerp_rgb(top, mid, t / 0.48)
        else:
            base = lerp_rgb(mid, bottom, (t - 0.48) / 0.52)
        for x in range(width):
            alpha = mx[x, y]
            if alpha == 0:
                continue
            center = abs((x / width) - 0.5) * 2
            sheen = max(0, 1 - center) * 0.18 + math.sin((x / width + t) * math.pi) * 0.08
            color = lerp_rgb(base, accent, max(0, min(1, sheen)))
            pxs[x, y] = (*color, int(alpha * 0.92))
    return layer


def draw_symmetric_curve(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    draw.line(points, fill=fill, width=width, joint="curve")
    mirrored = [(HI[0] - x, y) for x, y in points]
    draw.line(mirrored, fill=fill, width=width, joint="curve")


def draw_machine_pattern(draw: ImageDraw.ImageDraw, machine: dict[str, Any]) -> None:
    palette = machine["palette"]
    if machine["pattern"] == "ribbons":
        draw_symmetric_curve(
            draw,
            [(30 * SCALE, 142 * SCALE), (61 * SCALE, 116 * SCALE), (77 * SCALE, 86 * SCALE), (96 * SCALE, 72 * SCALE)],
            rgba(palette["accent"], 112),
            8 * SCALE,
        )
        draw_symmetric_curve(
            draw,
            [(34 * SCALE, 164 * SCALE), (63 * SCALE, 150 * SCALE), (80 * SCALE, 135 * SCALE), (96 * SCALE, 130 * SCALE)],
            rgba(palette["accent2"], 82),
            6 * SCALE,
        )
    else:
        for row, y in enumerate((72, 91, 112, 135, 156)):
            span = 28 + row * 6
            for step in range(4):
                x = 96 - span + step * 12
                for side in (-1, 1):
                    cx = (96 + side * abs(96 - x)) * SCALE
                    cy = (y + math.sin(step * 1.4 + row) * 3) * SCALE
                    radius = (2.2 + (step % 2) * 0.9) * SCALE
                    draw.ellipse(
                        (cx - radius, cy - radius, cx + radius, cy + radius),
                        fill=rgba(palette["accent"], 72),
                    )
        draw_symmetric_curve(
            draw,
            [(38 * SCALE, 136 * SCALE), (65 * SCALE, 122 * SCALE), (82 * SCALE, 111 * SCALE), (96 * SCALE, 110 * SCALE)],
            rgba(palette["accent2"], 68),
            4 * SCALE,
        )


def draw_lcd_screen(
    draw: ImageDraw.ImageDraw,
    palette: dict[str, str],
    x: int,
    y: int,
    w: int,
    h: int,
    r: int,
) -> None:
    ink = palette["screenInk"]
    ghost = palette["screenGhost"]
    draw.rounded_rectangle(
        (x - 9 * SCALE, y - 9 * SCALE, x + w + 9 * SCALE, y + h + 9 * SCALE),
        radius=r + 9 * SCALE,
        fill=rgba(palette["trim"], 60),
        outline=rgba(palette["accent"], 84),
        width=2 * SCALE,
    )
    draw.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=rgba(palette["screen"], 248))
    draw.rounded_rectangle((x, y, x + w, y + h), radius=r, outline=rgba(ink, 78), width=SCALE)

    inner = (x + 7 * SCALE, y + 7 * SCALE, x + w - 7 * SCALE, y + h - 7 * SCALE)
    draw.rounded_rectangle(inner, radius=max(SCALE, r - 5 * SCALE), outline=rgba(ghost, 48), width=SCALE)
    for gx in range(x + 10 * SCALE, x + w - 8 * SCALE, 4 * SCALE):
        draw.line((gx, y + 9 * SCALE, gx, y + h - 9 * SCALE), fill=rgba(ghost, 16), width=1)
    for gy in range(y + 10 * SCALE, y + h - 8 * SCALE, 4 * SCALE):
        draw.line((x + 9 * SCALE, gy, x + w - 9 * SCALE, gy), fill=rgba(ghost, 16), width=1)

    icon_y = y + 10 * SCALE
    for index, icon_x in enumerate((x + 13 * SCALE, x + 25 * SCALE, x + 37 * SCALE)):
        draw.rectangle((icon_x, icon_y, icon_x + 6 * SCALE, icon_y + 4 * SCALE), outline=rgba(ink, 48), width=SCALE)
        if index == 0:
            draw.rectangle((icon_x + 2 * SCALE, icon_y + SCALE, icon_x + 5 * SCALE, icon_y + 3 * SCALE), fill=rgba(ink, 42))
        elif index == 1:
            draw.line((icon_x + SCALE, icon_y + 3 * SCALE, icon_x + 3 * SCALE, icon_y + SCALE), fill=rgba(ink, 42), width=SCALE)
            draw.line((icon_x + 3 * SCALE, icon_y + SCALE, icon_x + 5 * SCALE, icon_y + 3 * SCALE), fill=rgba(ink, 42), width=SCALE)
        else:
            draw.rectangle((icon_x + SCALE, icon_y + SCALE, icon_x + 5 * SCALE, icon_y + 3 * SCALE), fill=rgba(ink, 30))

    floor_y = y + h - 24 * SCALE
    draw.line((x + 15 * SCALE, floor_y, x + w - 15 * SCALE, floor_y), fill=rgba(ink, 42), width=SCALE)
    for hill_x in (x + 20 * SCALE, x + 78 * SCALE):
        draw.line((hill_x, floor_y, hill_x + 10 * SCALE, floor_y - 9 * SCALE), fill=rgba(ghost, 42), width=SCALE)
        draw.line((hill_x + 10 * SCALE, floor_y - 9 * SCALE, hill_x + 22 * SCALE, floor_y), fill=rgba(ghost, 42), width=SCALE)
    draw.rectangle((x + w - 28 * SCALE, y + 22 * SCALE, x + w - 14 * SCALE, y + 28 * SCALE), outline=rgba(ink, 34), width=SCALE)
    draw.rectangle((x + w - 24 * SCALE, y + 18 * SCALE, x + w - 18 * SCALE, y + 22 * SCALE), fill=rgba(ink, 24))
    draw.line((x + 16 * SCALE, y + h - 15 * SCALE, x + 31 * SCALE, y + h - 15 * SCALE), fill=rgba(ink, 34), width=SCALE)


def build_machine_png(machine: dict[str, Any], mask: Image.Image) -> Image.Image:
    palette = machine["palette"]
    body = gradient_body(mask, palette)
    glow_alpha = mask.filter(ImageFilter.GaussianBlur(radius=8 * SCALE)).point(lambda value: int(value * 0.18))
    glow = Image.new("RGBA", HI, rgba(palette["accent"], 0))
    glow.putalpha(glow_alpha)
    shell = Image.alpha_composite(glow, body)

    overlay = Image.new("RGBA", HI, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw_machine_pattern(draw, machine)

    outer = mask.filter(ImageFilter.MaxFilter(13))
    inner = mask.filter(ImageFilter.MinFilter(13))
    ring = ImageChops.subtract(outer, inner)
    ring_layer = Image.new("RGBA", HI, rgba(palette["trim"], 0))
    ring_layer.putalpha(ring.point(lambda value: min(120, int(value * 0.58))))
    overlay = Image.alpha_composite(overlay, ring_layer)
    draw = ImageDraw.Draw(overlay)

    x = SCREEN["x"] * SCALE
    y = SCREEN["y"] * SCALE
    w = SCREEN["width"] * SCALE
    h = SCREEN["height"] * SCALE
    r = SCREEN["radius"] * SCALE
    draw_lcd_screen(draw, palette, x, y, w, h, r)
    draw.rounded_rectangle((59 * SCALE, 24 * SCALE, 133 * SCALE, 38 * SCALE), radius=7 * SCALE, fill=rgba(palette["trim"], 46), outline=rgba(palette["accent"], 64), width=SCALE)
    draw.rounded_rectangle((83 * SCALE, 29 * SCALE, 109 * SCALE, 33 * SCALE), radius=3 * SCALE, fill=rgba(palette["accent"], 132))
    draw.ellipse((68 * SCALE, 29 * SCALE, 74 * SCALE, 35 * SCALE), fill=rgba(palette["trim"], 54))
    draw.ellipse((118 * SCALE, 29 * SCALE, 124 * SCALE, 35 * SCALE), fill=rgba(palette["trim"], 54))
    draw.rounded_rectangle((68 * SCALE, 171 * SCALE, 124 * SCALE, 184 * SCALE), radius=7 * SCALE, fill=rgba(palette["trim"], 46), outline=rgba(palette["accent"], 58), width=SCALE)
    draw.rounded_rectangle((79 * SCALE, 176 * SCALE, 98 * SCALE, 180 * SCALE), radius=2 * SCALE, fill=rgba(palette["accent"], 132))
    draw.rounded_rectangle((103 * SCALE, 176 * SCALE, 113 * SCALE, 180 * SCALE), radius=2 * SCALE, fill=rgba(palette["accent2"], 116))

    shell = Image.alpha_composite(shell, overlay)
    return shell.resize(CELL, Image.Resampling.LANCZOS)


def machine_svg(machine_id: str, machine: dict[str, Any]) -> str:
    palette = machine["palette"]
    x, y, w, h, r = SCREEN["x"], SCREEN["y"], SCREEN["width"], SCREEN["height"], SCREEN["radius"]
    if machine["pattern"] == "ribbons":
        pattern = f"""
  <path d="M30 142 C61 116 77 86 96 72 C115 86 131 116 162 142" fill="none" stroke="{palette['accent']}" stroke-opacity="0.44" stroke-width="8" stroke-linecap="round"/>
  <path d="M34 164 C63 150 80 135 96 130 C112 135 129 150 158 164" fill="none" stroke="{palette['accent2']}" stroke-opacity="0.32" stroke-width="6" stroke-linecap="round"/>
"""
    else:
        dots = []
        for row, yy in enumerate((72, 91, 112, 135, 156)):
            span = 28 + row * 6
            for step in range(4):
                xx = 96 - span + step * 12
                for mirrored in (xx, 192 - xx):
                    dots.append(
                        f'<circle cx="{mirrored:.1f}" cy="{yy + math.sin(step * 1.4 + row) * 3:.1f}" r="{2.2 + (step % 2) * 0.9:.1f}" fill="{palette["accent"]}" fill-opacity="0.28"/>'
                    )
        pattern = "\n  ".join(dots)
    grid_lines = []
    for gx in range(x + 10, x + w - 8, 4):
        grid_lines.append(f'<path d="M{gx} {y + 9} V{y + h - 9}"/>')
    for gy in range(y + 10, y + h - 8, 4):
        grid_lines.append(f'<path d="M{x + 9} {gy} H{x + w - 9}"/>')
    grid = "\n    ".join(grid_lines)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 208" width="192" height="208" data-machine="{machine_id}">
  <defs>
    <linearGradient id="body" x1="96" y1="8" x2="96" y2="200" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="{palette['top']}" stop-opacity="0.94"/>
      <stop offset="0.48" stop-color="{palette['mid']}" stop-opacity="0.9"/>
      <stop offset="1" stop-color="{palette['bottom']}" stop-opacity="0.9"/>
    </linearGradient>
    <filter id="glow" x="-40%" y="-35%" width="180%" height="170%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <path d="{EGG_PATH_D}"
    fill="url(#body)" stroke="{palette['trim']}" stroke-opacity="0.38" stroke-width="2.6" filter="url(#glow)"/>
{pattern}
  <rect x="{x - 9}" y="{y - 9}" width="{w + 18}" height="{h + 18}" rx="{r + 9}" fill="{palette['trim']}" fill-opacity="0.24" stroke="{palette['accent']}" stroke-opacity="0.33"/>
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{palette['screen']}" fill-opacity="0.97" stroke="{palette['screenInk']}" stroke-opacity="0.3"/>
  <rect x="{x + 7}" y="{y + 7}" width="{w - 14}" height="{h - 14}" rx="{max(1, r - 5)}" fill="none" stroke="{palette['screenGhost']}" stroke-opacity="0.2"/>
  <g stroke="{palette['screenGhost']}" stroke-opacity="0.08" stroke-width="1">
    {grid}
  </g>
  <g fill="none" stroke="{palette['screenInk']}" stroke-opacity="0.18" stroke-width="1">
    <rect x="{x + 13}" y="{y + 10}" width="6" height="4"/><rect x="{x + 25}" y="{y + 10}" width="6" height="4"/><rect x="{x + 37}" y="{y + 10}" width="6" height="4"/>
    <path d="M{x + 15} {y + h - 24} H{x + w - 15}"/>
    <path d="M{x + 20} {y + h - 24} L{x + 30} {y + h - 33} L{x + 42} {y + h - 24}"/>
    <path d="M{x + 78} {y + h - 24} L{x + 88} {y + h - 33} L{x + 100} {y + h - 24}"/>
    <rect x="{x + w - 28}" y="{y + 22}" width="14" height="6"/>
  </g>
  <rect x="59" y="24" width="74" height="14" rx="7" fill="{palette['trim']}" fill-opacity="0.18" stroke="{palette['accent']}" stroke-opacity="0.25"/>
  <rect x="83" y="29" width="26" height="4" rx="2" fill="{palette['accent']}" fill-opacity="0.52"/>
  <circle cx="71" cy="32" r="3" fill="{palette['trim']}" fill-opacity="0.22"/>
  <circle cx="121" cy="32" r="3" fill="{palette['trim']}" fill-opacity="0.22"/>
  <rect x="68" y="171" width="56" height="13" rx="7" fill="{palette['trim']}" fill-opacity="0.18" stroke="{palette['accent']}" stroke-opacity="0.23"/>
  <rect x="79" y="176" width="19" height="4" rx="2" fill="{palette['accent']}" fill-opacity="0.52"/>
  <rect x="103" y="176" width="10" height="4" rx="2" fill="{palette['accent2']}" fill-opacity="0.45"/>
</svg>
"""


def empty_grid() -> Grid:
    return [[0 for _ in range(PAWN_SOURCE[0])] for _ in range(PAWN_SOURCE[1])]


def px(grid: Grid, x: float, y: float, value: int = 1) -> None:
    ix, iy = int(round(x)), int(round(y))
    if 0 <= ix < PAWN_SOURCE[0] and 0 <= iy < PAWN_SOURCE[1]:
        grid[iy][ix] = value


def rect(grid: Grid, x: float, y: float, w: float, h: float, value: int = 1) -> None:
    x0, y0 = int(round(x)), int(round(y))
    x1, y1 = int(round(x + w)), int(round(y + h))
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            px(grid, xx, yy, value)


def add_ellipse(grid: Grid, cx: float, cy: float, rx: float, ry: float, value: int = 1) -> None:
    for y in range(PAWN_SOURCE[1]):
        for x in range(PAWN_SOURCE[0]):
            if ((x + 0.5 - cx) / max(0.1, rx)) ** 2 + ((y + 0.5 - cy) / max(0.1, ry)) ** 2 <= 1:
                grid[y][x] = value


def add_polygon(grid: Grid, points: list[tuple[float, float]], value: int = 1) -> None:
    mask = Image.new("1", PAWN_SOURCE, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(int(round(x)), int(round(y))) for x, y in points], fill=1)
    mask_px = mask.load()
    for y in range(PAWN_SOURCE[1]):
        for x in range(PAWN_SOURCE[0]):
            if mask_px[x, y]:
                grid[y][x] = value


def draw_line(grid: Grid, x0: float, y0: float, x1: float, y1: float, value: int = 1) -> None:
    x0_i, y0_i, x1_i, y1_i = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    dx = abs(x1_i - x0_i)
    sx = 1 if x0_i < x1_i else -1
    dy = -abs(y1_i - y0_i)
    sy = 1 if y0_i < y1_i else -1
    err = dx + dy
    while True:
        px(grid, x0_i, y0_i, value)
        if x0_i == x1_i and y0_i == y1_i:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0_i += sx
        if e2 <= dx:
            err += dx
            y0_i += sy


def is_filled(grid: Grid, x: int, y: int) -> bool:
    return 0 <= x < PAWN_SOURCE[0] and 0 <= y < PAWN_SOURCE[1] and grid[y][x] != 0


def edge_shade(grid: Grid, min_y: int = 8, shade_value: int = INK_SECONDARY) -> None:
    for y in range(PAWN_SOURCE[1] - 1, -1, -1):
        for x in range(PAWN_SOURCE[0] - 1, -1, -1):
            if grid[y][x] == INK_MAIN:
                right_open = not is_filled(grid, x + 1, y)
                below_open = not is_filled(grid, x, y + 1)
                if y >= min_y and (right_open or below_open or y >= 20):
                    grid[y][x] = shade_value


def form_size(form: FormSpec) -> tuple[float, float, float]:
    family = form.family
    stage = form.stage
    branch = form.branch or ""
    if family == "mais":
        table = {
            "egg": (10.0, 13.0, 12.0),
            "hatchling": (10.5, 14.0, 11.8),
            "child": (12.8, 17.0, 10.8),
            "teen": (13.8, 19.0, 10.2),
            "adult": (15.2, 21.0, 9.8),
            "hibernation": (17.0, 10.5, 14.5),
        }
    elif family == "duck":
        table = {
            "egg": (11.0, 13.0, 12.2),
            "hatchling": (13.0, 12.0, 12.2),
            "child": (16.0, 14.0, 11.5),
            "teen": (18.0, 15.5, 11.0),
            "adult": (20.0, 16.5, 10.8),
            "hibernation": (18.0, 9.5, 14.5),
        }
    else:
        table = {
            "egg": (11.5, 12.8, 12.2),
            "hatchling": (13.0, 12.5, 12.0),
            "child": (17.0, 15.0, 11.0),
            "teen": (18.5, 16.5, 10.5),
            "adult": (20.2, 18.0, 10.2),
            "hibernation": (18.5, 10.2, 14.6),
        }
    w, h, cy = table.get(stage, table["child"])
    if branch == "resilient":
        w += 1.0
        h += 0.6
        cy += 0.2
    elif branch == "quiet":
        w -= 1.0
        h -= 0.8
        cy += 0.5
    elif branch == "sleepy":
        w += 0.4
        h -= 1.4
        cy += 1.4
    elif branch == "restless":
        h += 0.8
    elif branch == "focused":
        w -= 0.6
        h += 0.4
    return w, h, cy


def palette_policy(palette: dict[str, str]) -> str:
    return M21_PALETTE_POLICY if "mainShade" in palette and "accent" in palette else M2_PALETTE_POLICY


def palette_roles(palette: dict[str, str]) -> dict[int, str]:
    roles = {
        INK_MAIN: "main",
        INK_SECONDARY: "secondary" if "secondary" in palette else "shade",
    }
    if "mainShade" in palette:
        roles[INK_MAIN_SHADE] = "mainShade"
    if "accent" in palette:
        roles[INK_STAGE_ACCENT] = "accent"
    return roles


def shade_ink(form: FormSpec) -> int:
    return INK_SECONDARY


def face_ink(form: FormSpec) -> int:
    return INK_MAIN_SHADE if "mainShade" in form.palette else 0


def accent_key(stage: str, branch: str | None) -> str:
    return f"{stage}.{branch}" if branch else stage


def stage_accent(profile: Profile, stage: str, branch: str | None) -> str:
    key = accent_key(stage, branch)
    family_accents = FAMILY_STAGE_ACCENTS.get(profile.family, {})
    return (
        profile.stage_accents.get(key)
        or profile.stage_accents.get(stage)
        or family_accents.get(key)
        or family_accents.get(stage)
        or DEFAULT_STAGE_ACCENTS.get(key)
        or DEFAULT_STAGE_ACCENTS.get(stage)
        or DEFAULT_STAGE_ACCENTS["child"]
    )


def form_palette(profile: Profile, stage: str, branch: str | None, asset_version: str) -> dict[str, str]:
    base = dict(profile.palette)
    if asset_version != "m2.1":
        return base

    main = base["main"]
    secondary = base.get("secondary", base.get("shade", tone_hex(main, -0.26, 0.02)))
    accent = stage_accent(profile, stage, branch)
    return {
        "main": main,
        "secondary": secondary,
        "shade": secondary,
        "mainShade": base.get("mainShade", LCD_FACE_INK),
        "accent": accent,
        "identityMain": main,
        "identitySecondary": secondary,
        "identityAccent": accent,
    }


def pose_traits(name: str, form: FormSpec) -> dict[str, Any]:
    traits: dict[str, Any] = {
        "dx": 0.0,
        "dy": 0.0,
        "sx": 1.0,
        "sy": 1.0,
        "eyes": "open",
        "mouth": "soft",
        "limbs": "stand",
        "action": "",
    }
    overrides: dict[str, dict[str, Any]] = {
        "idle_1": {"dy": 0.5},
        "blink": {"eyes": "blink"},
        "squash": {"dy": 1.1, "sx": 1.12, "sy": 0.78, "limbs": "tuck"},
        "wave_1": {"mouth": "happy", "limbs": "wave", "action": "wave"},
        "wave_2": {"eyes": "blink", "mouth": "happy", "limbs": "wave", "action": "wave_high"},
        "jump_0": {"dy": 1.2, "sx": 1.04, "sy": 0.86, "mouth": "happy", "limbs": "tuck"},
        "jump_1": {"dy": -2.6, "mouth": "happy", "limbs": "float"},
        "jump_2": {"dy": -4.0, "eyes": "blink", "mouth": "happy", "limbs": "float"},
        "failed_0": {"dy": 0.8, "eyes": "failed", "mouth": "flat"},
        "failed_1": {"dy": 1.5, "sx": 1.06, "sy": 0.84, "eyes": "failed", "mouth": "flat", "limbs": "tuck"},
        "failed_2": {"dy": 2.0, "sx": 1.12, "sy": 0.72, "eyes": "failed", "mouth": "flat", "limbs": "tuck"},
        "wait_0": {"eyes": "up"},
        "wait_1": {"dy": -0.4, "eyes": "up", "mouth": "caret"},
        "wait_2": {"dx": 0.5, "eyes": "up", "mouth": "caret"},
        "work_0": {"mouth": "caret", "action": "work"},
        "work_1": {"dy": 0.5, "mouth": "caret", "action": "work"},
        "work_2": {"eyes": "blink", "mouth": "caret", "action": "work"},
        "review_0": {"eyes": "up", "action": "review"},
        "review_1": {"dy": -0.6, "mouth": "happy", "action": "review"},
        "review_2": {"dy": -0.6, "eyes": "blink", "mouth": "happy", "action": "review"},
        "step_right_0": {"dx": 0.6, "limbs": "right"},
        "step_right_1": {"dx": 1.5, "dy": 0.5, "limbs": "right"},
        "step_right_2": {"dx": 2.4, "dy": 1.0, "sx": 1.02, "sy": 0.9, "limbs": "tuck"},
        "step_left_0": {"dx": -0.6, "limbs": "left"},
        "step_left_1": {"dx": -1.5, "dy": 0.5, "limbs": "left"},
        "step_left_2": {"dx": -2.4, "dy": 1.0, "sx": 1.02, "sy": 0.9, "limbs": "tuck"},
    }
    traits.update(overrides.get(name, {}))
    if form.stage == "egg":
        traits["limbs"] = ""
        traits["eyes"] = "none"
        traits["mouth"] = "none"
        if name in {"jump_1", "jump_2", "work_1", "review_1", "wave_2"}:
            traits["action"] = "crack"
        elif name.startswith("step_right"):
            traits["dx"] += 0.8
        elif name.startswith("step_left"):
            traits["dx"] -= 0.8
    if form.stage == "hibernation":
        traits["eyes"] = "sleep"
        traits["mouth"] = "soft"
        traits["limbs"] = "tuck"
        traits["action"] = "rest"
        traits["dy"] = max(traits["dy"], 0.7)
        traits["sx"] = max(traits["sx"], 1.08)
        traits["sy"] = min(traits["sy"], 0.78)
    if form.branch == "sleepy":
        traits["eyes"] = "sleep" if name not in {"jump_1", "jump_2"} else "blink"
    if form.branch == "worker" and name.startswith(("work", "review")):
        traits["action"] = "work_plus" if name.startswith("work") else "review_plus"
    if form.branch == "quiet" and name.startswith("wave"):
        traits["action"] = ""
        traits["mouth"] = "soft"
    return traits


def detail_ink(form: FormSpec) -> int:
    return INK_MAIN_SHADE if "mainShade" in form.palette else INK_SECONDARY


def draw_face(grid: Grid, cx: float, cy: float, eyes: str, mouth: str, ink: int = 0) -> None:
    if eyes == "none":
        return
    if eyes == "open":
        rect(grid, cx - 4.5, cy - 1.0, 2, 2, ink)
        rect(grid, cx + 3.0, cy - 1.0, 2, 2, ink)
    elif eyes == "blink":
        rect(grid, cx - 4.7, cy, 3, 1, ink)
        rect(grid, cx + 2.2, cy, 3, 1, ink)
    elif eyes == "sleep":
        draw_line(grid, cx - 5, cy, cx - 3, cy + 1, ink)
        draw_line(grid, cx + 3, cy + 1, cx + 5, cy, ink)
    elif eyes == "up":
        rect(grid, cx - 4.5, cy - 2.0, 2, 2, ink)
        rect(grid, cx + 3.0, cy - 2.0, 2, 2, ink)
    elif eyes == "failed":
        for dx, dy in ((-5, -1), (-4, 0), (-3, 1), (3, 1), (4, 0), (5, -1)):
            px(grid, cx + dx, cy + dy, ink)

    if mouth == "none":
        return
    if mouth == "soft":
        rect(grid, cx - 1, cy + 4, 2, 1, ink)
    elif mouth == "caret":
        px(grid, cx - 1, cy + 4, ink)
        px(grid, cx, cy + 5, ink)
        px(grid, cx + 1, cy + 4, ink)
    elif mouth == "flat":
        rect(grid, cx - 3, cy + 4, 6, 1, ink)
    elif mouth == "happy":
        px(grid, cx - 2, cy + 3, ink)
        px(grid, cx - 1, cy + 4, ink)
        px(grid, cx, cy + 4, ink)
        px(grid, cx + 1, cy + 4, ink)
        px(grid, cx + 2, cy + 3, ink)


def draw_limbs(grid: Grid, cx: float, cy: float, mode: str, stage: str, family: str) -> None:
    if not mode or stage == "egg":
        return
    y = 20 if stage in {"teen", "adult"} else 19
    arm_y = cy + 3
    arm_span = 7 if family == "toast" else 6
    if stage == "hatchling":
        y = 18
        arm_span -= 1
    elif stage == "hibernation":
        y = 19
        arm_span = 6

    def foot(x: float, yy: float = y, step: int = 0) -> None:
        draw_line(grid, x, yy - 1 + step, x, yy + 1 + step, INK_SECONDARY)
        draw_line(grid, x - 1, yy + 1 + step, x + 1, yy + 1 + step, INK_SECONDARY)

    def arm(side: int, lift: float = 0.0, reach: float = 2.0) -> None:
        shoulder_x = cx + side * arm_span
        hand_x = shoulder_x + side * reach
        hand_y = arm_y - lift
        draw_line(grid, shoulder_x, arm_y, hand_x, hand_y, INK_SECONDARY)
        px(grid, hand_x + side * 0.5, hand_y, INK_SECONDARY)

    if stage == "hibernation":
        draw_line(grid, cx - 5, y, cx + 5, y, INK_SECONDARY)
    elif mode == "stand":
        arm(-1)
        arm(1)
        foot(cx - 4)
        foot(cx + 4)
    elif mode == "tuck":
        draw_line(grid, cx - 5, y, cx - 2, y + 1, INK_SECONDARY)
        draw_line(grid, cx + 5, y, cx + 2, y + 1, INK_SECONDARY)
    elif mode == "right":
        arm(-1, lift=-1, reach=2)
        arm(1, lift=1, reach=3)
        foot(cx - 3, y, 0)
        foot(cx + 5, y, -1)
    elif mode == "left":
        arm(-1, lift=1, reach=3)
        arm(1, lift=-1, reach=2)
        foot(cx - 5, y, -1)
        foot(cx + 3, y, 0)
    elif mode == "float":
        arm(-1, lift=2, reach=2)
        arm(1, lift=2, reach=2)
        draw_line(grid, cx - 4, y - 1, cx - 5, y, INK_SECONDARY)
        draw_line(grid, cx + 4, y - 1, cx + 5, y, INK_SECONDARY)
    elif mode == "wave":
        arm(-1)
        foot(cx - 4)
        foot(cx + 4)


def draw_action(grid: Grid, cx: float, cy: float, action: str, family: str) -> None:
    if action == "wave":
        draw_line(grid, cx + 7, cy + 2, cx + 9, cy - 1, INK_SECONDARY)
        draw_line(grid, cx + 9, cy - 1, cx + 8, cy - 4, INK_SECONDARY)
        px(grid, cx + 9, cy - 5, INK_SECONDARY)
    elif action == "wave_high":
        draw_line(grid, cx + 7, cy + 2, cx + 9, cy - 2, INK_SECONDARY)
        draw_line(grid, cx + 9, cy - 2, cx + 8, cy - 6, INK_SECONDARY)
        px(grid, cx + 9, cy - 7, INK_SECONDARY)
    elif action == "work":
        rect(grid, cx - 6, 22, 12, 2, 2)
        rect(grid, cx - 4, 22, 2, 1, 1)
        rect(grid, cx + 2, 22, 2, 1, 1)
        rect(grid, cx + 7, 14, 4, 3, 2)
    elif action == "work_plus":
        rect(grid, cx - 7, 21, 14, 3, 2)
        rect(grid, cx - 5, 21, 2, 1, 1)
        rect(grid, cx, 21, 2, 1, 1)
        rect(grid, cx + 5, 21, 2, 1, 1)
        rect(grid, cx + 7, 13, 5, 4, 2)
    elif action == "review":
        rect(grid, cx + 7, 10, 2, 3, 2)
        rect(grid, cx + 9, 5, 5, 7, 2)
        rect(grid, cx + 10, 6, 3, 1, 0)
        rect(grid, cx + 10, 9, 3, 1, 0)
        px(grid, cx + 13, 11, 0)
    elif action == "review_plus":
        rect(grid, cx + 7, 10, 2, 3, 2)
        rect(grid, cx + 9, 4, 6, 8, 2)
        rect(grid, cx + 10, 6, 4, 1, 0)
        rect(grid, cx + 10, 9, 4, 1, 0)
        px(grid, cx + 14, 11, 0)
    elif action == "crack":
        draw_line(grid, cx, cy - 6, cx - 1, cy - 3, 0)
        draw_line(grid, cx - 1, cy - 3, cx + 1, cy - 1, 0)
        draw_line(grid, cx + 1, cy - 1, cx, cy + 2, 0)
    elif action == "rest":
        rect(grid, cx + 5, cy - 2, 3, 1, 2)
        rect(grid, cx + 6, cy - 4, 2, 1, 2)


def px_masked(grid: Grid, x: float, y: float, value: int = INK_STAGE_ACCENT, allow_adjacent: bool = False) -> None:
    ix, iy = int(round(x)), int(round(y))
    if not (0 <= ix < PAWN_SOURCE[0] and 0 <= iy < PAWN_SOURCE[1]):
        return
    if is_filled(grid, ix, iy):
        grid[iy][ix] = value
        return
    if allow_adjacent:
        for yy in range(iy - 1, iy + 2):
            for xx in range(ix - 1, ix + 2):
                if is_filled(grid, xx, yy):
                    grid[iy][ix] = value
                    return


def rect_masked(
    grid: Grid,
    x: float,
    y: float,
    w: float,
    h: float,
    value: int = INK_STAGE_ACCENT,
    allow_adjacent: bool = False,
) -> None:
    x0, y0 = int(round(x)), int(round(y))
    x1, y1 = int(round(x + w)), int(round(y + h))
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            px_masked(grid, xx, yy, value, allow_adjacent)


def draw_line_masked(
    grid: Grid,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    value: int = INK_STAGE_ACCENT,
) -> None:
    x0_i, y0_i, x1_i, y1_i = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    dx = abs(x1_i - x0_i)
    sx = 1 if x0_i < x1_i else -1
    dy = -abs(y1_i - y0_i)
    sy = 1 if y0_i < y1_i else -1
    err = dx + dy
    while True:
        px_masked(grid, x0_i, y0_i, value)
        if x0_i == x1_i and y0_i == y1_i:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0_i += sx
        if e2 <= dx:
            err += dx
            y0_i += sy


def draw_stage_accent(grid: Grid, form: FormSpec, cx: float, cy: float) -> None:
    if "accent" not in form.palette:
        return

    if form.stage == "egg":
        rect_masked(grid, cx - 4, cy - 3, 8, 1)
        rect_masked(grid, cx - 3, cy - 2, 6, 1)
    elif form.stage == "hatchling":
        rect_masked(grid, cx - 1, cy - 6, 2, 2)
        px_masked(grid, cx - 2, cy - 5)
        px_masked(grid, cx + 2, cy - 5)
        px_masked(grid, cx, cy - 4)
    elif form.stage == "child":
        rect_masked(grid, cx - 3, cy + 4, 6, 1)
        px_masked(grid, cx - 1, cy + 5)
        px_masked(grid, cx, cy + 6)
        px_masked(grid, cx + 1, cy + 5)
    elif form.stage == "teen":
        if form.branch == "focused":
            rect_masked(grid, cx - 6, cy - 6, 12, 1)
            rect_masked(grid, cx - 4, cy - 5, 8, 1)
        elif form.branch == "resilient":
            draw_line_masked(grid, cx - 6, cy - 4, cx + 5, cy + 6)
            draw_line_masked(grid, cx - 5, cy - 4, cx + 6, cy + 5)
        elif form.branch == "restless":
            rect_masked(grid, cx + 4, cy - 5, 2, 2)
            px_masked(grid, cx + 3, cy - 4)
            px_masked(grid, cx + 6, cy - 4)
            px_masked(grid, cx + 5, cy - 3)
    elif form.stage == "adult":
        if form.branch == "worker":
            rect_masked(grid, cx - 6, cy - 8, 12, 1, allow_adjacent=True)
            rect_masked(grid, cx - 4, cy - 7, 8, 1, allow_adjacent=True)
            rect_masked(grid, cx - 1, cy + 4, 3, 1)
            rect_masked(grid, cx, cy + 5, 1, 3)
        elif form.branch == "calm":
            rect_masked(grid, cx - 4, cy + 4, 3, 2)
            rect_masked(grid, cx + 1, cy + 4, 3, 2)
            px_masked(grid, cx, cy + 5)
        elif form.branch == "resilient":
            draw_line_masked(grid, cx - 6, cy - 3, cx + 5, cy + 6)
            rect_masked(grid, cx - 5, cy + 4, 10, 1)
        elif form.branch == "quiet":
            rect_masked(grid, cx - 5, cy + 5, 10, 1)
            px_masked(grid, cx - 5, cy + 6)
            px_masked(grid, cx + 5, cy + 6)
        elif form.branch == "sleepy":
            rect_masked(grid, cx - 5, cy - 6, 8, 1)
            rect_masked(grid, cx + 2, cy - 5, 2, 1, allow_adjacent=True)
            rect_masked(grid, cx - 5, cy + 6, 10, 1)
    elif form.stage == "hibernation":
        rect_masked(grid, cx - 7, cy + 2, 14, 2)
        rect_masked(grid, cx + 4, cy, 4, 1, allow_adjacent=True)


def draw_toast(grid: Grid, form: FormSpec, traits: dict[str, Any]) -> tuple[float, float]:
    w, h, base_cy = form_size(form)
    cx = 12 + traits["dx"]
    cy = base_cy + traits["dy"]
    w *= traits["sx"]
    h *= traits["sy"]
    if form.stage == "egg":
        add_ellipse(grid, cx, cy, w / 2, h / 2, INK_SECONDARY)
        add_ellipse(grid, cx, cy, max(1, w / 2 - 1.2), max(1, h / 2 - 1.2), INK_MAIN)
        rect(grid, cx - w / 2 + 2, cy + h / 2 - 3, w - 4, 1, INK_SECONDARY)
        return cx, cy
    if form.stage == "hibernation":
        add_ellipse(grid, cx, cy + 2, w / 2, h / 2, INK_SECONDARY)
        rect(grid, cx - w / 2 + 1, cy + 1, w - 2, h / 2 + 1, INK_SECONDARY)
        add_ellipse(grid, cx, cy + 1.4, max(1, w / 2 - 1.6), max(1, h / 2 - 1.6), INK_MAIN)
        rect(grid, cx - w / 2 + 3, cy + 2, w - 6, h / 2 - 1, INK_MAIN)
        draw_line(grid, cx - 8, cy + 5, cx + 8, cy + 5, INK_SECONDARY)
        return cx, cy + 1

    top = cy - h / 2
    left = cx - w / 2
    add_ellipse(grid, cx, top + 4.8, w / 2, 4.7, INK_SECONDARY)
    rect(grid, left, top + 4.5, w, h - 4.5, INK_SECONDARY)
    add_ellipse(grid, cx, top + 5.2, max(1, w / 2 - 1.3), 3.5, INK_MAIN)
    rect(grid, left + 1.2, top + 5.8, w - 2.4, h - 7.2, INK_MAIN)
    rect(grid, left + 2.0, top + h - 2.0, w - 4.0, 1.0, INK_MAIN)
    draw_line(grid, left + 2, top + h - 1, left + w - 3, top + h - 1, INK_SECONDARY)
    if form.branch == "resilient":
        draw_line(grid, left + 3, top + 6, left + 6, top + 8, detail_ink(form))
        rect(grid, left + 4, top + h - 5, 4, 1, INK_SECONDARY)
    elif form.branch == "focused":
        rect(grid, cx - 4, top + 3, 8, 1, INK_SECONDARY)
    elif form.branch == "restless":
        draw_line(grid, cx + 5, top + 5, cx + 7, top + 3, INK_SECONDARY)
    elif form.branch == "quiet":
        rect(grid, left + 3, top + h - 4, w - 6, 1, INK_SECONDARY)
    elif form.branch == "sleepy":
        rect(grid, left + 4, top + h - 5, w - 8, 1, INK_SECONDARY)
    return cx, cy


def draw_mais(grid: Grid, form: FormSpec, traits: dict[str, Any]) -> tuple[float, float]:
    w, h, base_cy = form_size(form)
    cx = 12 + traits["dx"]
    cy = base_cy + traits["dy"]
    w *= traits["sx"]
    h *= traits["sy"]
    if form.stage == "egg":
        add_ellipse(grid, cx, cy, w / 2, h / 2, INK_MAIN)
        rect(grid, cx - w / 2 + 2, cy + h / 2 - 3, w - 4, 1, INK_SECONDARY)
        draw_line(grid, cx - 4, cy + 3, cx - 7, cy + 6, INK_SECONDARY)
        draw_line(grid, cx + 4, cy + 3, cx + 7, cy + 6, INK_SECONDARY)
        return cx, cy
    if form.stage == "hibernation":
        add_ellipse(grid, cx, cy + 2, w / 2, h / 2, INK_MAIN)
        draw_line(grid, cx - 8, cy + 3, cx - 4, cy + 6, INK_SECONDARY)
        draw_line(grid, cx + 8, cy + 3, cx + 4, cy + 6, INK_SECONDARY)
        rect(grid, cx - 5, cy + 4, 10, 1, INK_SECONDARY)
        return cx, cy + 1

    top = cy - h / 2
    bottom = cy + h / 2
    rx = w / 2
    cap = min(rx, 4.6)
    add_ellipse(grid, cx, top + cap, rx, cap, INK_MAIN)
    rect(grid, cx - rx, top + cap, w, h - cap * 2, INK_MAIN)
    add_ellipse(grid, cx, bottom - cap, rx, cap, INK_MAIN)
    draw_line(grid, cx - rx + 1, top + cap, cx - rx + 1, bottom - cap, INK_SECONDARY)
    draw_line(grid, cx + rx - 1, top + cap, cx + rx - 1, bottom - cap, INK_SECONDARY)
    kernel_marks = [(-3, 6), (2, 7), (0, 11), (-2, 15), (3, 16)]
    if form.stage == "adult":
        kernel_marks.append((0, 19))
    for x_offset, y_offset in kernel_marks:
        y = top + y_offset
        if y < bottom - 4:
            draw_line(grid, cx + x_offset, y, cx + x_offset, y + 1, detail_ink(form))
    draw_line(grid, cx - rx + 1, bottom - 4, cx - 4, bottom + 1, INK_SECONDARY)
    draw_line(grid, cx + rx - 1, bottom - 4, cx + 4, bottom + 1, INK_SECONDARY)
    draw_line(grid, cx - 4, bottom, cx - 7, bottom + 2, INK_SECONDARY)
    draw_line(grid, cx + 4, bottom, cx + 7, bottom + 2, INK_SECONDARY)
    if form.branch == "focused":
        draw_line(grid, cx, top + 3, cx, bottom - 5, INK_SECONDARY)
    elif form.branch == "resilient":
        rect(grid, cx - rx + 2, bottom - 6, w - 4, 1, INK_SECONDARY)
    elif form.branch == "restless":
        draw_line(grid, cx, top - 2, cx + 2, top + 1, INK_SECONDARY)
        draw_line(grid, cx + 2, top + 1, cx + 4, top - 1, INK_SECONDARY)
    elif form.branch == "quiet":
        rect(grid, cx - 3, bottom - 4, 6, 1, INK_SECONDARY)
    elif form.branch == "sleepy":
        draw_line(grid, cx - 6, bottom - 4, cx - 1, bottom, INK_SECONDARY)
    return cx, cy


def draw_duck(grid: Grid, form: FormSpec, traits: dict[str, Any]) -> tuple[float, float]:
    w, h, base_cy = form_size(form)
    cx = 12 + traits["dx"]
    cy = base_cy + traits["dy"]
    w *= traits["sx"]
    h *= traits["sy"]
    if form.stage == "egg":
        add_ellipse(grid, cx, cy, w / 2, h / 2, 1)
        rect(grid, cx - 4, cy + h / 2 - 3, 8, 2, 2)
        return cx, cy
    if form.stage == "hibernation":
        add_ellipse(grid, cx - 1, cy + 2, w / 2, h / 2, 1)
        rect(grid, cx + 4, cy + 1, 5, 2, 2)
        rect(grid, cx - 6, cy + 4, 10, 2, 2)
        return cx - 1, cy + 1

    top = cy - h / 2
    body_cy = cy + 2
    add_ellipse(grid, cx - 1, body_cy, w / 2, h / 2, 1)
    add_ellipse(grid, cx + 2, top + 4.7, max(4.5, w / 3), max(4.0, h / 3), 1)
    rect(grid, cx + 5, top + 5, 5, 2, 2)
    rect(grid, cx + 5, top + 7, 4, 1, 2)
    add_ellipse(grid, cx - 4, body_cy + 2, max(3.0, w / 4), max(2.0, h / 4), 2)
    if form.branch == "focused":
        rect(grid, cx - 1, top + 3, 5, 1, 2)
    elif form.branch == "resilient":
        rect(grid, cx - 6, body_cy + 5, 9, 2, 2)
    elif form.branch == "restless":
        px(grid, cx + 1, top - 1, 2)
        px(grid, cx + 2, top - 2, 2)
    elif form.branch == "quiet":
        rect(grid, cx - 6, body_cy + 4, 8, 1, 2)
    elif form.branch == "sleepy":
        rect(grid, cx + 4, top + 7, 5, 2, 2)
    edge_shade(grid, int(top + 6), shade_ink(form))
    return cx + 1, top + 6


def make_pose(form: FormSpec, pose_name: str) -> list[str]:
    grid = empty_grid()
    traits = pose_traits(pose_name, form)
    if form.family == "mais":
        face_cx, face_cy = draw_mais(grid, form, traits)
        face_cy -= 1.0 if form.stage in {"child", "teen", "adult"} else 0.0
    elif form.family == "duck":
        face_cx, face_cy = draw_duck(grid, form, traits)
    else:
        face_cx, face_cy = draw_toast(grid, form, traits)
    draw_limbs(grid, face_cx, face_cy, traits["limbs"], form.stage, form.family)
    draw_action(grid, face_cx, face_cy, traits["action"], form.family)
    draw_stage_accent(grid, form, face_cx, face_cy)
    draw_face(grid, face_cx, face_cy, traits["eyes"], traits["mouth"], face_ink(form))
    return ["".join(GRID_SYMBOLS[value] for value in row) for row in grid]


def pose_image(lines: list[str], palette: dict[str, str]) -> Image.Image:
    colors = {
        GRID_SYMBOLS[ink]: (*rgb(palette[role]), 255)
        for ink, role in palette_roles(palette).items()
    }
    image = Image.new("RGBA", PAWN_SOURCE, (0, 0, 0, 0))
    pixels = image.load()
    for y, line in enumerate(lines):
        for x, symbol in enumerate(line):
            if symbol in colors:
                pixels[x, y] = colors[symbol]
    return image


def validate_pose(image: Image.Image, palette: dict[str, str]) -> dict[str, Any]:
    allowed = {(0, 0, 0, 0)}
    for role in palette_roles(palette).values():
        allowed.add((*rgb(palette[role]), 255))
    colors = image.convert("RGBA").getcolors(maxcolors=1_000_000)
    if colors is None:
        raise SystemExit("too many colors in pose")
    rgba_colors = sorted(color for _count, color in colors)
    bad = [color for color in rgba_colors if color not in allowed]
    return {
        "color_count": len(rgba_colors),
        "colors_rgba": [list(color) for color in rgba_colors],
        "bad_colors": [list(color) for color in bad],
    }


def write_pose_text(path: Path, poses: dict[str, list[str]]) -> None:
    chunks: list[str] = []
    for name, lines in poses.items():
        chunks.append(f"@{name}")
        chunks.extend(lines)
        chunks.append("")
    path.write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")


def build_pet(pets_root: Path, form: FormSpec) -> dict[str, Any]:
    root = pets_root / form.id
    poses_dir = root / "poses"
    poses_dir.mkdir(parents=True, exist_ok=True)
    poses = {name: make_pose(form, name) for name in POSE_NAMES}
    write_pose_text(root / "poses.txt", poses)

    images: dict[str, Image.Image] = {}
    validation: dict[str, Any] = {}
    for name, lines in poses.items():
        image = pose_image(lines, form.palette)
        images[name] = image
        image.save(poses_dir / f"{name}.png")
        validation[name] = validate_pose(image, form.palette)

    columns = 8
    rows = math.ceil(len(images) / columns)
    atlas = Image.new("RGBA", (columns * PAWN_SOURCE[0], rows * PAWN_SOURCE[1]), (0, 0, 0, 0))
    for index, image in enumerate(images.values()):
        atlas.alpha_composite(image, ((index % columns) * PAWN_SOURCE[0], (index // columns) * PAWN_SOURCE[1]))
    atlas.save(root / "poses_atlas.png")
    atlas.resize((atlas.width * 4, atlas.height * 4), Image.Resampling.NEAREST).save(root / "poses_atlas_x4.png")

    write_json(
        root / "pose_manifest.json",
        {
            "id": form.id,
            "displayName": form.display_name,
            "description": form.description,
            "lineId": form.line_id,
            "lineDisplayName": form.line_display_name,
            "inspiration": form.inspiration,
            "stage": form.stage,
            "branch": form.branch,
            "sourceSize": list(PAWN_SOURCE),
            "ink": form.palette["main"],
            "palette": {
                **form.palette,
                "policy": palette_policy(form.palette),
                "lcdRuleset": LCD_RULESET_ID if "identityMain" in form.palette else None,
                "artDirection": ART_DIRECTION_ID if "identityMain" in form.palette else None,
                "symbolMap": {
                    GRID_SYMBOLS[ink]: role
                    for ink, role in palette_roles(form.palette).items()
                },
            },
            "artDirection": {
                "id": ART_DIRECTION_ID,
                "summary": "Clean 1px mascot silhouettes with visible hands, feet, and sparse intentional linework.",
                "noisePolicy": "No random dither or speckle fields; secondary/detail pixels must read as outline, limb, prop, or accessory.",
            },
            "transparent": "#00000000",
            "poses": list(poses.keys()),
            "atlas": {"path": "poses_atlas.png", "columns": columns, "cell": list(PAWN_SOURCE)},
        },
    )
    write_json(
        root / "runtime_motion.json",
        {
            "unit": "source_px",
            "screen": SCREEN,
            "pawnSource": list(PAWN_SOURCE),
            "defaultPawnScale": PAWN_SCALE,
            "lcdRuleset": LCD_RULESET_ID,
            "artDirection": ART_DIRECTION_ID,
            "states": RUNTIME_STATES,
        },
    )
    return {
        "pose_count": len(images),
        "atlas_size": [atlas.width, atlas.height],
        "bad_poses": [name for name, result in validation.items() if result["bad_colors"]],
        "validation": validation,
    }


def build_machine(tamago_root: Path, machine_id: str, machine: dict[str, Any], base_mask: Image.Image, mask_hash: str) -> dict[str, Any]:
    root = tamago_root / machine_id
    root.mkdir(parents=True, exist_ok=True)
    shell = build_machine_png(machine, base_mask)
    shell.save(root / "shell.png")
    shell.save(root / "shell.webp", format="WEBP", lossless=True, quality=100, method=6)
    (root / "shell.svg").write_text(machine_svg(machine_id, machine), encoding="utf-8")
    smask = screen_mask()
    smask.save(root / "screen_mask.png")
    viewport = {
        "cell": list(CELL),
        "screen": SCREEN,
        "pawnSource": list(PAWN_SOURCE),
        "defaultPawnScale": PAWN_SCALE,
        "shapePolicy": "shared symmetric goose-egg machine silhouette",
        "lcdRuleset": LCD_RULESET_ID,
        "screenMaskPolicy": "compiled Codex atlas clips pawn pixels to this LCD viewport mask",
    }
    write_json(root / "screen_viewport.json", viewport)
    write_json(
        root / "machine_manifest.json",
        {
            "id": machine_id,
            "displayName": machine["displayName"],
            "description": machine["description"],
            "shellSvg": "shell.svg",
            "shellPng": "shell.png",
            "shellWebp": "shell.webp",
            "screenMask": "screen_mask.png",
            "screenViewportPath": "screen_viewport.json",
            "outerSilhouetteHash": mask_hash,
            "outerSilhouetteSource": "shared SVG cubic path sampled into the raster mask",
            "lcdRuleset": LCD_RULESET_ID,
            "screenViewport": viewport,
        },
    )
    return {"outerSilhouetteHash": mask_hash, "screen": SCREEN, "pattern": machine["pattern"]}


def compose(shell: Image.Image, mask: Image.Image, pawn: Image.Image, offset: tuple[int, int] = (0, 0)) -> Image.Image:
    frame = Image.new("RGBA", CELL, (0, 0, 0, 0))
    frame.alpha_composite(shell)
    scaled = pawn.resize((PAWN_SOURCE[0] * PAWN_SCALE, PAWN_SOURCE[1] * PAWN_SCALE), Image.Resampling.NEAREST)
    x = SCREEN["x"] + (SCREEN["width"] - scaled.width) // 2 + offset[0] * PAWN_SCALE
    y = SCREEN["y"] + (SCREEN["height"] - scaled.height) // 2 + offset[1] * PAWN_SCALE
    layer = Image.new("RGBA", CELL, (0, 0, 0, 0))
    layer.alpha_composite(scaled, (x, y))
    alpha = Image.composite(layer.getchannel("A"), Image.new("L", CELL, 0), mask)
    layer.putalpha(alpha)
    frame.alpha_composite(layer)
    return frame


def load_profiles(paths: list[str]) -> list[Profile]:
    profiles: list[Profile] = []
    for path_value in paths:
        path = Path(path_value)
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles.append(
            Profile(
                id=data["id"],
                display_name=data.get("displayName", data["id"].title()),
                inspiration=data.get("inspiration", data.get("description", data["id"])),
                description=data.get("description", f"{data['id']} Tamacodex companion."),
                family=data.get("family", "generic"),
                palette=data["palette"],
                stage_accents=data.get("stageAccents", {}),
                source_path=str(path),
            )
        )
    return profiles


def forms_for_profile(profile: Profile, asset_version: str = "m2") -> list[FormSpec]:
    forms: list[FormSpec] = [
        FormSpec(
            id=f"{profile.id}_egg",
            display_name=f"{profile.display_name} Egg",
            description=f"Pre-hatch {profile.display_name} egg keyed to {profile.inspiration}.",
            line_id=profile.id,
            line_display_name=profile.display_name,
            inspiration=profile.inspiration,
            family=profile.family,
            stage="egg",
            branch=None,
            palette=form_palette(profile, "egg", None, asset_version),
        ),
        FormSpec(
            id=f"{profile.id}_hatchling",
            display_name=f"{profile.display_name} Hatchling",
            description=f"Tiny first-form {profile.display_name} before the child silhouette settles.",
            line_id=profile.id,
            line_display_name=profile.display_name,
            inspiration=profile.inspiration,
            family=profile.family,
            stage="hatchling",
            branch=None,
            palette=form_palette(profile, "hatchling", None, asset_version),
        ),
        FormSpec(
            id=profile.id,
            display_name=profile.display_name,
            description=profile.description,
            line_id=profile.id,
            line_display_name=profile.display_name,
            inspiration=profile.inspiration,
            family=profile.family,
            stage="child",
            branch=None,
            palette=form_palette(profile, "child", None, asset_version),
        ),
    ]
    for branch in TEEN_BRANCHES:
        forms.append(
            FormSpec(
                id=f"{profile.id}_teen_{branch}",
                display_name=f"{profile.display_name} Teen {branch.title()}",
                description=f"{profile.display_name}'s {branch} teen branch.",
                line_id=profile.id,
                line_display_name=profile.display_name,
                inspiration=profile.inspiration,
                family=profile.family,
                stage="teen",
                branch=branch,
                palette=form_palette(profile, "teen", branch, asset_version),
            )
        )
    for branch in ADULT_BRANCHES:
        forms.append(
            FormSpec(
                id=f"{profile.id}_adult_{branch}",
                display_name=f"{profile.display_name} Adult {branch.title()}",
                description=f"{profile.display_name}'s {branch} adult outcome.",
                line_id=profile.id,
                line_display_name=profile.display_name,
                inspiration=profile.inspiration,
                family=profile.family,
                stage="adult",
                branch=branch,
                palette=form_palette(profile, "adult", branch, asset_version),
            )
        )
    forms.append(
        FormSpec(
            id=f"{profile.id}_hibernation",
            display_name=f"{profile.display_name} Hibernation",
            description=f"Gentle long-idle hibernation form for {profile.display_name}.",
            line_id=profile.id,
            line_display_name=profile.display_name,
            inspiration=profile.inspiration,
            family=profile.family,
            stage="hibernation",
            branch=None,
            palette=form_palette(profile, "hibernation", None, asset_version),
        )
    )
    return forms


def build_evolution_manifest(
    evolution_root: Path,
    milestone: str,
    profiles: list[Profile],
    forms: list[FormSpec],
    asset_version: str,
) -> dict[str, Any]:
    form_index = {
        form.id: {
            "lineId": form.line_id,
            "stage": form.stage,
            "branch": form.branch,
            "assetRoot": f"../pets/{form.id}",
            "poseManifest": f"../pets/{form.id}/pose_manifest.json",
            "runtimeMotion": f"../pets/{form.id}/runtime_motion.json",
            "palette": form.palette,
            "status": "shipped",
        }
        for form in forms
    }
    paths: dict[str, Any] = {}
    for profile in profiles:
        line_forms = {form.id: form for form in forms if form.line_id == profile.id}
        paths[profile.id] = {
            "displayName": profile.display_name,
            "inspiration": profile.inspiration,
            "egg": f"{profile.id}_egg",
            "hatchling": f"{profile.id}_hatchling",
            "child": profile.id,
            "teen": {branch: f"{profile.id}_teen_{branch}" for branch in TEEN_BRANCHES},
            "adult": {branch: f"{profile.id}_adult_{branch}" for branch in ADULT_BRANCHES},
            "hibernation": f"{profile.id}_hibernation",
            "formCount": len(line_forms),
        }
    stage_forms: dict[str, list[str]] = {}
    for form in forms:
        stage_forms.setdefault(form.stage, []).append(form.id)
    manifest = {
        "id": "pawn-evolution-v1",
        "version": milestone,
        "contract": "tamacodex-pawn-evolution-v1",
        "generator": {
            "name": "tamacodex_gen",
            "assetVersion": asset_version,
            "profileSources": [profile.source_path for profile in profiles],
        },
        "coordinateSystem": {
            "pawnSource": list(PAWN_SOURCE),
            "requiredPoseSet": POSE_NAMES,
            "palettePolicy": M21_PALETTE_POLICY if asset_version == "m2.1" else M2_PALETTE_POLICY,
            "lcdRuleset": LCD_RULESET_ID if asset_version == "m2.1" else None,
            "artDirection": ART_DIRECTION_ID if asset_version == "m2.1" else None,
        },
        "assetContract": {
            "renderableFormRoot": "assets/pawn/pets/<form-id>/",
            "requiredFiles": [
                "poses.txt",
                "pose_manifest.json",
                "runtime_motion.json",
                "poses_atlas.png",
                "poses_atlas_x4.png",
                "poses/*.png",
            ],
            "formIdPolicy": "Form ids are immutable once shipped; later balance changes branch rules, not old art roots.",
            "slotPolicy": f"{milestone} ships every declared stage and branch slot for every line.",
        },
        "lifeStages": [
            {
                "id": "egg",
                "displayName": "Egg",
                "assetOwner": "pawn",
                "status": "shipped",
                "forms": sorted(stage_forms.get("egg", [])),
                "notes": "Pre-hatch pawn art now exists; machine shells still provide the outer device.",
            },
            {
                "id": "hatchling",
                "displayName": "Hatchling",
                "assetOwner": "pawn",
                "status": "shipped",
                "forms": sorted(stage_forms.get("hatchling", [])),
                "branchRole": "line-specific root before personality branches are visible",
            },
            {
                "id": "child",
                "displayName": "Child",
                "assetOwner": "pawn",
                "status": "shipped",
                "forms": sorted(stage_forms.get("child", [])),
                "branchRole": "first user-facing named form",
            },
            {
                "id": "teen",
                "displayName": "Teen",
                "assetOwner": "pawn",
                "status": "shipped",
                "branchSlots": TEEN_BRANCHES,
                "forms": sorted(stage_forms.get("teen", [])),
            },
            {
                "id": "adult",
                "displayName": "Adult",
                "assetOwner": "pawn",
                "status": "shipped",
                "branchSlots": ADULT_BRANCHES,
                "forms": sorted(stage_forms.get("adult", [])),
            },
            {
                "id": "hibernation",
                "displayName": "Hibernation",
                "assetOwner": "pawn",
                "status": "shipped",
                "forms": sorted(stage_forms.get("hibernation", [])),
                "branchRole": "gentle long-idle fallback instead of death art",
            },
        ],
        "branchAxes": [
            {
                "id": "care_quality",
                "signals": ["careMistakes", "ignoredUrgentCalls", "healthTrend"],
                "artImpact": "stable care nudges calm/focused outcomes; mistakes do not punish the user.",
            },
            {
                "id": "work_rhythm",
                "signals": ["completedRuns", "tokenFeed", "longTaskCompletions"],
                "artImpact": "visible work props and energetic work poses in worker/focused branches.",
            },
            {
                "id": "recovery_style",
                "signals": ["failedThenResolved", "testPassAfterFail", "cleanupEvents"],
                "artImpact": "resilient branches get sturdier silhouettes and clearer recovery poses.",
            },
            {
                "id": "boundary_style",
                "signals": ["dismissedLowPriorityCalls", "quietness"],
                "artImpact": "quiet/sleepy branches use smaller waves and more contained rest poses.",
            },
        ],
        "formIndex": form_index,
        "evolutionPaths": paths,
        "branchRules": [
            {
                "fromStage": "egg",
                "toStage": "hatchling",
                "gate": "first successful Codex session or explicit hatch event",
                "selection": "keep the pawn's chosen line id",
            },
            {
                "fromStage": "hatchling",
                "toStage": "child",
                "gate": "age and minimum interaction history",
                "selection": "activate the named child form for the same line",
            },
            {
                "fromStage": "child",
                "toStage": "teen",
                "gate": "age plus enough care signal confidence",
                "selection": "focused, resilient, or restless from dominant recent signal",
            },
            {
                "fromStage": "teen",
                "toStage": "adult",
                "gate": "age plus stable branch history",
                "selection": "calm, resilient, worker, quiet, or sleepy by weighted history",
            },
            {
                "fromStage": "any",
                "toStage": "hibernation",
                "gate": "long idle gap with no active urgent care",
                "selection": "line-specific hibernation form",
            },
        ],
        "researchNotes": [
            f"{milestone} completes the M1.3 lifecycle slots with shipped art for every line.",
            "Outcomes are framed as personality and work-style differences, not punishment.",
            "M6 keeps M2 identity colors for shells and pawns while preserving the shared LCD screen plane.",
            "M6.1 cleans Toast and Mais into 1px mascot silhouettes with hands, feet, and sparse intentional detail pixels.",
            "Generation follows an agent-authored profile plus deterministic renderer workflow.",
        ],
        "researchSources": [
            "https://tamagotchi-official.com/us/series/original/",
            "https://tamagotchi.fandom.com/wiki/Growth",
            "https://www.remotion.dev/docs/composition",
            "https://www.remotion.dev/docs/parameterized-rendering",
        ],
    }
    write_json(evolution_root / "evolution_manifest.json", manifest)
    return manifest


def build_preview_matrix(root: Path, forms: list[FormSpec]) -> dict[str, str]:
    tamago_root = root / "assets" / "tamago" / "machines"
    pets_root = root / "assets" / "pawn" / "pets"
    preview = root / "assets" / "preview"
    rows = list(MACHINES.keys())
    cols = [form.id for form in forms]
    tile_w, tile_h = CELL
    label_h = 24
    canvas = Image.new("RGBA", (tile_w * len(cols), (tile_h + label_h) * len(rows)), (8, 16, 38, 255))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    outputs: dict[str, str] = {}
    for row_index, machine_id in enumerate(rows):
        shell = Image.open(tamago_root / machine_id / "shell.png").convert("RGBA")
        mask = Image.open(tamago_root / machine_id / "screen_mask.png").convert("L")
        for col_index, form_id in enumerate(cols):
            pawn = Image.open(pets_root / form_id / "poses" / "idle_0.png").convert("RGBA")
            frame = compose(shell, mask, pawn)
            x = col_index * tile_w
            y = row_index * (tile_h + label_h)
            canvas.alpha_composite(frame, (x, y))
            draw.text((x + 8, y + tile_h + 6), f"{machine_id} + {form_id}", fill=(255, 253, 245, 230), font=font)
            if form_id in {"toast", "mais", "toast_egg", "mais_egg", "toast_adult_worker", "mais_adult_worker"}:
                path = preview / f"{machine_id}_{form_id}.png"
                frame.save(path)
                outputs[f"{machine_id}_{form_id}"] = str(path.relative_to(root))
    matrix = preview / "catalog_matrix.png"
    canvas.save(matrix)
    (root / "qa" / "catalog_matrix.png").write_bytes(matrix.read_bytes())
    outputs["catalog_matrix"] = str(matrix.relative_to(root))
    return outputs


def build_contact_sheets(root: Path, forms: list[FormSpec]) -> None:
    pets_root = root / "assets" / "pawn" / "pets"
    tamago_root = root / "assets" / "tamago" / "machines"
    qa = root / "qa"
    font = ImageFont.load_default()
    machine_sheet = Image.new("RGBA", (CELL[0] * len(MACHINES), CELL[1] + 24), (8, 16, 38, 255))
    draw = ImageDraw.Draw(machine_sheet)
    for index, machine_id in enumerate(MACHINES):
        image = Image.open(tamago_root / machine_id / "shell.png").convert("RGBA")
        machine_sheet.alpha_composite(image, (index * CELL[0], 0))
        draw.text((index * CELL[0] + 8, CELL[1] + 6), machine_id, fill=(255, 253, 245, 230), font=font)
    machine_sheet.save(qa / "machine_contact_sheet.png")

    sample_poses = ["idle_0", "blink", "work_1", "review_1", "failed_1", "jump_2"]
    label_w = 172
    col_w = 112
    row_h = 124
    pet_sheet = Image.new("RGBA", (label_w + len(sample_poses) * col_w, len(forms) * row_h), (8, 16, 38, 255))
    draw = ImageDraw.Draw(pet_sheet)
    for row, form in enumerate(forms):
        row_y = row * row_h
        draw.text((8, row_y + 8), form.id, fill=(255, 253, 245, 235), font=font)
        stage_label = form.stage if form.branch is None else f"{form.stage}:{form.branch}"
        draw.text((8, row_y + 24), stage_label, fill=(255, 253, 245, 170), font=font)
        for col, pose in enumerate(sample_poses):
            image = Image.open(pets_root / form.id / "poses" / f"{pose}.png").convert("RGBA")
            scaled = image.resize((PAWN_SOURCE[0] * 4, PAWN_SOURCE[1] * 4), Image.Resampling.NEAREST)
            x = label_w + col * col_w + 4
            y = row_y + 4
            pet_sheet.alpha_composite(scaled, (x, y))
            draw.text((label_w + col * col_w + 4, row_y + 106), pose, fill=(255, 253, 245, 210), font=font)
    pet_sheet.save(qa / "pet_contact_sheet.png")


def write_preview_html(root: Path, forms: list[FormSpec], milestone: str) -> None:
    preview = root / "assets" / "preview"
    machines = json.dumps(list(MACHINES.keys()))
    pets = json.dumps([{"id": form.id, "label": form.display_name, "stage": form.stage} for form in forms])
    screen_left = SCREEN["x"] / CELL[0] * 100
    screen_top = SCREEN["y"] / CELL[1] * 100
    screen_width = SCREEN["width"] / CELL[0] * 100
    screen_height = SCREEN["height"] / CELL[1] * 100
    screen_radius = SCREEN["radius"] / SCREEN["width"] * 100
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tamacodex {milestone} Catalog</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: #fffdf5;
      background: #081026;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ width: min(1180px, calc(100vw - 32px)); margin: 32px auto; display: grid; gap: 20px; }}
    h1 {{ margin: 0; font-size: 34px; line-height: 1.05; letter-spacing: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 14px; }}
    .combo {{ display: grid; justify-items: center; gap: 7px; padding: 12px; border: 1px solid rgba(255,253,245,0.14); border-radius: 8px; background: rgba(255,253,245,0.06); }}
    .stage {{ position: relative; width: min(148px, 38vw); aspect-ratio: 192 / 208; filter: drop-shadow(0 18px 38px rgba(0,0,0,0.32)); }}
    .shell {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    .screen {{ position: absolute; left: {screen_left:.4f}%; top: {screen_top:.4f}%; width: {screen_width:.4f}%; height: {screen_height:.4f}%; overflow: hidden; border-radius: {screen_radius:.4f}%; }}
    .pawn {{ position: absolute; width: 62.069%; height: 78%; left: 18.9655%; top: 11%; image-rendering: pixelated; object-fit: contain; }}
    .label {{ width: 100%; color: rgba(255,253,245,0.76); font-size: 12px; text-align: center; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <main>
    <h1>Tamacodex {milestone}</h1>
    <section class="grid" id="grid"></section>
  </main>
  <script>
    const machines = {machines};
    const pets = {pets};
    const grid = document.getElementById("grid");
    for (const pet of pets) {{
      for (const machine of machines) {{
        const item = document.createElement("article");
        item.className = "combo";
        item.innerHTML = `
          <div class="stage">
            <img class="shell" src="../tamago/machines/${{machine}}/shell.svg" alt="">
            <div class="screen">
              <img class="pawn" src="../pawn/pets/${{pet.id}}/poses/idle_0.png" alt="">
            </div>
          </div>
          <div class="label">${{machine}} + ${{pet.id}}</div>
        `;
        grid.appendChild(item);
      }}
    }}
  </script>
</body>
</html>
"""
    (preview / "index.html").write_text(html, encoding="utf-8")


def prepare_output(root: Path) -> None:
    for name in ("assets", "qa"):
        path = root / name
        if path.exists():
            shutil.rmtree(path)
    for path in (
        root / "assets",
        root / "qa",
        root / "assets" / "tamago" / "machines",
        root / "assets" / "pawn" / "pets",
        root / "assets" / "pawn" / "evolution",
        root / "assets" / "preview",
    ):
        path.mkdir(parents=True, exist_ok=True)


def render(milestone: str, output_dir: Path, profiles: list[Profile], asset_version: str = "m2") -> dict[str, Any]:
    root = output_dir.resolve()
    prepare_output(root)
    assets = root / "assets"
    qa = root / "qa"
    tamago = assets / "tamago" / "machines"
    pets = assets / "pawn" / "pets"
    evolution = assets / "pawn" / "evolution"

    forms = [form for profile in profiles for form in forms_for_profile(profile, asset_version)]
    base_mask = goose_mask()
    mask_hash = hashlib.sha256(base_mask.tobytes()).hexdigest()[:16]
    base_mask.resize(CELL, Image.Resampling.LANCZOS).save(qa / "shared_goose_egg_mask.png")

    machine_report = {
        machine_id: build_machine(tamago, machine_id, machine, base_mask, mask_hash)
        for machine_id, machine in MACHINES.items()
    }
    pet_report = {form.id: build_pet(pets, form) for form in forms}
    evolution_manifest = build_evolution_manifest(evolution, milestone, profiles, forms, asset_version)
    preview_outputs = build_preview_matrix(root, forms)
    build_contact_sheets(root, forms)
    write_preview_html(root, forms, milestone)

    pet_manifest = {
        form.id: {
            "manifest": f"pawn/pets/{form.id}/pose_manifest.json",
            "runtimeMotion": f"pawn/pets/{form.id}/runtime_motion.json",
            "posesDir": f"pawn/pets/{form.id}/poses",
            "stage": form.stage,
            "branch": form.branch,
            "lineId": form.line_id,
        }
        for form in forms
    }
    manifest = {
        "id": f"tamacodex-{milestone.lower()}",
        "version": milestone,
        "contract": (
            "layered-tamacodex-v1.catalog-v2.evolution-v1.generator-v4.palette-m21.lcd-m6.art-m61"
            if asset_version == "m2.1"
            else "layered-tamacodex-v1.catalog-v2.evolution-v1.generator-v1"
        ),
        "generator": {
            "name": "tamacodex_gen",
            "assetVersion": asset_version,
            "profileAuthoring": "codex-agent-json-contract",
            "lcdRuleset": LCD_RULESET_ID if asset_version == "m2.1" else None,
            "artDirection": ART_DIRECTION_ID if asset_version == "m2.1" else None,
            "profileSources": [profile.source_path for profile in profiles],
            "renderScript": "tamacodex_gen/scripts/render_catalog.py",
        },
        "cell": list(CELL),
        "screen": SCREEN,
        "lcdRuleset": LCD_RULESET_ID if asset_version == "m2.1" else None,
        "artDirection": ART_DIRECTION_ID if asset_version == "m2.1" else None,
        "machines": {
            machine_id: {
                "manifest": f"tamago/machines/{machine_id}/machine_manifest.json",
                "shellSvg": f"tamago/machines/{machine_id}/shell.svg",
                "shellPng": f"tamago/machines/{machine_id}/shell.png",
                "shellWebp": f"tamago/machines/{machine_id}/shell.webp",
            }
            for machine_id in MACHINES
        },
        "pets": pet_manifest,
        "preview": {
            "html": "preview/index.html",
            "catalogMatrix": "preview/catalog_matrix.png",
        },
        "evolution": {
            "manifest": "pawn/evolution/evolution_manifest.json",
            "contract": evolution_manifest["contract"],
            "stageCount": len(evolution_manifest["lifeStages"]),
            "lineCount": len(profiles),
            "shippedForms": sorted(pet_manifest),
        },
        "compatibilityExports": {
            "codexPetFlattened": None,
            "note": "Layered catalog remains source of truth; flattened Codex pet export is a later target.",
        },
    }
    write_json(assets / "manifest.json", manifest)

    silhouette_hashes = sorted({report["outerSilhouetteHash"] for report in machine_report.values()})
    viewport_values = {json.dumps(report["screen"], sort_keys=True) for report in machine_report.values()}
    bad_pets = {
        pet_id: report["bad_poses"]
        for pet_id, report in pet_report.items()
        if report["bad_poses"]
    }
    missing_roots = [
        form_id
        for form_id, info in evolution_manifest["formIndex"].items()
        if not (evolution / info["assetRoot"]).resolve().exists()
    ]
    report = {
        "ok": len(silhouette_hashes) == 1 and len(viewport_values) == 1 and not bad_pets and not missing_roots,
        "milestone": milestone,
        "assetVersion": asset_version,
        "lcdRuleset": LCD_RULESET_ID if asset_version == "m2.1" else None,
        "artDirection": ART_DIRECTION_ID if asset_version == "m2.1" else None,
        "palettePolicy": M21_PALETTE_POLICY if asset_version == "m2.1" else M2_PALETTE_POLICY,
        "machineCount": len(MACHINES),
        "lineCount": len(profiles),
        "petCount": len(forms),
        "combinationCount": len(MACHINES) * len(forms),
        "evolutionStageCount": len(evolution_manifest["lifeStages"]),
        "shippedEvolutionFormCount": len(evolution_manifest["formIndex"]),
        "sharedOuterSilhouetteHashes": silhouette_hashes,
        "sharedScreenViewport": len(viewport_values) == 1,
        "badPets": bad_pets,
        "missingEvolutionRoots": missing_roots,
        "machines": machine_report,
        "pets": {
            pet_id: {
                "pose_count": data["pose_count"],
                "atlas_size": data["atlas_size"],
                "bad_poses": data["bad_poses"],
            }
            for pet_id, data in pet_report.items()
        },
        "preview": preview_outputs,
        "files": {
            "manifest": str((assets / "manifest.json").relative_to(root)),
            "previewHtml": str((assets / "preview" / "index.html").relative_to(root)),
            "catalogMatrix": str((assets / "preview" / "catalog_matrix.png").relative_to(root)),
            "machineContactSheet": str((qa / "machine_contact_sheet.png").relative_to(root)),
            "petContactSheet": str((qa / "pet_contact_sheet.png").relative_to(root)),
            "evolutionManifest": str((evolution / "evolution_manifest.json").relative_to(root)),
        },
    }
    write_json(qa / "build_report.json", report)
    return report


def infer_asset_version(milestone: str, asset_version: str | None) -> str:
    if asset_version:
        return asset_version
    return "m2.1" if milestone.lower().startswith(("m2.1", "m21")) else "m2"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestone", default="M2")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--asset-version", choices=["m2", "m2.1"], default=None)
    args = parser.parse_args()

    profiles = load_profiles(args.profile)
    asset_version = infer_asset_version(args.milestone, args.asset_version)
    report = render(args.milestone, Path(args.output_dir), profiles, asset_version)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "assetVersion": report["assetVersion"],
                "files": report["files"],
                "petCount": report["petCount"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
