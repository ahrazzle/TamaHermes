from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .catalog import Catalog, read_json
from .visual_state import derive_visual_state, visual_state_hash

COLUMNS = 8
ROWS = 9
CELL_WIDTH = 192
CELL_HEIGHT = 208
ATLAS_WIDTH = COLUMNS * CELL_WIDTH
ATLAS_HEIGHT = ROWS * CELL_HEIGHT

CODEX_ROWS = [
    ("idle", "idle", 0, 6),
    ("running-right", "drag_right", 1, 8),
    ("running-left", "drag_left", 2, 8),
    ("waving", "greeting", 3, 4),
    ("jumping", "success", 4, 5),
    ("failed", "failed", 5, 8),
    ("waiting", "waiting", 6, 6),
    ("running", "work", 7, 6),
    ("review", "review", 8, 6),
]

VISUAL_OVERLAY_VERSION = "m9.2-status-strip-xp-bar-v1"
SPRITESHEET_BASENAME = "spritesheet.webp"

# The growth bar sits on the shell's face *below* the LCD viewport (aurora's screen
# ends at y=150 and the oval keeps ~56px of empty face beneath it), so it is painted
# outside the screen mask and clipped to the oval silhouette instead. The geometry is
# a fixed rect on purpose: the 192x208 atlas cell is a hard contract with Hermes
# (FRAME_W/FRAME_H), so the art cannot grow to make room -- the bar has to live in
# space the shell already leaves empty.
XP_BAR = {"x": 35, "y": 156, "width": 122, "height": 11}
XP_BAR_RECT = (
    XP_BAR["x"],
    XP_BAR["y"],
    XP_BAR["x"] + XP_BAR["width"] - 1,
    XP_BAR["y"] + XP_BAR["height"] - 1,
)
# Growth is drawn in 1/XP_BAR_STEPS buckets (see visual_state.percent_bucket), so the
# drawn bar matches the quantised value the rebuild decision was made on.
XP_BAR_STEPS = 20

_STAGE_BAR_FILL = {
    "egg": (204, 142, 49, 245),
    "hatchling": (57, 132, 85, 245),
    "child": (58, 102, 161, 245),
    "teen": (206, 72, 88, 245),
    "adult": (196, 154, 66, 245),
    "hibernation": (83, 87, 71, 200),
}


class PetCompileError(RuntimeError):
    pass


def _hash_bytes(hasher: "hashlib._Hash", label: str, payload: bytes) -> None:
    hasher.update(label.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(len(payload)).encode("ascii"))
    hasher.update(b"\0")
    hasher.update(payload)
    hasher.update(b"\0")


def _hash_json(hasher: "hashlib._Hash", label: str, payload: dict[str, Any]) -> None:
    _hash_bytes(hasher, label, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _hash_file(hasher: "hashlib._Hash", label: str, path: Path) -> None:
    try:
        _hash_bytes(hasher, label, path.read_bytes())
    except FileNotFoundError as exc:
        raise PetCompileError(f"missing source file for hash: {path}") from exc


def package_source_hash(
    catalog: Catalog,
    state: dict[str, Any],
    form_id: str | None = None,
    machine_id: str | None = None,
    pet_id: str = "tamahermes",
) -> str:
    form = form_id or state["formId"]
    machine = machine_id or state["machineId"]
    form_info = catalog.form_info(form)
    machine_info = catalog.machine_info(machine)
    motion = read_json(catalog.runtime_motion_path(form))
    viewport_path = catalog.screen_viewport_path(machine)
    machine_manifest_path = catalog.root / machine_info["manifest"]
    hasher = hashlib.sha256()
    visual_state = derive_visual_state(state)
    _hash_json(
        hasher,
        "selection",
        {
            "petId": pet_id,
            "displayName": state.get("displayName") or "TamaHermes",
            "catalogRoot": str(catalog.root),
            "formId": form,
            "lineId": form_info["lineId"],
            "lifeStage": form_info["stage"],
            "branch": form_info.get("branch"),
            "machineId": machine,
            "visualOverlayVersion": VISUAL_OVERLAY_VERSION,
        },
    )
    _hash_json(hasher, "visual-state", visual_state)
    for label, path in [
        ("catalog-manifest", catalog.root / "manifest.json"),
        ("evolution-manifest", catalog.root / "pawn" / "evolution" / "evolution_manifest.json"),
        ("form-manifest", catalog.pose_manifest_path(form)),
        ("runtime-motion", catalog.runtime_motion_path(form)),
        ("machine-manifest", machine_manifest_path),
        ("screen-viewport", viewport_path),
        ("screen-mask", catalog.screen_mask_path(machine)),
        ("machine-shell", catalog.shell_path(machine)),
    ]:
        _hash_file(hasher, label, path)

    pose_names = sorted(
        {
            frame["pose"]
            for frames in motion.get("states", {}).values()
            for frame in frames
            if isinstance(frame, dict) and frame.get("pose")
        }
    )
    for pose_name in pose_names:
        _hash_file(hasher, f"pose:{pose_name}", catalog.pose_root(form) / f"{pose_name}.png")
    return hasher.hexdigest()


def nontransparent_pixels(image: Image.Image) -> int:
    alpha = image.getchannel("A")
    return sum(alpha.histogram()[1:])


def validate_atlas(path: Path, min_used_pixels: int = 50) -> dict[str, Any]:
    with Image.open(path) as opened:
        source_format = opened.format
        image = opened.convert("RGBA")
    errors: list[str] = []
    cells: list[dict[str, Any]] = []
    if image.size != (ATLAS_WIDTH, ATLAS_HEIGHT):
        errors.append(f"expected {ATLAS_WIDTH}x{ATLAS_HEIGHT}, got {image.width}x{image.height}")
    if source_format not in {"PNG", "WEBP"}:
        errors.append(f"expected PNG or WebP, got {source_format}")

    frame_counts = {row: frame_count for row, _motion, _index, frame_count in CODEX_ROWS}
    for row_name, _motion, row_index, _frame_count in CODEX_ROWS:
        for column in range(COLUMNS):
            left = column * CELL_WIDTH
            top = row_index * CELL_HEIGHT
            cell = image.crop((left, top, left + CELL_WIDTH, top + CELL_HEIGHT))
            count = nontransparent_pixels(cell)
            used = column < frame_counts[row_name]
            cells.append({"state": row_name, "row": row_index, "column": column, "used": used, "nontransparentPixels": count})
            if used and count < min_used_pixels:
                errors.append(f"{row_name} column {column} is empty or too sparse")
            if not used and count:
                errors.append(f"{row_name} unused column {column} is not transparent")

    return {
        "ok": not errors,
        "file": str(path),
        "format": source_format,
        "width": image.width,
        "height": image.height,
        "errors": errors,
        "cells": cells,
    }


def _pixels_differ(left: tuple[int, int, int, int], right: tuple[int, int, int, int], tolerance: int) -> bool:
    if left[3] == 0 and right[3] == 0:
        return False
    return any(abs(left[index] - right[index]) > tolerance for index in range(4))


def _in_rects(x: int, y: int, rects: tuple[tuple[int, int, int, int], ...]) -> bool:
    return any(x0 <= x <= x2 and y0 <= y <= y2 for x0, y0, x2, y2 in rects)


def validate_screen_mask_clipping(
    atlas_path: Path,
    shell_path: Path,
    screen_mask_path: Path,
    tolerance: int = 0,
    skip_rects: tuple[tuple[int, int, int, int], ...] = (),
) -> dict[str, Any]:
    """Assert compiled pawn pixels never alter shell pixels outside the LCD mask.

    *skip_rects* are regions the compiler deliberately paints outside the mask (the
    growth bar on the shell face below the screen). Each must sit entirely inside the
    shell's opaque silhouette, so a drifting rect is reported rather than quietly
    floating over transparency.
    """

    with Image.open(atlas_path) as opened:
        atlas = opened.convert("RGBA")
    shell = Image.open(shell_path).convert("RGBA")
    screen_mask = Image.open(screen_mask_path).convert("L")
    errors: list[str] = []
    cells: list[dict[str, Any]] = []
    if atlas.size != (ATLAS_WIDTH, ATLAS_HEIGHT):
        errors.append(f"expected atlas {ATLAS_WIDTH}x{ATLAS_HEIGHT}, got {atlas.width}x{atlas.height}")
    if shell.size != (CELL_WIDTH, CELL_HEIGHT):
        errors.append(f"expected shell {CELL_WIDTH}x{CELL_HEIGHT}, got {shell.width}x{shell.height}")
    if screen_mask.size != (CELL_WIDTH, CELL_HEIGHT):
        errors.append(f"expected screen mask {CELL_WIDTH}x{CELL_HEIGHT}, got {screen_mask.width}x{screen_mask.height}")
    if errors:
        return {
            "ok": False,
            "file": str(atlas_path),
            "shell": str(shell_path),
            "screenMask": str(screen_mask_path),
            "tolerance": tolerance,
            "skipRects": [list(rect) for rect in skip_rects],
            "errors": errors,
            "cells": cells,
        }

    shell_pixels = shell.load()
    mask_pixels = screen_mask.load()
    for rect in skip_rects:
        transparent = sum(
            1
            for y in range(rect[1], rect[3] + 1)
            for x in range(rect[0], rect[2] + 1)
            if 0 <= x < CELL_WIDTH and 0 <= y < CELL_HEIGHT and shell_pixels[x, y][3] <= 8
        )
        if transparent:
            errors.append(f"overlay rect {rect} covers {transparent} pixels outside the shell silhouette")
    for row_name, _motion, row_index, frame_count in CODEX_ROWS:
        for column in range(frame_count):
            cell = atlas.crop(
                (
                    column * CELL_WIDTH,
                    row_index * CELL_HEIGHT,
                    (column + 1) * CELL_WIDTH,
                    (row_index + 1) * CELL_HEIGHT,
                )
            )
            cell_pixels = cell.load()
            violation_pixels = 0
            first_violation: list[int] | None = None
            for y in range(CELL_HEIGHT):
                for x in range(CELL_WIDTH):
                    if mask_pixels[x, y] != 0:
                        continue
                    if _in_rects(x, y, skip_rects):
                        continue
                    if _pixels_differ(cell_pixels[x, y], shell_pixels[x, y], tolerance):
                        violation_pixels += 1
                        if first_violation is None:
                            first_violation = [x, y]
            cell_result = {
                "state": row_name,
                "row": row_index,
                "column": column,
                "violationPixels": violation_pixels,
                "firstViolation": first_violation,
            }
            cells.append(cell_result)
            if violation_pixels:
                errors.append(f"{row_name} column {column} changes {violation_pixels} pixels outside the LCD mask")

    return {
        "ok": not errors,
        "file": str(atlas_path),
        "shell": str(shell_path),
        "screenMask": str(screen_mask_path),
        "tolerance": tolerance,
        "skipRects": [list(rect) for rect in skip_rects],
        "errors": errors,
        "cells": cells,
    }


def write_contact_sheet(atlas_path: Path, output_path: Path) -> Path:
    scale = 0.5
    thumb_width = int(CELL_WIDTH * scale)
    thumb_height = int(CELL_HEIGHT * scale)
    label_width = 118
    top_margin = 24
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(atlas_path) as opened:
        atlas = opened.convert("RGBA")
    sheet = Image.new("RGBA", (label_width + COLUMNS * thumb_width, top_margin + ROWS * thumb_height), (17, 20, 25, 255))
    draw = ImageDraw.Draw(sheet)
    for column in range(COLUMNS):
        x = label_width + column * thumb_width + thumb_width // 2
        draw.text((x - 4, 6), str(column), fill=(186, 197, 211, 255))
    for row_name, _motion, row_index, _frame_count in CODEX_ROWS:
        y = top_margin + row_index * thumb_height
        draw.text((8, y + 8), row_name, fill=(244, 247, 251, 255))
        for column in range(COLUMNS):
            cell = atlas.crop((column * CELL_WIDTH, row_index * CELL_HEIGHT, (column + 1) * CELL_WIDTH, (row_index + 1) * CELL_HEIGHT))
            thumb = cell.resize((thumb_width, thumb_height), Image.Resampling.NEAREST)
            x = label_width + column * thumb_width
            sheet.alpha_composite(thumb, (x, y))
            draw.rectangle((x, y, x + thumb_width - 1, y + thumb_height - 1), outline=(74, 86, 102, 255))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    return output_path


def compose_cell(
    shell: Image.Image,
    pose: Image.Image,
    screen: dict[str, int],
    scale: int,
    offset: list[int],
    screen_mask: Image.Image | None = None,
) -> Image.Image:
    cell = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    cell.alpha_composite(shell)
    pawn = pose.resize((pose.width * scale, pose.height * scale), Image.Resampling.NEAREST)
    x = int(screen["x"]) + (int(screen["width"]) - pawn.width) // 2 + int(offset[0]) * scale
    y = int(screen["y"]) + (int(screen["height"]) - pawn.height) // 2 + int(offset[1]) * scale
    layer = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    layer.alpha_composite(pawn, (x, y))
    if screen_mask is not None:
        alpha = Image.composite(layer.getchannel("A"), Image.new("L", (CELL_WIDTH, CELL_HEIGHT), 0), screen_mask)
        layer.putalpha(alpha)
    cell.alpha_composite(layer)
    return cell


def _rect(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, fill: tuple[int, int, int, int]) -> None:
    draw.rectangle((x, y, x + width - 1, y + height - 1), fill=fill)


def _draw_battery(draw: ImageDraw.ImageDraw, x: int, y: int, energy: str) -> None:
    ink = (38, 54, 44, 235)
    fill_by_energy = {
        "critical": ((186, 49, 52, 245), 3),
        "low": ((204, 142, 49, 245), 7),
        "full": ((57, 132, 85, 245), 13),
    }
    if energy not in fill_by_energy:
        return
    fill, width = fill_by_energy[energy]
    draw.rectangle((x, y, x + 15, y + 7), outline=ink)
    _rect(draw, x + 16, y + 2, 2, 4, ink)
    _rect(draw, x + 2, y + 2, width, 4, fill)
    if energy == "critical":
        _rect(draw, x + 7, y + 1, 2, 6, (235, 226, 167, 230))


def _draw_health_warning(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    warn = (186, 49, 52, 245)
    ink = (38, 54, 44, 235)
    draw.polygon([(x + 6, y), (x + 13, y + 12), (x, y + 12)], fill=warn)
    draw.line([(x + 6, y + 3), (x + 6, y + 8)], fill=ink, width=2)
    _rect(draw, x + 5, y + 10, 3, 2, ink)


def _draw_mess(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, mess: str) -> None:
    if mess == "clean":
        return
    dust = (83, 87, 71, 150)
    smudge = (51, 65, 52, 105)
    specks = [
        (x + width - 25, y + 26),
        (x + width - 18, y + 47),
        (x + 17, y + height - 34),
        (x + 28, y + 31),
    ]
    if mess == "messy":
        specks.extend(
            [
                (x + 44, y + height - 27),
                (x + width - 37, y + height - 29),
                (x + 56, y + 24),
            ]
        )
        draw.rectangle((x + width - 34, y + height - 41, x + width - 18, y + height - 36), fill=smudge)
        draw.rectangle((x + 18, y + 39, x + 31, y + 42), fill=smudge)
    for sx, sy in specks:
        _rect(draw, sx, sy, 2, 2, dust)


def _draw_food(draw: ImageDraw.ImageDraw, x: int, y: int, satiety: str) -> None:
    ink = (38, 54, 44, 235)
    fill_by_satiety = {
        "hungry": None,
        "ok": (204, 142, 49, 230),
        "fed": (57, 132, 85, 235),
    }
    draw.line([(x + 1, y + 4), (x + 14, y + 4)], fill=ink, width=2)
    draw.line([(x + 4, y + 8), (x + 11, y + 8)], fill=ink, width=2)
    draw.line([(x + 1, y + 4), (x + 4, y + 8)], fill=ink, width=2)
    draw.line([(x + 14, y + 4), (x + 11, y + 8)], fill=ink, width=2)
    fill = fill_by_satiety.get(satiety)
    if fill:
        fill_width = 8 if satiety == "ok" else 12
        _rect(draw, x + 3, y + 5, fill_width, 2, fill)
    if satiety == "hungry":
        _rect(draw, x + 6, y + 1, 2, 2, ink)


def _draw_heart(draw: ImageDraw.ImageDraw, x: int, y: int, bond: str) -> None:
    if bond == "new":
        return
    fill = (206, 72, 88, 235) if bond == "warm" else (230, 87, 105, 245)
    for px, py, width in [
        (2, 0, 2),
        (6, 0, 2),
        (0, 2, 10),
        (1, 4, 8),
        (3, 6, 4),
        (4, 8, 2),
    ]:
        _rect(draw, x + px, y + py, width, 2, fill)
    if bond == "attached":
        spark = (235, 226, 167, 235)
        _rect(draw, x - 4, y + 1, 2, 2, spark)
        _rect(draw, x + 13, y + 5, 2, 2, spark)


def _draw_alert(draw: ImageDraw.ImageDraw, x: int, y: int, alert: str) -> None:
    if alert == "none":
        return
    ink = (38, 54, 44, 245)
    fill_by_alert = {
        "failure": (186, 49, 52, 245),
        "recovery": (57, 132, 85, 245),
        "review": (58, 102, 161, 245),
    }
    fill = fill_by_alert[alert]
    draw.rectangle((x, y, x + 13, y + 13), fill=fill)
    draw.rectangle((x, y, x + 13, y + 13), outline=ink)
    if alert == "failure":
        _rect(draw, x + 6, y + 3, 2, 6, ink)
        _rect(draw, x + 6, y + 10, 2, 2, ink)
    elif alert == "recovery":
        _rect(draw, x + 6, y + 3, 2, 8, ink)
        _rect(draw, x + 3, y + 6, 8, 2, ink)
    elif alert == "review":
        draw.line([(x + 3, y + 7), (x + 6, y + 4), (x + 10, y + 7), (x + 6, y + 10), (x + 3, y + 7)], fill=ink, width=1)
        _rect(draw, x + 6, y + 7, 2, 2, ink)


def _status_level(kind: str, value: str) -> tuple[int, tuple[int, int, int, int]]:
    red = (186, 49, 52, 245)
    amber = (204, 142, 49, 245)
    green = (57, 132, 85, 245)
    blue = (58, 102, 161, 245)
    pink = (206, 72, 88, 245)
    gray = (83, 87, 71, 210)
    levels = {
        "energy": {
            "critical": (1, red),
            "low": (2, amber),
            "ok": (3, green),
            "full": (4, blue),
        },
        "satiety": {
            "hungry": (1, red),
            "ok": (3, amber),
            "fed": (4, green),
        },
        "health": {
            "weak": (1, red),
            "ok": (4, green),
        },
        "bond": {
            "new": (1, gray),
            "warm": (3, pink),
            "attached": (4, pink),
        },
        "mess": {
            "clean": (4, green),
            "dusty": (2, amber),
            "messy": (1, red),
        },
    }
    return levels[kind][value]


def _draw_status_segment(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    kind: str,
    value: str,
) -> None:
    ink = (38, 54, 44, 245)
    panel = (196, 199, 140, 185)
    level, fill = _status_level(kind, value)
    draw.rectangle((x, y, x + 15, y + 8), fill=panel)
    draw.rectangle((x, y, x + 15, y + 8), outline=ink)
    for index in range(4):
        bar_x = x + 2 + index * 3
        color = fill if index < level else (74, 86, 80, 115)
        _rect(draw, bar_x, y + 5 - index, 2, 2 + index, color)


def _draw_status_strip(draw: ImageDraw.ImageDraw, x: int, y: int, visual_state: dict[str, str]) -> None:
    ink = (38, 54, 44, 245)
    draw.rectangle((x, y, x + 106, y + 12), fill=(226, 214, 153, 135))
    draw.rectangle((x, y, x + 106, y + 12), outline=ink)
    for index, key in enumerate(["energy", "satiety", "health", "bond", "mess"]):
        _draw_status_segment(draw, x + 3 + index * 20, y + 2, key, visual_state[key])


def _percent_value(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _draw_xp_bar(draw: ImageDraw.ImageDraw, visual_state: dict[str, str]) -> None:
    """Evolution progress, drawn in the dead space below the LCD screen.

    The status strip answers "how is the pet doing" in coarse bins; this answers
    "how close is the next stage", which is the number a Tamagotchi owner actually
    watches. The fill colour tracks the current stage so the bar changes character as
    the pet grows, and it fills completely on the terminal (adult) stage.
    """
    x, y, width, height = XP_BAR["x"], XP_BAR["y"], XP_BAR["width"], XP_BAR["height"]
    ink = (38, 54, 44, 245)
    track = (26, 30, 40, 205)
    tick = (74, 86, 80, 120)
    stage = str(visual_state.get("stage") or "egg")
    fill = _STAGE_BAR_FILL.get(stage, _STAGE_BAR_FILL["egg"])
    percent = _percent_value(visual_state.get("xpPercent"))

    # Rounded like the LCD it sits under, and inset from the face edge, so it reads as
    # something milled into the casing rather than a flat graphic laid on top of it.
    radius = 3
    frame = (x, y, x + width - 1, y + height - 1)
    draw.rounded_rectangle(frame, radius=radius, fill=track)
    inner_x, inner_y = x + 2, y + 2
    inner_width, inner_height = width - 4, height - 4
    filled = inner_width if percent >= 100 else int(round(inner_width * percent / 100))
    if filled > 0:
        draw.rounded_rectangle(
            (inner_x, inner_y, inner_x + filled - 1, inner_y + inner_height - 1), radius=2, fill=fill
        )
    # Recessed top edge: a hairline shadow sells the depth.
    draw.line((inner_x, inner_y - 1, inner_x + inner_width - 1, inner_y - 1), fill=(18, 20, 26, 150))
    # Quarter ticks on the unfilled remainder so the bar reads as a gauge rather than
    # as a short bar that happens to stop early.
    for quarter in (1, 2, 3):
        tick_x = inner_x + (inner_width * quarter) // 4
        if tick_x < inner_x + filled:
            continue
        _rect(draw, tick_x, inner_y, 1, inner_height, tick)
    draw.rounded_rectangle(frame, radius=radius, outline=ink)


def apply_visual_overlay(
    cell: Image.Image,
    screen: dict[str, int],
    screen_mask: Image.Image,
    visual_state: dict[str, str],
    shell: Image.Image | None = None,
) -> Image.Image:
    overlay = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    screen_x = int(screen["x"])
    screen_y = int(screen["y"])
    screen_width = int(screen["width"])
    screen_height = int(screen["height"])

    _draw_status_strip(draw, screen_x + 8, screen_y + 5, visual_state)
    if visual_state["health"] == "weak":
        _draw_health_warning(draw, screen_x + screen_width - 22, screen_y + 22)
    _draw_mess(draw, screen_x, screen_y, screen_width, screen_height, visual_state["mess"])
    _draw_food(draw, screen_x + 9, screen_y + screen_height - 18, visual_state["satiety"])
    _draw_heart(draw, screen_x + screen_width - 24, screen_y + screen_height - 19, visual_state["bond"])
    _draw_alert(draw, screen_x + (screen_width // 2) - 7, screen_y + 21, visual_state["alert"])

    clipped_alpha = Image.composite(overlay.getchannel("A"), Image.new("L", (CELL_WIDTH, CELL_HEIGHT), 0), screen_mask)
    overlay.putalpha(clipped_alpha)
    cell.alpha_composite(overlay)

    # The growth bar is outside the LCD, so it is clipped to the shell silhouette
    # (the machine's own alpha) instead of the screen mask. Callers with no shell
    # keep the LCD-only behaviour, so nothing else changes shape.
    if shell is not None:
        bar_layer = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
        _draw_xp_bar(ImageDraw.Draw(bar_layer), visual_state)
        silhouette = Image.composite(
            bar_layer.getchannel("A"), Image.new("L", (CELL_WIDTH, CELL_HEIGHT), 0), shell.getchannel("A")
        )
        bar_layer.putalpha(silhouette)
        cell.alpha_composite(bar_layer)
    return cell


def build_codex_pet(
    catalog: Catalog,
    state: dict[str, Any],
    output_dir: Path,
    form_id: str | None = None,
    machine_id: str | None = None,
    pet_id: str = "tamahermes",
) -> dict[str, Any]:
    form = form_id or state["formId"]
    machine = machine_id or state["machineId"]
    source_hash = package_source_hash(catalog, state, form_id=form, machine_id=machine, pet_id=pet_id)
    form_info = catalog.form_info(form)
    machine_info = catalog.machine_info(machine)
    visual_state = derive_visual_state(state)
    visual_hash = visual_state_hash(visual_state)
    motion = read_json(catalog.runtime_motion_path(form))
    viewport = read_json(catalog.screen_viewport_path(machine))
    screen = viewport["screen"]
    scale = int(motion.get("defaultPawnScale") or viewport.get("defaultPawnScale") or 3)
    shell = Image.open(catalog.shell_path(machine)).convert("RGBA")
    screen_mask = Image.open(catalog.screen_mask_path(machine)).convert("L")
    if shell.size != (CELL_WIDTH, CELL_HEIGHT):
        raise PetCompileError(f"machine shell must be {CELL_WIDTH}x{CELL_HEIGHT}, got {shell.size}")
    if screen_mask.size != (CELL_WIDTH, CELL_HEIGHT):
        raise PetCompileError(f"screen mask must be {CELL_WIDTH}x{CELL_HEIGHT}, got {screen_mask.size}")

    output_dir.mkdir(parents=True, exist_ok=True)
    atlas = Image.new("RGBA", (ATLAS_WIDTH, ATLAS_HEIGHT), (0, 0, 0, 0))
    frames_written: list[dict[str, Any]] = []
    for codex_state, motion_state, row_index, frame_count in CODEX_ROWS:
        source_frames = motion["states"].get(motion_state)
        if not source_frames:
            raise PetCompileError(f"{form} runtime motion missing {motion_state!r}")
        for column in range(frame_count):
            frame = source_frames[column % len(source_frames)]
            pose_path = catalog.pose_root(form) / f"{frame['pose']}.png"
            if not pose_path.exists():
                raise PetCompileError(f"missing pose image: {pose_path}")
            pose = Image.open(pose_path).convert("RGBA")
            cell = compose_cell(shell, pose, screen, scale, frame.get("offset", [0, 0]), screen_mask)
            cell = apply_visual_overlay(cell, screen, screen_mask, visual_state, shell)
            atlas.alpha_composite(cell, (column * CELL_WIDTH, row_index * CELL_HEIGHT))
            frames_written.append(
                {
                    "codexState": codex_state,
                    "motionState": motion_state,
                    "row": row_index,
                    "column": column,
                    "pose": frame["pose"],
                    "durationMs": frame.get("durationMs"),
                }
            )

    png_path = output_dir / "spritesheet.png"
    webp_path = output_dir / SPRITESHEET_BASENAME
    manifest_path = output_dir / "pet.json"
    report_path = output_dir / "build_report.json"
    qa_dir = output_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    atlas.save(png_path)
    atlas.save(webp_path, format="WEBP", lossless=True, quality=100, method=6)

    display_name = state.get("displayName") or "TamaHermes"
    pet_manifest = {
        "id": pet_id,
        "displayName": display_name,
        "description": f"{display_name} form {form_info.get('stage')}:{form_info.get('branch') or 'root'} from the bundled catalog.",
        "spritesheetPath": SPRITESHEET_BASENAME,
    }
    manifest_path.write_text(json.dumps(pet_manifest, indent=2) + "\n", encoding="utf-8")
    validation_png = validate_atlas(png_path)
    validation_webp = validate_atlas(webp_path)
    validation_screen_mask = validate_screen_mask_clipping(
        png_path, catalog.shell_path(machine), catalog.screen_mask_path(machine), skip_rects=(XP_BAR_RECT,)
    )
    validation_png_path = qa_dir / "validation-png.json"
    validation_webp_path = qa_dir / "validation-webp.json"
    validation_screen_mask_path = qa_dir / "validation-screen-mask.json"
    contact_sheet_path = write_contact_sheet(png_path, qa_dir / "contact-sheet.png")
    validation_png_path.write_text(json.dumps(validation_png, indent=2) + "\n", encoding="utf-8")
    validation_webp_path.write_text(json.dumps(validation_webp, indent=2) + "\n", encoding="utf-8")
    validation_screen_mask_path.write_text(json.dumps(validation_screen_mask, indent=2) + "\n", encoding="utf-8")
    validation_errors = validation_png["errors"] + validation_webp["errors"] + validation_screen_mask["errors"]
    report = {
        "ok": not validation_errors,
        "petId": pet_id,
        "formId": form,
        "lineId": form_info["lineId"],
        "lifeStage": form_info["stage"],
        "branch": form_info.get("branch"),
        "machineId": machine,
        "machineDisplayName": machine_info.get("displayName", machine),
        "catalogDir": str(catalog.root),
        "visualOverlayVersion": VISUAL_OVERLAY_VERSION,
        "visualState": visual_state,
        "visualStateHash": visual_hash,
        "xpBar": {
            "rect": list(XP_BAR_RECT),
            "steps": XP_BAR_STEPS,
            "stage": visual_state.get("stage"),
            "percent": visual_state.get("xpPercent"),
        },
        "sourceHash": source_hash,
        "installHash": source_hash,
        "atlas": {
            "png": str(png_path),
            "webp": str(webp_path),
            "width": ATLAS_WIDTH,
            "height": ATLAS_HEIGHT,
            "cell": [CELL_WIDTH, CELL_HEIGHT],
            "grid": [COLUMNS, ROWS],
        },
        "framesWritten": frames_written,
        "validation": {
            "ok": not validation_errors,
            "errors": validation_errors,
            "png": {key: value for key, value in validation_png.items() if key != "cells"},
            "webp": {key: value for key, value in validation_webp.items() if key != "cells"},
            "screenMaskClipping": {key: value for key, value in validation_screen_mask.items() if key != "cells"},
        },
        "qa": {
            "dir": str(qa_dir),
            "validationPng": str(validation_png_path),
            "validationWebp": str(validation_webp_path),
            "validationScreenMask": str(validation_screen_mask_path),
            "contactSheet": str(contact_sheet_path),
        },
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if validation_errors:
        raise PetCompileError("; ".join(validation_errors))
    return report


def _is_versioned_sheet(name: str) -> bool:
    """True for ``spritesheet-<12 hex>.webp`` — a sheet we generated and may own.

    Deliberately narrow: a hand-placed ``spritesheet-custom.webp`` is never pruned.
    """
    prefix, suffix = "spritesheet-", ".webp"
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return False
    stem = name[len(prefix) : -len(suffix)]
    return len(stem) == 12 and all(char in "0123456789abcdef" for char in stem)


def install_petdex_pet(
    catalog: Catalog,
    state: dict[str, Any],
    petdex_home: Path,
    build_dir: Path,
    force: bool = False,
    source_sheet: Path | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Install the pet into a Petdex desktop home (``~/.petdex``).

    The Petdex desktop app renders the same artifact Hermes does — an 8x9 atlas
    of 192x208 cells plus ``pet.json`` — but it expects the conventional
    unversioned ``spritesheet.webp`` beside the manifest, so this lays the pet
    out that way instead of using the content-addressed name a Hermes home gets.

    ``source_sheet`` short-circuits the render: when the pet is already built
    for Hermes, mirroring it to the desktop is a file copy, not a rebuild.
    """
    pet_id = state.get("petId", "tamahermes")
    pet_dir = petdex_home / "pets" / pet_id
    target_sheet = pet_dir / SPRITESHEET_BASENAME
    target_manifest = pet_dir / "pet.json"
    if pet_dir.exists() and not force and (target_sheet.exists() or target_manifest.exists()):
        raise PetCompileError(
            f"{pet_dir} already contains a Petdex pet. This protects the existing package; pass --force to replace it."
        )
    if source_sheet is not None and Path(source_sheet).is_file():
        sheet_source = Path(source_sheet)
    else:
        build_codex_pet(catalog, state, build_dir)
        sheet_source = build_dir / SPRITESHEET_BASENAME

    pet_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sheet_source, target_sheet)
    display_name = state.get("displayName") or "TamaHermes"
    manifest: dict[str, Any] = {
        "id": pet_id,
        "displayName": display_name,
        "description": (
            f"{display_name} — {state.get('lifeStage', 'egg')} stage, grown from "
            "Hermes agent activity. A Tamagotchi-style desktop pet for Hermes Agent."
        ),
        "spritesheetPath": SPRITESHEET_BASENAME,
    }
    if kind:
        # `kind` is optional in Petdex (most shipped pets omit it), so it is
        # only written when the caller explicitly asks for one.
        manifest["kind"] = kind
    target_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "petId": pet_id,
        "petDir": str(pet_dir),
        "manifest": str(target_manifest),
        "spritesheet": str(target_sheet),
        "spritesheetPath": SPRITESHEET_BASENAME,
        "sourceSheet": str(sheet_source),
    }


def set_petdex_active_pet(petdex_home: Path, pet_id: str) -> dict[str, Any]:
    """Point the Petdex desktop app at ``pet_id``.

    Rewrites only the ``active_pet`` key of ``desktop-native-settings.json``,
    preserving every other setting and its order. Petdex must not be running:
    a live app writes that file from memory on quit and would clobber this.
    """
    settings_path = petdex_home / "desktop-native-settings.json"
    settings: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings = loaded
        except (OSError, ValueError):
            settings = {}
    previous = settings.get("active_pet")
    settings["active_pet"] = pet_id
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    # No trailing newline and no spaces: matches how the app writes the file.
    settings_path.write_text(json.dumps(settings, separators=(",", ":")), encoding="utf-8")
    return {
        "ok": True,
        "settings": str(settings_path),
        "previousPet": previous,
        "activePet": pet_id,
    }


def install_codex_pet(catalog: Catalog, state: dict[str, Any], codex_home: Path, build_dir: Path, force: bool = False) -> dict[str, Any]:
    pet_dir = codex_home / "pets" / state.get("petId", "tamahermes")
    target_sheet = pet_dir / SPRITESHEET_BASENAME
    target_manifest = pet_dir / "pet.json"
    if pet_dir.exists() and not force and (target_sheet.exists() or target_manifest.exists()):
        raise PetCompileError(
            f"{pet_dir} already contains Codex pet files. This protects the existing package; pass --force to replace it."
        )
    report = build_codex_pet(catalog, state, build_dir)
    pet_dir.mkdir(parents=True, exist_ok=True)
    versioned_sheet_name = f"spritesheet-{report['installHash'][:12]}.webp"
    versioned_sheet = pet_dir / versioned_sheet_name
    shutil.copy2(build_dir / SPRITESHEET_BASENAME, target_sheet)
    shutil.copy2(build_dir / SPRITESHEET_BASENAME, versioned_sheet)

    manifest = json.loads((build_dir / "pet.json").read_text(encoding="utf-8"))
    manifest["spritesheetPath"] = versioned_sheet_name
    target_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Every rebuild mints a new content-addressed sheet; without pruning, a pet
    # that regrows often leaves one webp per rebuild in the pets dir forever.
    pruned_sheets: list[str] = []
    for stale in sorted(pet_dir.glob("spritesheet-*.webp")):
        if stale.name == versioned_sheet_name or not _is_versioned_sheet(stale.name):
            continue
        try:
            stale.unlink()
            pruned_sheets.append(stale.name)
        except OSError:
            # A locked/unreadable stale sheet is not worth failing an install.
            pass

    install_report = {
        "ok": True,
        "petDir": str(pet_dir),
        "manifest": str(target_manifest),
        "spritesheet": str(versioned_sheet),
        "legacySpritesheet": str(target_sheet),
        "spritesheetPath": versioned_sheet_name,
        "prunedSheets": pruned_sheets,
        "formId": report["formId"],
        "machineId": report["machineId"],
        "catalogDir": report["catalogDir"],
        "sourceHash": report["sourceHash"],
        "installHash": report["installHash"],
        "visualState": report["visualState"],
        "visualStateHash": report["visualStateHash"],
        "buildReport": str(build_dir / "build_report.json"),
        "qa": report["qa"],
    }
    (build_dir / "install_report.json").write_text(json.dumps(install_report, indent=2) + "\n", encoding="utf-8")
    return install_report
