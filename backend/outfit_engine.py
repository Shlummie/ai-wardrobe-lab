from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .settings import CACHE_DIR, OUTFIT_DIR


NEUTRALS = {"black", "charcoal", "gray", "white", "cream", "brown", "tan", "navy"}
COLOR_HUES = {
    "red": 0,
    "burgundy": 345,
    "orange": 28,
    "yellow": 52,
    "green": 126,
    "olive": 80,
    "blue": 215,
    "navy": 225,
    "purple": 275,
    "pink": 335,
    "brown": 28,
    "tan": 34,
}
MAX_ACCESSORIES_PER_OUTFIT = 4


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _color_compatibility(first: str, second: str) -> float:
    if first == second:
        return 0.86
    if first in NEUTRALS or second in NEUTRALS:
        return 0.96
    hue_a = COLOR_HUES.get(first)
    hue_b = COLOR_HUES.get(second)
    if hue_a is None or hue_b is None:
        return 0.68
    distance = abs(hue_a - hue_b)
    distance = min(distance, 360 - distance)
    if distance <= 32:
        return 0.84
    if 145 <= distance <= 215:
        return 0.82
    if 55 <= distance <= 135:
        return 0.70
    return 0.58


def _recency_score(last_worn: str | None) -> float:
    if not last_worn:
        return 1.0
    try:
        worn = date.fromisoformat(last_worn)
    except ValueError:
        return 0.7
    days = max(0, (date.today() - worn).days)
    return min(1.0, 0.25 + days / 21)


def score_combination(items: list[dict]) -> float:
    colors = [item["dominant_color"] for item in items]
    pair_scores = [
        _color_compatibility(first, second)
        for first, second in itertools.combinations(colors, 2)
    ]
    color_score = sum(pair_scores) / len(pair_scores) if pair_scores else 0.75
    recency = sum(_recency_score(item.get("last_worn")) for item in items) / len(items)
    categories = {item["category"] for item in items}
    complete = 1.0 if ({"top", "bottom"} <= categories or "one_piece" in categories) else 0.0
    shoes = 1.0 if "shoes" in categories else 0.72
    # This is deliberately a coordination filter, not an audience prediction.
    return round(10 * (0.54 * color_score + 0.20 * recency + 0.17 * complete + 0.09 * shoes), 2)


def _accessory_stacks(accessories: list[dict]) -> list[tuple[dict, ...]]:
    """Return every wearable accessory subset up to the local complexity cap."""
    return [
        stack
        for size in range(0, min(len(accessories), MAX_ACCESSORIES_PER_OUTFIT) + 1)
        for stack in itertools.combinations(accessories, size)
    ]


def enumerate_combinations(items: list[dict], max_candidates: int = 5000) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        if item.get("available"):
            grouped.setdefault(item["category"], []).append(item)

    bases: list[list[dict]] = []
    for top in grouped.get("top", []):
        for bottom in grouped.get("bottom", []):
            bases.append([top, bottom])
    bases.extend([[piece] for piece in grouped.get("one_piece", [])])
    if not bases:
        raise ValueError("Add an available top and bottom, or an available one-piece item.")

    shoe_choices: list[dict | None] = grouped.get("shoes", []) or [None]
    outer_choices: list[dict | None] = [None, *grouped.get("outerwear", [])]
    accessory_choices = _accessory_stacks(grouped.get("accessory", []))

    combinations: list[dict] = []
    for base, shoes, outerwear, accessories in itertools.product(
        bases, shoe_choices, outer_choices, accessory_choices
    ):
        chosen = [
            *base,
            *[item for item in (shoes, outerwear) if item],
            *accessories,
        ]
        signature = hashlib.sha256(
            "|".join(sorted(item["id"] for item in chosen)).encode("utf-8")
        ).hexdigest()
        combinations.append(
            {
                "signature": signature,
                "items": chosen,
                "local_score": score_combination(chosen),
            }
        )
        if len(combinations) >= max(1, max_candidates):
            break

    combinations.sort(key=lambda candidate: (-candidate["local_score"], candidate["signature"]))
    return combinations


def generate_combinations(items: list[dict], limit: int = 8) -> tuple[list[dict], int]:
    combinations = enumerate_combinations(items)
    return combinations[: max(1, min(limit, 12))], len(combinations)


def render_outfit_board(outfit_id: str, items: list[dict], rank: int) -> Path:
    destination = OUTFIT_DIR / f"{outfit_id}.jpg"
    board = Image.new("RGB", (920, 920), "#ece8df")
    draw = ImageDraw.Draw(board)
    draw.rectangle((0, 0, 920, 78), fill="#1d1c1a")
    draw.text((28, 22), f"LOOK {rank:02d}", fill="#c8f04b", font=_font(25, bold=True))
    draw.text((884, 25), "AWL", fill="#f8f6f0", font=_font(16, bold=True), anchor="ra")

    count = len(items)
    columns = 2 if count > 1 else 1
    rows = math.ceil(count / columns)
    content_top = 94
    content_bottom = 805
    cell_width = 440 if columns == 2 else 860
    cell_height = (content_bottom - content_top) // rows

    for index, item in enumerate(items):
        column = index % columns
        row = index // columns
        left = 20 + column * 440
        top = content_top + row * cell_height
        image = Image.open(item["image_path"]).convert("RGB")
        contained = ImageOps.contain(
            image,
            (cell_width - 28, cell_height - 42),
            Image.Resampling.LANCZOS,
        )
        paste_x = left + (cell_width - contained.width) // 2
        paste_y = top + (cell_height - contained.height) // 2
        board.paste(contained, (paste_x, paste_y))
        draw.text(
            (left + 10, top + cell_height - 28),
            item["name"][:34],
            fill="#44413c",
            font=_font(13, bold=True),
        )

    draw.rectangle((0, 805, 920, 920), fill="#f9f7f1")
    names = "  ·  ".join(item["name"] for item in items)
    draw.text((28, 836), names[:98], fill="#1d1c1a", font=_font(16, bold=True))
    colors = " / ".join(item["dominant_color"] for item in items)
    draw.text((28, 875), colors.upper(), fill="#777169", font=_font(11))
    destination.parent.mkdir(parents=True, exist_ok=True)
    board.save(destination, "JPEG", quality=90, optimize=True)
    return destination


def render_contact_sheet(outfits: list[dict], cache_key_seed: str) -> Path:
    digest = hashlib.sha256(cache_key_seed.encode("utf-8")).hexdigest()[:20]
    destination = CACHE_DIR / f"panel-{digest}.jpg"
    canvas = Image.new("RGB", (1640, 1040), "#1d1c1a")
    draw = ImageDraw.Draw(canvas)
    draw.text((34, 24), "AUDIENCE SHORTLIST", fill="#c8f04b", font=_font(26, bold=True))
    draw.text((1604, 30), "AI Wardrobe Lab", fill="#f9f7f1", font=_font(16), anchor="ra")

    for index, outfit in enumerate(outfits[:8]):
        column = index % 4
        row = index // 4
        left = 30 + column * 400
        top = 82 + row * 474
        card = Image.open(outfit["board_path"]).convert("RGB")
        card = ImageOps.fit(card, (382, 382), method=Image.Resampling.LANCZOS)
        canvas.paste(card, (left, top))
        label = f"OUTFIT {index + 1}"
        draw.rectangle((left, top + 382, left + 382, top + 432), fill="#f8f6f0")
        draw.text((left + 15, top + 396), label, fill="#1d1c1a", font=_font(17, bold=True))
        outfit["panel_label"] = label

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "JPEG", quality=88, optimize=True)
    return destination


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def outfit_record(candidate: dict, outfit_id: str, board_path: Path) -> dict:
    return {
        "id": outfit_id,
        "signature": candidate["signature"],
        "item_ids_json": json.dumps([item["id"] for item in candidate["items"]]),
        "local_score": candidate["local_score"],
        "board_path": str(board_path),
        "created_at": utc_now(),
    }
