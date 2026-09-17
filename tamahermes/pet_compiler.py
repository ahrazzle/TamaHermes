from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import levels
from .catalog import Catalog, read_json
from .state import evolution_gates, normalize_ledger
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
# Two ways to render a pet into the 192x208 cell:
#   "floating" -- the creature alone, with its HUD floating around it (the default;
#                 no device shell, so no Tamagotchi-shaped bubble and no LCD panel)
#   "shell"    -- the creature inside a tamago machine shell (aurora/pulse), with the
#                 HUD drawn on the LCD. Retained so the framed look stays available.
LAYOUTS = ("floating", "shell")
DEFAULT_LAYOUT = "floating"

# Shell layout: the bar sits in the shell's dead space below the LCD (aurora's screen
# ends at y=150). Floating layout: it sits under the creature instead.
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

# Floating layout geometry. Without a shell there is no frame to anchor the HUD, so
# it is arranged as tidy rows -- one above the creature, one below -- rather than
# scattered into the cell's corners:
#     y  8..20   [alert] [ energy chip ] [health]
#     y 33..176  the creature
#     y181..192  [food] [ growth bar ] [heart]
#     y192..207  [ level number, free-floating ]
# Each row is centred as a *group*, with the side icons sitting a 4px gap away from the
# bar they decorate -- parking them in the cell corners reads as scattered, and there is
# no shell left to anchor them.
FLOATING_XP_BAR = {"x": 38, "y": 181, "width": 122, "height": 11}
# Option C: the top row stays silent unless something needs the owner, so exactly two things
# may appear in it. The energy chip, drawn while energy is low or critical because that band
# is the one that ends in hibernation. And the alert glyph, drawn only for the two events
# that need a human -- a task failure (red "!") or an opened review (blue diamond); good news
# such as a recovery escalates nothing and paints nothing. The chip keeps the exact footprint
# the retired status strip used, so the alert and health glyphs flanking it do not move.
FLOATING_ENERGY_CHIP = {"x": 43, "y": 8, "width": 106, "height": 12}
# The level readout is persistent: it is drawn under the growth bar at every level, so the
# owner always sees which rung of the ladder the pet stands on. Three digits: the ladder's
# bound is 999, and the real levels pets reach are three digits (the live combined ledger sits
# around level 120), so a two-digit readout would print 99 for a level-123 pet.
# It is a free-floating number, not a badge: no bubble or container, just enlarged pixel-art
# digits (glyph scale 3) in a fixed bright ink with a near-black halo, so the number stays
# legible when the pet is downsized instead of shrinking inside a box. The stage-colored
# caret beside the digits is decoration only. The rect below is the maximum painted extent
# (three digits + caret + halo) used by the collision guard and the build report.
FLOATING_LEVEL_RECT = {"x": 72, "y": 192, "width": 48, "height": 16}
FLOATING_ALERT_XY = (26, 8)
FLOATING_HEALTH_XY = (154, 8)
FLOATING_FOOD_XY = (18, 182)      # group of 16+4+122+4+9 = 155, centred
FLOATING_HEART_XY = (164, 182)
FLOATING_PET_BAND = (29, 181)          # inclusive top, exclusive bottom
FLOATING_PET_MAX_WIDTH = 144
FLOATING_MESS_BOX = (16, 28, 160, 152)  # x, y, width, height

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
    layout: str | None = None,
) -> str:
    layout_name = resolve_layout(layout)
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
            "layout": layout_name,
            "visualOverlayVersion": VISUAL_OVERLAY_VERSION,
        },
    )
    _hash_json(hasher, "visual-state", visual_state)
    hashed_files = [
        ("catalog-manifest", catalog.root / "manifest.json"),
        ("evolution-manifest", catalog.root / "pawn" / "evolution" / "evolution_manifest.json"),
        ("form-manifest", catalog.pose_manifest_path(form)),
        ("runtime-motion", catalog.runtime_motion_path(form)),
    ]
    if layout_name == "shell":
        hashed_files += [
            ("machine-manifest", machine_manifest_path),
            ("screen-viewport", viewport_path),
            ("screen-mask", catalog.screen_mask_path(machine)),
            ("machine-shell", catalog.shell_path(machine)),
        ]
    for label, path in hashed_files:
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


def resolve_layout(layout: str | None) -> str:
    """Normalise a layout name, defaulting to the floating (shell-less) look."""
    name = (layout or DEFAULT_LAYOUT).strip().lower()
    if name not in LAYOUTS:
        raise PetCompileError(f"unknown layout {layout!r}; expected one of {', '.join(LAYOUTS)}")
    return name


def xp_bar_rect(layout: str | None = None) -> tuple[int, int, int, int]:
    """The growth bar rect for *layout* (the drawn copies differ per layout)."""
    bar = FLOATING_XP_BAR if resolve_layout(layout) == "floating" else XP_BAR
    return (bar["x"], bar["y"], bar["x"] + bar["width"] - 1, bar["y"] + bar["height"] - 1)


def level_badge_rect(layout: str | None = None) -> tuple[int, int, int, int] | None:
    """The persistent level readout rect, or ``None`` for a layout that has no room for it.

    Only the floating HUD carries the readout: the shell layout's HUD is pinned inside the
    device viewport and has no free row, so the shelled look renders exactly as it always did.
    """
    if resolve_layout(layout) != "floating":
        return None
    box = FLOATING_LEVEL_RECT
    return (box["x"], box["y"], box["x"] + box["width"] - 1, box["y"] + box["height"] - 1)


def level_badge_report(layout: str | None, visual_state: dict[str, str]) -> dict[str, Any] | None:
    """The level readout block for a build report, or ``None`` when the layout has none."""
    rect = level_badge_rect(layout)
    if rect is None:
        return None
    return {"rect": list(rect), "level": visual_state.get("level")}


def union_content_bbox(catalog: Catalog, form: str) -> tuple[int, int, int, int]:
    """Union opaque bbox across every pose *form* can play.

    Cropping every frame by the same box (rather than each frame's own bbox) is what
    keeps per-frame offsets meaningful -- a per-frame crop would make the creature
    jitter as limbs move.
    """
    motion = read_json(catalog.runtime_motion_path(form))
    names = sorted({f["pose"] for frames in motion.get("states", {}).values() for f in frames if f.get("pose")})
    union: list[int] | None = None
    for name in names:
        path = catalog.pose_root(form) / f"{name}.png"
        if not path.exists():
            raise PetCompileError(f"missing pose image: {path}")
        bbox = Image.open(path).convert("RGBA").getchannel("A").getbbox()
        if not bbox:
            continue
        union = list(bbox) if union is None else [
            min(union[0], bbox[0]), min(union[1], bbox[1]), max(union[2], bbox[2]), max(union[3], bbox[3])
        ]
    if union is None:
        raise PetCompileError(f"form {form} has no opaque pose pixels")
    return (union[0], union[1], union[2], union[3])


def floating_pet_placement(union: tuple[int, int, int, int]) -> tuple[int, tuple[int, int]]:
    """Largest integer scale that fits the creature in its band, and where to put it.

    Integer-only scaling keeps pixel art crisp; anything else resamples the art.
    """
    width, height = union[2] - union[0], union[3] - union[1]
    top, bottom = FLOATING_PET_BAND
    scale = max(1, min((bottom - top) // max(1, height), FLOATING_PET_MAX_WIDTH // max(1, width)))
    scaled_w, scaled_h = width * scale, height * scale
    return scale, ((CELL_WIDTH - scaled_w) // 2, top + ((bottom - top) - scaled_h) // 2)


def floating_pet_rect(union: tuple[int, int, int, int], scale: int, origin: tuple[int, int]) -> tuple[int, int, int, int]:
    """Where the scaled creature lands in the cell."""
    return (
        origin[0],
        origin[1],
        origin[0] + (union[2] - union[0]) * scale - 1,
        origin[1] + (union[3] - union[1]) * scale - 1,
    )


def validate_layout_geometry(layout: str, union: tuple[int, int, int, int]) -> dict[str, Any]:
    """Cheap structural guard for the floating layout: HUD and creature must not collide."""
    errors: list[str] = []
    scale, (px, py) = floating_pet_placement(union)
    pet = floating_pet_rect(union, scale, (px, py))
    rects = {
        "energy": (
            FLOATING_ENERGY_CHIP["x"],
            FLOATING_ENERGY_CHIP["y"],
            FLOATING_ENERGY_CHIP["x"] + FLOATING_ENERGY_CHIP["width"],
            FLOATING_ENERGY_CHIP["y"] + FLOATING_ENERGY_CHIP["height"],
        ),
        "xpBar": xp_bar_rect("floating"),
        "alert": (FLOATING_ALERT_XY[0], FLOATING_ALERT_XY[1], FLOATING_ALERT_XY[0] + 13, FLOATING_ALERT_XY[1] + 11),
        "food": (FLOATING_FOOD_XY[0], FLOATING_FOOD_XY[1], FLOATING_FOOD_XY[0] + 15, FLOATING_FOOD_XY[1] + 8),
        "heart": (FLOATING_HEART_XY[0], FLOATING_HEART_XY[1], FLOATING_HEART_XY[0] + 9, FLOATING_HEART_XY[1] + 8),
        "health": (FLOATING_HEALTH_XY[0], FLOATING_HEALTH_XY[1], FLOATING_HEALTH_XY[0] + 13, FLOATING_HEALTH_XY[1] + 11),
        "level": (
            FLOATING_LEVEL_RECT["x"],
            FLOATING_LEVEL_RECT["y"],
            FLOATING_LEVEL_RECT["x"] + FLOATING_LEVEL_RECT["width"] - 1,
            FLOATING_LEVEL_RECT["y"] + FLOATING_LEVEL_RECT["height"] - 1,
        ),
    }
    for name, rect in rects.items():
        if not (0 <= rect[0] < rect[2] < CELL_WIDTH and 0 <= rect[1] < rect[3] < CELL_HEIGHT):
            errors.append(f"{name} rect {rect} leaves the {CELL_WIDTH}x{CELL_HEIGHT} cell")
    for name, rect in rects.items():
        if not (rect[2] < pet[0] or rect[0] > pet[2] or rect[3] < pet[1] or rect[1] > pet[3]):
            errors.append(f"{name} rect {rect} overlaps the creature {pet}")
    names = list(rects)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            a, b = rects[left], rects[right]
            if not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]):
                errors.append(f"{left} rect {a} overlaps {right} rect {b}")
    return {
        "ok": not errors,
        "layout": layout,
        "skipped": "floating layout has no LCD shell, so there is no screen mask to clip to",
        "creature": {"scale": scale, "rect": list(pet)},
        "rects": {name: list(rect) for name, rect in rects.items()},
        "errors": errors,
    }


def compose_float_cell(
    pose: Image.Image,
    offset: list[int] | tuple[int, int],
    union: tuple[int, int, int, int],
    scale: int,
    origin: tuple[int, int],
) -> Image.Image:
    """The creature alone in the cell -- no shell, no LCD, transparent everywhere else."""
    cell = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    cropped = pose.crop(union)
    pawn = cropped.resize((cropped.width * scale, cropped.height * scale), Image.Resampling.NEAREST)
    cell.alpha_composite(pawn, (origin[0] + int(offset[0]) * scale, origin[1] + int(offset[1]) * scale))
    return cell


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


def _draw_mess(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    mess: str,
    avoid: tuple[int, int, int, int] | None = None,
) -> None:
    """Grime specks. *avoid* is the creature's rect: with no LCD to grub up, the mess
    has to sit in the space around the pet, and drawing it over the pet looks broken."""
    if mess == "clean":
        return
    dust = (83, 87, 71, 150)
    smudge = (51, 65, 52, 105)

    def blocked(rect: tuple[int, int, int, int]) -> bool:
        if not avoid:
            return False
        return not (rect[2] < avoid[0] or rect[0] > avoid[2] or rect[3] < avoid[1] or rect[1] > avoid[3])
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
        for rect in ((x + width - 34, y + height - 41, x + width - 18, y + height - 36), (x + 18, y + 39, x + 31, y + 42)):
            if not blocked(rect):
                draw.rectangle(rect, fill=smudge)
    for sx, sy in specks:
        if not blocked((sx, sy, sx + 1, sy + 1)):
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
        "review": (58, 102, 161, 245),
    }
    fill = fill_by_alert[alert]
    draw.rectangle((x, y, x + 13, y + 13), fill=fill)
    draw.rectangle((x, y, x + 13, y + 13), outline=ink)
    if alert == "failure":
        _rect(draw, x + 6, y + 3, 2, 6, ink)
        _rect(draw, x + 6, y + 10, 2, 2, ink)
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


def _draw_bolt(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    """A 10x11 lightning bolt: the energy gauge's label, in place of text."""
    points = [
        (x + 5, y),
        (x, y + 6),
        (x + 4, y + 6),
        (x + 2, y + 11),
        (x + 9, y + 4),
        (x + 5, y + 4),
    ]
    draw.polygon(points, fill=color, outline=(38, 54, 44, 245))


def _draw_energy_chip(
    draw: ImageDraw.ImageDraw,
    visual_state: dict[str, str],
    rect: dict[str, int] | None = None,
) -> None:
    """The top row's one escalation, and the whole of it.

    Energy is the only tracker that interrupts, because it is the only band that ends in
    hibernation: the pet sleeps at 4 and does not wake until 35. Satiety, bond and health
    already escalate on their own (the bowl, the heart, the warning glyph) and mess is
    legible as grime on the creature, so none of them is repeated up here.
    """
    band = str(visual_state.get("energy") or "ok")
    if band not in ("low", "critical"):
        return
    chip = rect or FLOATING_ENERGY_CHIP
    x, y = chip["x"], chip["y"]
    width, height = chip["width"], chip["height"]
    ink = (38, 54, 44, 245)
    track = (26, 30, 40, 205)
    tick = (74, 86, 80, 120)
    fill = (186, 49, 52, 245) if band == "critical" else (204, 142, 49, 245)
    percent = _percent_value(visual_state.get("energyPercent"))

    _draw_bolt(draw, x, y, fill)

    # The gauge mirrors the growth bar's frame, so the two read as one instrument.
    bar_x = x + 14
    bar_width = width - 14
    frame = (bar_x, y + 1, bar_x + bar_width - 1, y + height - 2)
    draw.rounded_rectangle(frame, radius=3, fill=track)
    inner_x, inner_width = bar_x + 2, bar_width - 4
    inner_y, inner_height = y + 3, height - 6
    filled = inner_width if percent >= 100 else int(round(inner_width * percent / 100))
    if filled > 0:
        draw.rounded_rectangle(
            (inner_x, inner_y, inner_x + filled - 1, inner_y + inner_height - 1), radius=2, fill=fill
        )
    draw.line((inner_x, inner_y - 1, inner_x + inner_width - 1, inner_y - 1), fill=(18, 20, 26, 150))
    for quarter in (1, 2, 3):
        tick_x = inner_x + (inner_width * quarter) // 4
        if tick_x < inner_x + filled:
            continue
        _rect(draw, tick_x, inner_y, 1, inner_height, tick)
    draw.rounded_rectangle(frame, radius=3, outline=ink)


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


def _draw_xp_bar(draw: ImageDraw.ImageDraw, visual_state: dict[str, str], rect: dict[str, int] | None = None) -> None:
    """Evolution progress, drawn in the dead space below the LCD screen.

    The status strip answers "how is the pet doing" in coarse bins; this answers
    "how close is the next level", which is the number a Tamagotchi owner actually
    watches. The fill colour tracks the current stage so the bar changes character as
    the pet grows, and it fills completely only at the top of the ladder.
    """
    bar = rect or XP_BAR
    x, y, width, height = bar["x"], bar["y"], bar["width"], bar["height"]
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


# A level readout needs real numerals and the cell carries no font: these are 3x5 pixel-art
# digits drawn with the same ``_rect`` primitive as every other glyph in the strip, so the
# readout belongs to the same visual language instead of being a bitmap pasted on top.
# They are drawn free-floating at 3x (9x15 sprite px per digit) with no container: a fixed
# bright ink plus a 1px near-black halo carries the contrast on any desktop, so the number
# survives pet downsizing that used to mush the old bubble's outline first. The only
# stage-tinted element is the small caret, which is decoration and never load-bearing.
_LEVEL_GLYPH_SCALE = 3
_LEVEL_DIGIT_GAP = 3
_LEVEL_CARET_W, _LEVEL_CARET_H = 9, 9
_LEVEL_CARET_GAP = 4
_LEVEL_INK = (232, 240, 232, 255)
_LEVEL_HALO = (12, 14, 16, 235)
_LEVEL_DESKTOP_GREY = (128, 132, 140)
_LEVEL_GLYPHS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}
_LEVEL_GLYPH_W, _LEVEL_GLYPH_H = 3, 5
_LEVEL_MAX = 999


def level_text(value: Any) -> str:
    """The level as one, two or three digits, clamped to the ladder's top."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 1
    return str(max(1, min(_LEVEL_MAX, number)))


def _draw_level_number(
    draw: ImageDraw.ImageDraw, visual_state: dict[str, str], rect: dict[str, int] | None = None
) -> None:
    """The persistent level readout, free-floating under the growth bar.

    The number is the shared ledger's level (``visual_state['level']``, from ``state.level``),
    so it is the same rung the growth bar fills toward and the manifest describes. It is drawn
    whenever the pet is on screen -- level 1 as much as level 999 -- with no bubble or
    container around it: enlarged 3x digits in a fixed bright ink, each lit pixel backed by
    a 1px near-black halo, so the number carries its own contrast on any desktop instead of
    borrowing a container's. The stage-colored caret is decoration only.

    The content is centered in *rect* (which reserves the three-digit maximum): a one-digit
    level leaves transparent margins, which is what makes "no container" observable.
    A 1px halo fringe may kiss the growth bar's bottom edge directly above the rect; both
    are near-black, so it reads as one shadow, not an overlap.
    """
    box = rect or FLOATING_LEVEL_RECT
    x, y, width, height = box["x"], box["y"], box["width"], box["height"]
    stage = str(visual_state.get("stage") or "egg")
    caret_fill = _STAGE_BAR_FILL.get(stage, _STAGE_BAR_FILL["egg"])

    digits = level_text(visual_state.get("level"))
    scale = _LEVEL_GLYPH_SCALE
    digit_w, digit_h = _LEVEL_GLYPH_W * scale, _LEVEL_GLYPH_H * scale
    text_w = len(digits) * digit_w + (len(digits) - 1) * _LEVEL_DIGIT_GAP
    total_w = _LEVEL_CARET_W + _LEVEL_CARET_GAP + text_w
    left = x + (width - total_w) // 2
    top = y + (height - digit_h) // 2

    lit: list[tuple[int, int, int, int, tuple[int, int, int, int]]] = []
    # The caret keeps the pre-scale motif (a small downward triangle), enlarged 3x.
    for step in range(_LEVEL_CARET_W):
        lit.append((left + step, top + scale * 2 + step, _LEVEL_CARET_W - step, 1, caret_fill))
    cursor = left + _LEVEL_CARET_W + _LEVEL_CARET_GAP
    for index, char in enumerate(digits):
        glyph = _LEVEL_GLYPHS.get(char, _LEVEL_GLYPHS["0"])
        gx = cursor + index * (digit_w + _LEVEL_DIGIT_GAP)
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == "1":
                    lit.append((gx + column * scale, top + row * scale, scale, scale, _LEVEL_INK))
    # Halo first (a 1px dilation behind every lit pixel), then the inks on top.
    for px, py, w, h, _fill in lit:
        _rect(draw, px - 1, py - 1, w + 2, h + 2, _LEVEL_HALO)
    for px, py, w, h, fill in lit:
        _rect(draw, px, py, w, h, fill)


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


def apply_float_overlay(
    cell: Image.Image,
    visual_state: dict[str, str],
    creature_rect: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    """Draw the HUD around a shell-less creature.

    Nothing is clipped: with no LCD there is no screen mask, and the only reason the
    shelled layout clipped at all was to keep the pawn inside the device's viewport.
    """
    overlay = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _draw_energy_chip(draw, visual_state)
    if visual_state["health"] == "weak":
        _draw_health_warning(draw, FLOATING_HEALTH_XY[0], FLOATING_HEALTH_XY[1])
    _draw_mess(
        draw,
        FLOATING_MESS_BOX[0],
        FLOATING_MESS_BOX[1],
        FLOATING_MESS_BOX[2],
        FLOATING_MESS_BOX[3],
        visual_state["mess"],
        avoid=creature_rect,
    )
    _draw_food(draw, FLOATING_FOOD_XY[0], FLOATING_FOOD_XY[1], visual_state["satiety"])
    _draw_heart(draw, FLOATING_HEART_XY[0], FLOATING_HEART_XY[1], visual_state["bond"])
    _draw_alert(draw, FLOATING_ALERT_XY[0], FLOATING_ALERT_XY[1], visual_state["alert"])
    _draw_xp_bar(draw, visual_state, FLOATING_XP_BAR)
    _draw_level_number(draw, visual_state, FLOATING_LEVEL_RECT)
    cell.alpha_composite(overlay)
    return cell


# The creator-facing contract written into every ``pet.json`` this compiler emits: the pet's
# gates, plus the ladder they are read against. Hermes renders the sheet; a fork changes the
# gates, so the block travels with the manifest and install copies it into the ledger -- the
# same list the compiler reads back out, which is what makes the round trip hold.
CURVE = f"round({levels.QUADRATIC_TERM} * (L - 1) ** 2 + (L - 1) ** 6 / {levels.TAIL_DIVISOR})"


def evolution_block(state: dict[str, Any]) -> dict[str, Any]:
    """The ``evopet`` block for a pet manifest: this pet's gates on the fixed ladder."""
    return {
        "evolutionGates": evolution_gates(state),
        "maxLevel": levels.MAX_LEVEL,
        "topXp": levels.TOP_XP,
        "curve": CURVE,
    }


def read_manifest(path: Path) -> dict[str, Any] | None:
    """A pet manifest as a dict, or ``None`` when the file is missing or unreadable.

    An unreadable manifest is treated as one that declares nothing rather than failing the
    install: the package beside it is what is being replaced, and the ledger already carries
    the gates the pet is running on.
    """
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def declared_gates(manifest: dict[str, Any] | None, source: str = "manifest") -> list[int] | None:
    """The gates a manifest explicitly declares, or ``None`` when it declares none.

    Deliberately not ``levels.gates_from_manifest``, which substitutes the default pet's gates
    when the block is absent -- at install, silence must not overwrite a ledger that already
    carries a creator's list. A block that is present but invalid raises, naming the file: a
    broken declaration is the creator's to fix, not something to absorb quietly.
    """
    block = (manifest or {}).get(levels.MANIFEST_KEY) or {}
    raw = block.get("evolutionGates")
    if raw is None:
        return None
    try:
        return levels.validate_gates(raw)
    except ValueError as exc:
        raise PetCompileError(f"{source}: {exc}") from exc


def check_gates_against_catalog(catalog: Catalog, gates: list[int], line_id: str, source: str) -> None:
    """Refuse a gate list the pet's own line cannot render, naming the manifest and the gap.

    ``levels.validate_gates`` checks only the *shape* of a declaration (1..4 gates, strictly
    increasing, in range). A list that passes can still ask for more stages than the line ships —
    four gates name five forms — and ``catalog.find_form`` then raises the moment the pet reaches
    the missing one, which strands the tracker the runtime fallback exists to protect. Install is
    the last moment the creator can still fix that, so the declaration is refused here; mid-run
    the runtime holds the pet on its current form instead (``state.maybe_evolve``).
    """
    required = levels.forms_for_gates(gates)
    available = catalog.stages_for_line(line_id)
    missing = [stage for stage in required if stage not in available]
    if not missing:
        return
    shipped = ", ".join(catalog.form_ids(line_id)) or "none"
    raise PetCompileError(
        f"{source}: evolutionGates {list(gates)} need a form for every stage "
        f"({', '.join(required)}), but line {line_id!r} has no form for {', '.join(missing)}; "
        f"available forms: {shipped}"
    )


def sync_ledger_gates(
    state: dict[str, Any],
    manifest: dict[str, Any] | None,
    source: str = "manifest",
    catalog: Catalog | None = None,
) -> list[int]:
    """Copy a pet manifest's declared gates into the ledger; return the gates to evolve on.

    Install is where a creator's declaration reaches the running pet. The ledger keeps the list
    so nothing re-reads a manifest mid-run, which also gives the rule its edge: editing a
    manifest takes effect on the next install, not on the next event. A manifest that declares
    nothing leaves whatever the ledger already carries, and a ledger with no list at all (an
    older pet, or one written before install) is seeded from ``default_state``'s gates.

    The returned list is the normalised gates the runtime actually evolves on -- digit-strings
    from the manifest become ints, exactly as ``levels.validate_gates`` does it -- and reading
    never rewrites the ledger, so a caller can inspect the gates without mutating the row. A
    ledger list that is present but unusable falls back to the default pet's gates, the same
    way ``state.evolution_gates`` does mid-run.

    Pass *catalog* to also cross-check the declaration against the pet's own line: a gate list that
    is well-formed but names more stages than the line ships is refused here, at install, where the
    creator can still fix it (``check_gates_against_catalog``). Without a catalog the shape check
    still runs and the list is copied in, so direct callers see exactly what they saw before.
    """
    declared = declared_gates(manifest, source)
    if declared is not None:
        if catalog is not None:
            # A declaration the line cannot render is refused before anything is copied into the
            # ledger, so the running pet keeps the gates it already had -- and the creator finds
            # out at install, where the manifest is still theirs to fix.
            check_gates_against_catalog(catalog, declared, state.get("lineId") or "", source)
        state["evolutionGates"] = list(declared)
    elif state.get("evolutionGates") is None:
        state["evolutionGates"] = evolution_gates(state)
    return evolution_gates(state)


def build_codex_pet(
    catalog: Catalog,
    state: dict[str, Any],
    output_dir: Path,
    form_id: str | None = None,
    machine_id: str | None = None,
    pet_id: str = "tamahermes",
    layout: str | None = None,
) -> dict[str, Any]:
    layout_name = resolve_layout(layout)
    form = form_id or state["formId"]
    machine = machine_id or state["machineId"]
    source_hash = package_source_hash(
        catalog, state, form_id=form, machine_id=machine, pet_id=pet_id, layout=layout_name
    )
    form_info = catalog.form_info(form)
    machine_info = catalog.machine_info(machine)
    visual_state = derive_visual_state(state)
    visual_hash = visual_state_hash(visual_state)
    motion = read_json(catalog.runtime_motion_path(form))
    shell = screen_mask = screen = None
    union: tuple[int, int, int, int] | None = None
    origin: tuple[int, int] = (0, 0)
    creature_rect: tuple[int, int, int, int] | None = None
    if layout_name == "shell":
        viewport = read_json(catalog.screen_viewport_path(machine))
        screen = viewport["screen"]
        scale = int(motion.get("defaultPawnScale") or viewport.get("defaultPawnScale") or 3)
        shell = Image.open(catalog.shell_path(machine)).convert("RGBA")
        screen_mask = Image.open(catalog.screen_mask_path(machine)).convert("L")
        if shell.size != (CELL_WIDTH, CELL_HEIGHT):
            raise PetCompileError(f"machine shell must be {CELL_WIDTH}x{CELL_HEIGHT}, got {shell.size}")
        if screen_mask.size != (CELL_WIDTH, CELL_HEIGHT):
            raise PetCompileError(f"screen mask must be {CELL_WIDTH}x{CELL_HEIGHT}, got {screen_mask.size}")
    else:
        union = union_content_bbox(catalog, form)
        scale, origin = floating_pet_placement(union)
        creature_rect = floating_pet_rect(union, scale, origin)

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
            offset = frame.get("offset", [0, 0])
            if layout_name == "shell":
                cell = compose_cell(shell, pose, screen, scale, offset, screen_mask)
                cell = apply_visual_overlay(cell, screen, screen_mask, visual_state, shell)
            else:
                cell = compose_float_cell(pose, offset, union, scale, origin)
                cell = apply_float_overlay(cell, visual_state, creature_rect)
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
        # The pet's gates, on the fixed ladder -- the creator-facing contract a fork edits and
        # the one ``levels.gates_from_manifest`` reads back.
        "evopet": evolution_block(state),
    }
    manifest_path.write_text(json.dumps(pet_manifest, indent=2) + "\n", encoding="utf-8")
    validation_png = validate_atlas(png_path)
    validation_webp = validate_atlas(webp_path)
    if layout_name == "shell":
        validation_screen_mask = validate_screen_mask_clipping(
            png_path, catalog.shell_path(machine), catalog.screen_mask_path(machine), skip_rects=(XP_BAR_RECT,)
        )
    else:
        validation_screen_mask = validate_layout_geometry(layout_name, union)
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
        "layout": layout_name,
        "creature": {"scale": scale, "origin": list(origin), "contentBox": list(union) if union else None},
        "catalogDir": str(catalog.root),
        "visualOverlayVersion": VISUAL_OVERLAY_VERSION,
        "visualState": visual_state,
        "visualStateHash": visual_hash,
        "xpBar": {
            "rect": list(xp_bar_rect(layout_name)),
            "steps": XP_BAR_STEPS,
            "stage": visual_state.get("stage"),
            "percent": visual_state.get("xpPercent"),
        },
        # The persistent level readout, beside the bar. ``None`` for the shell layout, which
        # has no free row: that layout's report is byte-identical to what it always was.
        "levelBadge": level_badge_report(layout_name, visual_state),
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


# --- who may write the desktop mirror --------------------------------------
#
# The desktop mirror (``~/.petdex/pets/<slug>``) is *one* pet shown machine-wide, while the
# ledgers that grow it are per-profile. So a per-profile build cannot be the mirror's authority:
# whichever session happened to build last would decide the pet's identity, stage and look, and
# the next session would flip it back. Ownership therefore lives in the combined ledger
# (``~/.evopet/state.json``), which the drain writes from every profile at once -- and the drain
# renders the mirror from it. Until a ledger claims the mirror, nothing changes: a machine that
# has not opted into the combined ledger installs exactly as it always did.

MIRROR_OWNER = "evopet-drain"
PER_PROFILE_WRITER = "per-profile"
COMBINED_STATE_ENV = "EVOPET_STATE"


class MirrorOwnershipError(PetCompileError):
    """A build tried to write a desktop mirror that the combined ledger owns."""


def combined_ledger_path(value: str | Path | None = None) -> Path:
    """Where the combined ledger keeps mirror ownership (``EVOPET_STATE``, else ``~/.evopet``)."""
    import os

    raw = value or os.environ.get(COMBINED_STATE_ENV) or (Path.home() / ".evopet" / "state.json")
    return Path(raw).expanduser()


def mirror_claim(combined_path: Path | None = None) -> dict[str, Any] | None:
    """The combined ledger's mirror claim, or ``None`` when no ledger owns the mirror.

    Ownership is declared, never inferred: a ledger with no ``mirror`` block -- an older
    install, or a machine that never opted in -- owns nothing and blocks nothing.
    """
    path = combined_ledger_path(combined_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    block = payload.get("mirror") if isinstance(payload, dict) else None
    if not isinstance(block, dict) or not block.get("petdexHome"):
        return None
    claim = dict(block)
    claim["ledger"] = str(path)
    return claim


def assert_mirror_writer(petdex_home: Path, target: Path, writer: str) -> dict[str, Any] | None:
    """Refuse a mirror write the combined ledger has given to another writer.

    Scoped to the desktop home the claim names: the rule is about *that* mirror, so a build
    aimed elsewhere (a staging home, a test tmpdir) is untouched and needs no opt-out.
    """
    claim = mirror_claim()
    if claim is None or writer == claim.get("owner"):
        return claim
    owned = Path(str(claim["petdexHome"])).expanduser()
    try:
        owned_home = owned.resolve()
    except OSError:
        owned_home = owned
    if owned_home != Path(petdex_home).expanduser().resolve():
        return claim
    raise MirrorOwnershipError(
        f"refusing to write {target}: the desktop mirror is owned by {claim.get('owner')} "
        f"(declared in {claim['ledger']}), and a {writer} build must not overwrite it. "
        "The drain renders that mirror from the combined ledger; remove the \"mirror\" block "
        "from the combined ledger to hand it back."
    )


def ledger_description(state: dict[str, Any]) -> str:
    """A pet's one-line description, read off the ledger it mirrors.

    Stage and level come from the XP (and the ledger's own gates), not from whatever label the
    ledger carries: a pet labelled "teen" at an XP that earns "hatchling" must introduce itself
    as the creature the sheet actually draws. Hibernation is a condition, not a rung, so it is
    reported as itself.
    """
    name = state.get("displayName") or "TamaHermes"
    xp = int(state.get("xp") or 0)
    level = levels.level_for_xp(xp)
    stage = str(state.get("lifeStage") or "")
    try:
        earned = levels.stage_for_xp(xp, state.get("evolutionGates") or levels.DEFAULT_EVOLUTION_GATES)
    except (TypeError, ValueError):
        earned = stage or "egg"
    if stage != "hibernation":
        stage = earned
    return (
        f"{name} — {stage} stage, level {level}. Grown from your coding agents. "
        "An evolving desktop companion."
    )


def install_petdex_pet(
    catalog: Catalog,
    state: dict[str, Any],
    petdex_home: Path,
    build_dir: Path,
    force: bool = False,
    source_sheet: Path | None = None,
    kind: str | None = None,
    layout: str | None = None,
    writer: str = PER_PROFILE_WRITER,
    mirror_hash: str | None = None,
) -> dict[str, Any]:
    """Install the pet into a Petdex desktop home (``~/.petdex``).

    The Petdex desktop app renders the same artifact Hermes does — an 8x9 atlas
    of 192x208 cells plus ``pet.json`` — but it expects the conventional
    unversioned ``spritesheet.webp`` beside the manifest, so this lays the pet
    out that way instead of using the content-addressed name a Hermes home gets.

    ``source_sheet`` short-circuits the render: when the pet is already built
    for Hermes, mirroring it to the desktop is a file copy, not a rebuild.

    ``writer`` is the guard on a shared resource: once the combined ledger claims
    this mirror, only ``MIRROR_OWNER`` may write it (see ``assert_mirror_writer``).
    """
    pet_id = state.get("petId", "tamahermes")
    pet_dir = petdex_home / "pets" / pet_id
    target_sheet = pet_dir / SPRITESHEET_BASENAME
    target_manifest = pet_dir / "pet.json"
    # Before anything is rendered or replaced: does another writer own this mirror?
    assert_mirror_writer(petdex_home, target_manifest, writer)
    if pet_dir.exists() and not force and (target_sheet.exists() or target_manifest.exists()):
        raise PetCompileError(
            f"{pet_dir} already contains a Petdex pet. This protects the existing package; pass --force to replace it."
        )
    if source_sheet is not None and Path(source_sheet).is_file():
        sheet_source = Path(source_sheet)
    else:
        build_codex_pet(catalog, state, build_dir, layout=layout)
        sheet_source = build_dir / SPRITESHEET_BASENAME

    pet_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sheet_source, target_sheet)
    display_name = state.get("displayName") or "TamaHermes"
    manifest: dict[str, Any] = {
        "id": pet_id,
        "displayName": display_name,
        "description": ledger_description(state),
        "spritesheetPath": SPRITESHEET_BASENAME,
        # Read out of the ledger, never into it: the desktop copy is a mirror of the pet the
        # Hermes/Codex install produced, so a stale mirror can never reset a creator's gates.
        "evopet": evolution_block(state),
    }
    if kind:
        # `kind` is optional in Petdex (most shipped pets omit it), so it is
        # only written when the caller explicitly asks for one.
        manifest["kind"] = kind
    if writer == MIRROR_OWNER:
        # The owning writer stamps which ledger state this sheet came from, so the next run can
        # tell "unchanged" from "someone replaced it" without re-rendering anything.
        manifest["mirror"] = {"owner": writer, "hash": mirror_hash}
    target_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "petId": pet_id,
        "petDir": str(pet_dir),
        "manifest": str(target_manifest),
        "spritesheet": str(target_sheet),
        "spritesheetPath": SPRITESHEET_BASENAME,
        "sourceSheet": str(sheet_source),
        "layout": resolve_layout(layout),
        "writer": writer,
        "mirrorHash": mirror_hash,
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
    # Install is the one moment a declared gate list moves: absorb the manifest being replaced
    # into the ledger, then build -- so the new pet.json carries the ledger's gates and the
    # runtime evolves on them. A manifest that declares nothing leaves the ledger as it is. The
    # catalog is passed so a declaration this line cannot render is refused here, before it can
    # strand a running pet.
    sync_ledger_gates(
        state, read_manifest(target_manifest), source=str(target_manifest), catalog=catalog
    )
    # Defensively, and last: a ledger that reached install without a load -- or was written under
    # an older curve -- is normalised before it is drawn, so the built form cannot disagree with
    # the XP the ledger carries.
    normalize_ledger(state, catalog)
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
