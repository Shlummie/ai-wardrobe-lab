from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from . import settings
from .db import database
from .focus_group import (
    MAX_OUTPUT_TOKENS,
    MAX_PANEL_SIZE,
    SCREENING_LENSES,
    run_panel,
)
from .outfit_engine import _font, enumerate_combinations, utc_now


PROTOCOL_VERSION = "aiwl-tournament-v1"
STAGED_MODE = "staged"
FULL_PANEL_MODE = "full_panel"
BATCH_SIZE = 8
SEMIFINAL_SIZE = 24
FINAL_SIZE = 8
# A 1640x1040 high-detail sheet plus the prompt and JSON schema is comfortably
# below this input allowance. Combined with MAX_OUTPUT_TOKENS, it lets the
# runner reserve a deliberately conservative maximum before launching a wave.
MAX_INPUT_TOKENS_PER_CALL = 8000

MODEL_PRICING_PER_MILLION = {
    "gpt-5.6-luna": {"input": 1.0, "output": 6.0},
    "gpt-5.6-terra": {"input": 2.5, "output": 15.0},
    "gpt-5.6-sol": {"input": 5.0, "output": 30.0},
    "gpt-5.6": {"input": 5.0, "output": 30.0},
}


def estimate_tournament_calls(total_outfits: int) -> int:
    if total_outfits <= 0:
        return 0
    screening = math.ceil(total_outfits / BATCH_SIZE)
    semifinalists = min(SEMIFINAL_SIZE, total_outfits)
    semifinal = math.ceil(semifinalists / BATCH_SIZE) * MAX_PANEL_SIZE
    final = MAX_PANEL_SIZE if semifinalists else 0
    return screening + semifinal + final


def estimate_full_panel_calls(total_outfits: int) -> int:
    if total_outfits <= 0:
        return 0
    return math.ceil(total_outfits / BATCH_SIZE) * MAX_PANEL_SIZE


def conservative_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> float:
    pricing = MODEL_PRICING_PER_MILLION.get(model)
    if pricing is None:
        raise ValueError(f"No tournament pricing is configured for {model}.")
    # Treat every input token as a cache write at 1.25x. This intentionally
    # overestimates ordinary and cached inputs so the configured cap fails safe.
    cost = (
        input_tokens * pricing["input"] * 1.25
        + output_tokens * pricing["output"]
    ) / 1_000_000
    return round(cost, 6)


def select_diverse(
    scored: list[dict[str, Any]], count: int, max_per_base: int
) -> list[dict[str, Any]]:
    ordered = sorted(
        scored,
        key=lambda row: (-float(row["score"]["overall"]), row["outfit_id"]),
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    base_counts: Counter[str] = Counter()
    for row in ordered:
        if base_counts[row["base_key"]] >= max_per_base:
            continue
        selected.append(row)
        selected_ids.add(row["outfit_id"])
        base_counts[row["base_key"]] += 1
        if len(selected) >= count:
            return selected
    for row in ordered:
        if row["outfit_id"] in selected_ids:
            continue
        selected.append(row)
        if len(selected) >= count:
            break
    return selected


def _full_item(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "dominant_color": row["dominant_color"],
        "color_hex": row["color_hex"],
        "image_path": row["image_path"],
        "image_sha256": row["image_sha256"],
        "available": bool(row["available"]),
        "last_worn": row["last_worn"],
        "notes": row["notes"],
    }


def _base_key(items: list[dict[str, Any]]) -> str:
    ids = sorted(item["id"] for item in items if item["category"] != "accessory")
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()


def _panel_card_path(outfit_id: str, items: list[dict[str, Any]]) -> Path:
    content_key = hashlib.sha256(
        "|".join(item["image_sha256"] for item in items).encode("utf-8")
    ).hexdigest()[:12]
    return settings.TOURNAMENT_DIR / "cards" / f"{outfit_id}-{content_key}.jpg"


def _render_panel_card(
    outfit_id: str, items: list[dict[str, Any]]
) -> Path:
    destination = _panel_card_path(outfit_id, items)
    if destination.is_file():
        return destination
    canvas = Image.new("RGB", (920, 920), "#ece8df")
    count = len(items)
    columns = 2 if count > 1 else 1
    rows = max(1, math.ceil(count / columns))
    cell_width = 460 if columns == 2 else 920
    cell_height = 920 // rows
    for index, item in enumerate(items):
        column = index % columns
        row = index // columns
        left = column * 460
        top = row * cell_height
        image = Image.open(item["image_path"]).convert("RGB")
        contained = ImageOps.contain(
            image,
            (cell_width - 32, cell_height - 32),
            Image.Resampling.LANCZOS,
        )
        paste_x = left + (cell_width - contained.width) // 2
        paste_y = top + (cell_height - contained.height) // 2
        canvas.paste(contained, (paste_x, paste_y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "JPEG", quality=90, optimize=True)
    return destination


def _render_contact_sheet(
    tournament_id: str,
    stage: str,
    batch_index: int,
    outfit_rows: list,
    item_lookup: dict[str, dict[str, Any]],
) -> Path:
    destination = (
        settings.TOURNAMENT_DIR
        / tournament_id
        / f"{stage}-{batch_index:03d}.jpg"
    )
    canvas = Image.new("RGB", (1640, 1040), "#1d1c1a")
    draw = ImageDraw.Draw(canvas)
    label_font = _font(20, bold=True)
    for index, outfit in enumerate(outfit_rows):
        item_ids = json.loads(outfit["item_ids_json"])
        items = [item_lookup[item_id] for item_id in item_ids]
        card_path = _render_panel_card(outfit["id"], items)
        card = Image.open(card_path).convert("RGB")
        card = ImageOps.fit(card, (382, 382), method=Image.Resampling.LANCZOS)
        column = index % 4
        row = index // 4
        left = 30 + column * 400
        top = 50 + row * 490
        canvas.paste(card, (left, top))
        label = f"OUTFIT {index + 1}"
        draw.rectangle((left, top + 382, left + 382, top + 432), fill="#f8f6f0")
        draw.text(
            (left + 15, top + 393),
            label,
            fill="#1d1c1a",
            font=label_font,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "JPEG", quality=90, optimize=True)
    return destination


def _batch_groups(rows: list[dict[str, Any]], batch_count: int) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = [[] for _ in range(batch_count)]
    for index, row in enumerate(rows):
        groups[index % batch_count].append(row)
    return [group for group in groups if group]


def _insert_batches(
    connection,
    tournament_id: str,
    stage: str,
    groups: list[list[dict[str, Any]]],
    panel_size: int,
) -> None:
    now = utc_now()
    for index, group in enumerate(groups):
        connection.execute(
            """
            INSERT OR IGNORE INTO tournament_batches
                (tournament_id, stage, batch_index, outfit_ids_json, panel_size,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                tournament_id,
                stage,
                index,
                json.dumps([row["outfit_id"] for row in group]),
                panel_size,
                now,
                now,
            ),
        )


def prepare_tournament(
    *,
    audience: str,
    impression: str,
    model: str,
    budget_usd: float,
    mode: str = STAGED_MODE,
) -> str:
    if model not in MODEL_PRICING_PER_MILLION:
        raise ValueError(f"Tournament pricing is unavailable for {model}.")
    if mode not in {STAGED_MODE, FULL_PANEL_MODE}:
        raise ValueError(f"Unsupported tournament mode: {mode}.")
    with database() as connection:
        garment_rows = connection.execute(
            "SELECT * FROM garments WHERE available = 1 ORDER BY category, created_at"
        ).fetchall()
    items = [_full_item(row) for row in garment_rows]
    combinations = enumerate_combinations(items)
    if not combinations:
        raise ValueError("No complete enabled outfits are available for a tournament.")

    tournament_id = str(uuid.uuid4())
    seed = hashlib.sha256(
        f"{PROTOCOL_VERSION}|{mode}|{audience}|{impression}|{model}".encode("utf-8")
    ).hexdigest()
    ordered = sorted(
        combinations,
        key=lambda candidate: hashlib.sha256(
            f"{seed}|{candidate['signature']}".encode("utf-8")
        ).hexdigest(),
    )
    now = utc_now()
    estimated_calls = (
        estimate_full_panel_calls(len(ordered))
        if mode == FULL_PANEL_MODE
        else estimate_tournament_calls(len(ordered))
    )
    with database() as connection:
        active = connection.execute(
            "SELECT id FROM tournament_runs WHERE status IN ('preparing', 'running') LIMIT 1"
        ).fetchone()
        if active:
            raise RuntimeError("A tournament is already preparing or running.")
        connection.execute(
            """
            INSERT INTO tournament_runs
                (id, audience, impression, model, mode, status, stage, total_outfits,
                 estimated_calls, budget_usd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'preparing', 'screening', ?, ?, ?, ?, ?)
            """,
            (
                tournament_id,
                audience,
                impression,
                model,
                mode,
                len(ordered),
                estimated_calls,
                budget_usd,
                now,
                now,
            ),
        )
        existing_rows = connection.execute(
            "SELECT * FROM outfits"
        ).fetchall()
        existing_by_signature = {row["signature"]: row for row in existing_rows}
        screen_rows: list[dict[str, Any]] = []
        for position, candidate in enumerate(ordered):
            existing = existing_by_signature.get(candidate["signature"])
            if existing:
                outfit_id = existing["id"]
                connection.execute(
                    "UPDATE outfits SET local_score = ? WHERE id = ?",
                    (candidate["local_score"], outfit_id),
                )
            else:
                outfit_id = str(uuid.uuid4())
                card_path = _panel_card_path(outfit_id, candidate["items"])
                connection.execute(
                    """
                    INSERT INTO outfits
                        (id, signature, item_ids_json, local_score, board_path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outfit_id,
                        candidate["signature"],
                        json.dumps([item["id"] for item in candidate["items"]]),
                        candidate["local_score"],
                        str(card_path),
                        now,
                    ),
                )
            base_key = _base_key(candidate["items"])
            connection.execute(
                """
                INSERT INTO tournament_entries
                    (tournament_id, outfit_id, base_key, screen_order)
                VALUES (?, ?, ?, ?)
                """,
                (tournament_id, outfit_id, base_key, position),
            )
            screen_rows.append({"outfit_id": outfit_id})

        groups = [
            screen_rows[index : index + BATCH_SIZE]
            for index in range(0, len(screen_rows), BATCH_SIZE)
        ]
        screening_panel_size = (
            MAX_PANEL_SIZE if mode == FULL_PANEL_MODE else 1
        )
        _insert_batches(
            connection,
            tournament_id,
            "screening",
            groups,
            screening_panel_size,
        )
        connection.execute(
            "UPDATE tournament_runs SET status = 'ready', updated_at = ? WHERE id = ?",
            (utc_now(), tournament_id),
        )
    return tournament_id


def _score_rows(tournament_id: str, column: str) -> list[dict[str, Any]]:
    if column not in {
        "screening_score_json",
        "semifinal_score_json",
        "final_score_json",
    }:
        raise ValueError("Unsupported tournament score column.")
    with database() as connection:
        rows = connection.execute(
            f"""
            SELECT outfit_id, base_key, {column} AS score_json
            FROM tournament_entries
            WHERE tournament_id = ? AND {column} IS NOT NULL
            """,
            (tournament_id,),
        ).fetchall()
    return [
        {
            "outfit_id": row["outfit_id"],
            "base_key": row["base_key"],
            "score": json.loads(row["score_json"]),
        }
        for row in rows
    ]


def _advance_to_semifinal(tournament_id: str) -> None:
    scored = _score_rows(tournament_id, "screening_score_json")
    selected = select_diverse(scored, min(SEMIFINAL_SIZE, len(scored)), 2)
    batch_count = math.ceil(len(selected) / BATCH_SIZE)
    groups = _batch_groups(selected, batch_count)
    with database() as connection:
        for row in selected:
            connection.execute(
                "UPDATE tournament_entries SET semifinalist = 1 WHERE tournament_id = ? AND outfit_id = ?",
                (tournament_id, row["outfit_id"]),
            )
        _insert_batches(
            connection, tournament_id, "semifinal", groups, MAX_PANEL_SIZE
        )
        connection.execute(
            "UPDATE tournament_runs SET stage = 'semifinal', updated_at = ? WHERE id = ?",
            (utc_now(), tournament_id),
        )


def _advance_to_final(tournament_id: str) -> None:
    scored = _score_rows(tournament_id, "semifinal_score_json")
    selected = select_diverse(scored, min(FINAL_SIZE, len(scored)), 1)
    with database() as connection:
        for row in selected:
            connection.execute(
                "UPDATE tournament_entries SET finalist = 1 WHERE tournament_id = ? AND outfit_id = ?",
                (tournament_id, row["outfit_id"]),
            )
        _insert_batches(
            connection, tournament_id, "final", [selected], MAX_PANEL_SIZE
        )
        connection.execute(
            "UPDATE tournament_runs SET stage = 'final', updated_at = ? WHERE id = ?",
            (utc_now(), tournament_id),
        )


def _complete_tournament(tournament_id: str) -> None:
    scored = _score_rows(tournament_id, "final_score_json")
    ranked = sorted(
        scored,
        key=lambda row: (-float(row["score"]["overall"]), row["outfit_id"]),
    )
    result = [
        {"outfit_id": row["outfit_id"], "rank": index, **row["score"]}
        for index, row in enumerate(ranked, start=1)
    ]
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE tournament_runs
            SET status = 'complete', stage = 'complete', result_json = ?,
                error = NULL, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (json.dumps(result), now, now, tournament_id),
        )


def _complete_full_panel(tournament_id: str) -> None:
    scored = _score_rows(tournament_id, "screening_score_json")
    ranked = sorted(
        scored,
        key=lambda row: (-float(row["score"]["overall"]), row["outfit_id"]),
    )
    result = [
        {"outfit_id": row["outfit_id"], "rank": index, **row["score"]}
        for index, row in enumerate(ranked, start=1)
    ]
    now = utc_now()
    with database() as connection:
        for row in result[:FINAL_SIZE]:
            connection.execute(
                "UPDATE tournament_entries SET finalist = 1 WHERE tournament_id = ? AND outfit_id = ?",
                (tournament_id, row["outfit_id"]),
            )
        connection.execute(
            """
            UPDATE tournament_runs
            SET status = 'complete', stage = 'complete', result_json = ?,
                error = NULL, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (json.dumps(result), now, now, tournament_id),
        )


def _refresh_usage(connection, tournament_id: str, model: str) -> tuple[int, float]:
    totals = connection.execute(
        """
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cached_tokens), 0) AS cached_tokens
        FROM tournament_usage WHERE tournament_id = ?
        """,
        (tournament_id,),
    ).fetchone()
    cost = conservative_cost_usd(
        model, totals["input_tokens"], totals["output_tokens"]
    )
    connection.execute(
        """
        UPDATE tournament_runs
        SET completed_calls = ?, input_tokens = ?, output_tokens = ?,
            cached_tokens = ?, estimated_cost_usd = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            totals["calls"],
            totals["input_tokens"],
            totals["output_tokens"],
            totals["cached_tokens"],
            cost,
            utc_now(),
            tournament_id,
        ),
    )
    return totals["calls"], cost


async def _process_batch(tournament_id: str, stage: str, batch_index: int) -> None:
    with database() as connection:
        run = connection.execute(
            "SELECT * FROM tournament_runs WHERE id = ?", (tournament_id,)
        ).fetchone()
        batch = connection.execute(
            """
            SELECT * FROM tournament_batches
            WHERE tournament_id = ? AND stage = ? AND batch_index = ?
            """,
            (tournament_id, stage, batch_index),
        ).fetchone()
        if not run or not batch or batch["status"] == "complete":
            return
        connection.execute(
            """
            UPDATE tournament_batches
            SET status = 'running', error = NULL, updated_at = ?
            WHERE tournament_id = ? AND stage = ? AND batch_index = ?
            """,
            (utc_now(), tournament_id, stage, batch_index),
        )
        outfit_ids = json.loads(batch["outfit_ids_json"])
        placeholders = ",".join("?" for _ in outfit_ids)
        outfit_rows = connection.execute(
            f"SELECT * FROM outfits WHERE id IN ({placeholders})", outfit_ids
        ).fetchall()
        outfit_by_id = {row["id"]: row for row in outfit_rows}
        ordered_outfits = [outfit_by_id[outfit_id] for outfit_id in outfit_ids]
        garment_rows = connection.execute("SELECT * FROM garments").fetchall()

    item_lookup = {row["id"]: _full_item(row) for row in garment_rows}
    try:
        contact_sheet = await asyncio.to_thread(
            _render_contact_sheet,
            tournament_id,
            stage,
            batch_index,
            ordered_outfits,
            item_lookup,
        )
        labels = [f"OUTFIT {index}" for index in range(1, len(outfit_ids) + 1)]
        ranking, usage_rows = await run_panel(
            api_key=settings.OPENAI_API_KEY,
            model=run["model"],
            audience=run["audience"],
            impression=run["impression"],
            contact_sheet=contact_sheet,
            labels=labels,
            panel_size=batch["panel_size"],
            persona_lenses=(
                SCREENING_LENSES
                if stage == "screening" and run["mode"] != FULL_PANEL_MODE
                else None
            ),
        )
        by_label = {row["panel_label"]: row for row in ranking}
        score_column = {
            "screening": "screening_score_json",
            "semifinal": "semifinal_score_json",
            "final": "final_score_json",
        }[stage]
        now = utc_now()
        with database() as connection:
            for index, outfit_id in enumerate(outfit_ids, start=1):
                score = by_label[f"OUTFIT {index}"]
                connection.execute(
                    f"UPDATE tournament_entries SET {score_column} = ? WHERE tournament_id = ? AND outfit_id = ?",
                    (json.dumps(score), tournament_id, outfit_id),
                )
            for usage in usage_rows:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO tournament_usage
                        (tournament_id, stage, batch_index, persona_key, model,
                         input_tokens, output_tokens, cached_tokens, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tournament_id,
                        stage,
                        batch_index,
                        usage["persona_key"],
                        run["model"],
                        usage["input_tokens"],
                        usage["output_tokens"],
                        usage["cached_tokens"],
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE tournament_batches
                SET status = 'complete', result_json = ?, error = NULL, updated_at = ?
                WHERE tournament_id = ? AND stage = ? AND batch_index = ?
                """,
                (json.dumps(ranking), now, tournament_id, stage, batch_index),
            )
            _, cost = _refresh_usage(connection, tournament_id, run["model"])
            if float(run["budget_usd"]) > 0 and cost >= float(run["budget_usd"]):
                connection.execute(
                    """
                    UPDATE tournament_runs
                    SET status = 'budget_stopped', error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        f"The conservative ${run['budget_usd']:.2f} budget cap was reached.",
                        utc_now(),
                        tournament_id,
                    ),
                )
    except Exception as exc:
        detail = str(exc)[-3000:]
        with database() as connection:
            connection.execute(
                """
                UPDATE tournament_batches
                SET status = 'failed', error = ?, updated_at = ?
                WHERE tournament_id = ? AND stage = ? AND batch_index = ?
                """,
                (detail, utc_now(), tournament_id, stage, batch_index),
            )
        raise


def _pending_batches(tournament_id: str, stage: str) -> list[dict[str, int]]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT batch_index, panel_size FROM tournament_batches
            WHERE tournament_id = ? AND stage = ? AND status != 'complete'
            ORDER BY batch_index
            """,
            (tournament_id, stage),
        ).fetchall()
    return [
        {"batch_index": row["batch_index"], "panel_size": row["panel_size"]}
        for row in rows
    ]


def _reserve_cost_usd(model: str, calls: int) -> float:
    return conservative_cost_usd(
        model,
        MAX_INPUT_TOKENS_PER_CALL * calls,
        MAX_OUTPUT_TOKENS * calls,
    )


def _run_row(tournament_id: str):
    with database() as connection:
        return connection.execute(
            "SELECT * FROM tournament_runs WHERE id = ?", (tournament_id,)
        ).fetchone()


async def run_tournament(tournament_id: str) -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE tournament_batches SET status = 'pending', error = NULL
            WHERE tournament_id = ? AND status IN ('failed', 'running')
            """,
            (tournament_id,),
        )
        connection.execute(
            "UPDATE tournament_runs SET status = 'running', error = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), tournament_id),
        )
    try:
        while True:
            run = _run_row(tournament_id)
            if not run or run["status"] != "running":
                return
            stage = run["stage"]
            pending = _pending_batches(tournament_id, stage)
            if pending:
                concurrency = (
                    settings.TOURNAMENT_SCREEN_CONCURRENCY
                    if stage == "screening"
                    else 1
                )
                wave = pending[:concurrency]
                reserved_calls = sum(batch["panel_size"] for batch in wave)
                reserve_cost = _reserve_cost_usd(run["model"], reserved_calls)
                if (
                    float(run["budget_usd"]) > 0
                    and float(run["estimated_cost_usd"]) + reserve_cost
                    > float(run["budget_usd"])
                ):
                    with database() as connection:
                        connection.execute(
                            """
                            UPDATE tournament_runs
                            SET status = 'budget_stopped', error = ?, updated_at = ?
                            WHERE id = ? AND status = 'running'
                            """,
                            (
                                "The next API wave could exceed the conservative "
                                f"${run['budget_usd']:.2f} budget cap.",
                                utc_now(),
                                tournament_id,
                            ),
                        )
                    return
                results = await asyncio.gather(
                    *(
                        _process_batch(
                            tournament_id, stage, batch["batch_index"]
                        )
                        for batch in wave
                    ),
                    return_exceptions=True,
                )
                failures = [result for result in results if isinstance(result, Exception)]
                if failures:
                    raise failures[0]
                continue
            if stage == "screening":
                if run["mode"] == FULL_PANEL_MODE:
                    _complete_full_panel(tournament_id)
                    return
                _advance_to_semifinal(tournament_id)
            elif stage == "semifinal":
                _advance_to_final(tournament_id)
            elif stage == "final":
                _complete_tournament(tournament_id)
                return
            else:
                raise RuntimeError(f"Unknown tournament stage: {stage}")
    except Exception as exc:
        detail = str(exc)[-3000:]
        with database() as connection:
            connection.execute(
                """
                UPDATE tournament_runs
                SET status = 'paused', error = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (detail, utc_now(), tournament_id),
            )


def mark_interrupted_tournaments_paused() -> None:
    with database() as connection:
        connection.execute(
            """
            UPDATE tournament_runs
            SET status = 'paused', error = 'The local service restarted; resume to continue from the last completed batch.', updated_at = ?
            WHERE status IN ('preparing', 'running')
            """,
            (utc_now(),),
        )


def tournament_snapshot(tournament_id: str | None = None) -> dict[str, Any] | None:
    with database() as connection:
        if tournament_id:
            run = connection.execute(
                "SELECT * FROM tournament_runs WHERE id = ?", (tournament_id,)
            ).fetchone()
        else:
            run = connection.execute(
                "SELECT * FROM tournament_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not run:
            return None
        stage_counts = {
            row["stage"]: {"complete": row["complete"], "total": row["total"]}
            for row in connection.execute(
                """
                SELECT stage, COUNT(*) AS total,
                       SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS complete
                FROM tournament_batches WHERE tournament_id = ? GROUP BY stage
                """,
                (run["id"],),
            ).fetchall()
        }
        scored = connection.execute(
            """
            SELECT
                SUM(CASE WHEN screening_score_json IS NOT NULL THEN 1 ELSE 0 END) AS screened,
                SUM(semifinalist) AS semifinalists,
                SUM(finalist) AS finalists
            FROM tournament_entries WHERE tournament_id = ?
            """,
            (run["id"],),
        ).fetchone()
    result = dict(run)
    result["stage_counts"] = stage_counts
    result["screened_outfits"] = int(scored["screened"] or 0)
    result["semifinalists"] = int(scored["semifinalists"] or 0)
    result["finalists"] = int(scored["finalists"] or 0)
    result["results"] = json.loads(run["result_json"]) if run["result_json"] else []
    result.pop("result_json", None)
    return result
