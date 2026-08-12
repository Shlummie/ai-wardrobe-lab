from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .settings import DATABASE_PATH, ensure_data_dirs


SCHEMA = """
CREATE TABLE IF NOT EXISTS garments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    image_path TEXT NOT NULL,
    image_sha256 TEXT NOT NULL UNIQUE,
    dominant_color TEXT NOT NULL,
    color_hex TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    available INTEGER NOT NULL DEFAULT 1,
    last_worn TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS garments_available_category_idx
ON garments (available, category);

CREATE TABLE IF NOT EXISTS reference_photos (
    id TEXT PRIMARY KEY,
    image_path TEXT NOT NULL,
    privacy_path TEXT NOT NULL,
    privacy_method TEXT NOT NULL,
    faces_detected INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outfits (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    item_ids_json TEXT NOT NULL,
    local_score REAL NOT NULL,
    board_path TEXT NOT NULL,
    tryon_path TEXT,
    tryon_privacy_path TEXT,
    tryon_reference_id TEXT,
    tryon_status TEXT NOT NULL DEFAULT 'not_generated',
    tryon_error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS outfits_created_idx ON outfits (created_at DESC);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,
    audience TEXT NOT NULL,
    impression TEXT NOT NULL,
    model TEXT NOT NULL,
    panel_size INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    api_calls INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS evaluations_created_idx
ON evaluations (created_at DESC);

CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id TEXT NOT NULL,
    persona_key TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (evaluation_id) REFERENCES evaluations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tournament_runs (
    id TEXT PRIMARY KEY,
    audience TEXT NOT NULL,
    impression TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'staged',
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    total_outfits INTEGER NOT NULL,
    estimated_calls INTEGER NOT NULL,
    completed_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    budget_usd REAL NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS tournament_runs_created_idx
ON tournament_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS tournament_entries (
    tournament_id TEXT NOT NULL,
    outfit_id TEXT NOT NULL,
    base_key TEXT NOT NULL,
    screen_order INTEGER NOT NULL,
    screening_score_json TEXT,
    semifinal_score_json TEXT,
    final_score_json TEXT,
    semifinalist INTEGER NOT NULL DEFAULT 0,
    finalist INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tournament_id, outfit_id),
    FOREIGN KEY (tournament_id) REFERENCES tournament_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (outfit_id) REFERENCES outfits(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS tournament_entries_stage_idx
ON tournament_entries (tournament_id, semifinalist, finalist);

CREATE TABLE IF NOT EXISTS tournament_batches (
    tournament_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    batch_index INTEGER NOT NULL,
    outfit_ids_json TEXT NOT NULL,
    panel_size INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tournament_id, stage, batch_index),
    FOREIGN KEY (tournament_id) REFERENCES tournament_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS tournament_batches_status_idx
ON tournament_batches (tournament_id, stage, status);

CREATE TABLE IF NOT EXISTS tournament_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    batch_index INTEGER NOT NULL,
    persona_key TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (tournament_id, stage, batch_index, persona_key),
    FOREIGN KEY (tournament_id) REFERENCES tournament_runs(id) ON DELETE CASCADE
);
"""

def connect() -> sqlite3.Connection:
    ensure_data_dirs()
    connection = sqlite3.connect(DATABASE_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize() -> None:
    with database() as connection:
        connection.executescript(SCHEMA)
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(outfits)").fetchall()
        }
        migrations = {
            "tryon_path": "TEXT",
            "tryon_privacy_path": "TEXT",
            "tryon_reference_id": "TEXT",
            "tryon_status": "TEXT NOT NULL DEFAULT 'not_generated'",
            "tryon_error": "TEXT",
        }
        for column, declaration in migrations.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE outfits ADD COLUMN {column} {declaration}"
                )
        tournament_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(tournament_runs)"
            ).fetchall()
        }
        if "mode" not in tournament_columns:
            connection.execute(
                "ALTER TABLE tournament_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'staged'"
            )
