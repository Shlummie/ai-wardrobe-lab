from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import settings
from .db import database, initialize
from .focus_group import MAX_PANEL_SIZE, PROMPT_VERSION, run_panel
from .image_tools import decode_and_normalize, make_privacy_copy
from .mobile_sessions import (
    MobileAuthenticationError,
    MobileSessionError,
    MobileSessionExpired,
    discover_private_ipv4,
    is_loopback_client,
    mobile_sessions,
)
from .outfit_engine import (
    generate_combinations,
    outfit_record,
    render_contact_sheet,
    render_outfit_board,
    utc_now,
)
from .tournament import (
    FULL_PANEL_MODE,
    PROTOCOL_VERSION,
    mark_interrupted_tournaments_paused,
    prepare_tournament,
    run_tournament,
    tournament_snapshot,
)
from .tryon_service import SUPPORTED_CATEGORIES, run_tryon_batch


Category = Literal["top", "bottom", "one_piece", "shoes", "outerwear", "accessory"]
VALID_CATEGORIES = {"top", "bottom", "one_piece", "shoes", "outerwear", "accessory"}


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    category: Category | None = None
    available: bool | None = None
    last_worn: str | None = None
    notes: str | None = Field(default=None, max_length=300)


class GenerateRequest(BaseModel):
    limit: int = Field(default=8, ge=1, le=12)


class EvaluationRequest(BaseModel):
    audience: str = Field(min_length=3, max_length=300)
    impression: str = Field(min_length=3, max_length=300)
    outfit_ids: list[str] = Field(min_length=1, max_length=8)
    panel_size: int = Field(
        default=settings.PANEL_SIZE,
        ge=1,
        le=MAX_PANEL_SIZE,
        description="Number of independent audience lenses and maximum new OpenAI calls.",
    )


class TournamentRequest(BaseModel):
    audience: str = Field(min_length=3, max_length=300)
    impression: str = Field(min_length=3, max_length=300)
    mode: Literal["staged", "full_panel"] = "staged"


class TryOnRequest(BaseModel):
    outfit_ids: list[str] = Field(min_length=1, max_length=3)
    force: bool = False


class MobileAuthRequest(BaseModel):
    join_token: str = Field(min_length=20, max_length=200)
    pin: str = Field(pattern=r"^\d{6}$")


settings.ensure_data_dirs()
initialize()
mark_interrupted_tournaments_paused()

app = FastAPI(title="AI Wardrobe Lab local service", version="0.1.0")
tournament_tasks: dict[str, asyncio.Task[None]] = {}
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=(
        r"^http://(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}):(?:3000|3001)$"
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=settings.DATA_DIR), name="media")


@app.middleware("http")
async def restrict_private_remote_surface(request: Request, call_next):
    """Only the token-gated mobile endpoints are reachable from another device."""
    client_host = request.client.host if request.client else None
    if is_loopback_client(client_host):
        return await call_next(request)
    if request.method == "OPTIONS" or request.url.path.startswith("/api/mobile/"):
        return await call_next(request)
    return JSONResponse(
        status_code=403,
        content={
            "detail": "This private endpoint is available only on the AI Wardrobe Lab host."
        },
    )


def _media_url(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(settings.DATA_DIR)
    except ValueError as exc:
        raise RuntimeError("A stored media path escaped the local data directory.") from exc
    return f"/media/{relative.as_posix()}"


def _row_to_item(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "dominant_color": row["dominant_color"],
        "color_hex": row["color_hex"],
        "media_url": _media_url(row["image_path"]),
        "available": bool(row["available"]),
        "last_worn": row["last_worn"],
        "notes": row["notes"],
    }


def _full_item(row) -> dict:
    item = _row_to_item(row)
    item["image_path"] = row["image_path"]
    item["image_sha256"] = row["image_sha256"]
    return item


def _reference_response(row) -> dict:
    return {
        "id": row["id"],
        "media_url": _media_url(row["image_path"]),
        "privacy_media_url": _media_url(row["privacy_path"]),
        "privacy_method": row["privacy_method"],
        "faces_detected": row["faces_detected"],
        "created_at": row["created_at"],
    }


def _outfit_response(row, item_lookup: dict[str, dict]) -> dict:
    item_ids = json.loads(row["item_ids_json"])
    return {
        "id": row["id"],
        "item_ids": item_ids,
        "item_names": [item_lookup[item_id]["name"] for item_id in item_ids if item_id in item_lookup],
        "local_score": row["local_score"],
        "board_media_url": _media_url(row["board_path"]),
        "board_path": row["board_path"],
        **_outfit_runtime_fields(row),
    }


def _outfit_runtime_fields(row) -> dict:
    tryon_path = row["tryon_path"]
    tryon_privacy_path = row["tryon_privacy_path"]
    return {
        "tryon": {
            "status": row["tryon_status"],
            "media_url": _media_url(tryon_path) if tryon_path and Path(tryon_path).exists() else None,
            "privacy_media_url": (
                _media_url(tryon_privacy_path)
                if tryon_privacy_path and Path(tryon_privacy_path).exists()
                else None
            ),
            "reference_id": row["tryon_reference_id"],
            "error": row["tryon_error"],
        },
    }


def _refresh_result_runtime(result: dict) -> dict:
    outfit_ids = [outfit["id"] for outfit in result.get("outfits", [])]
    if not outfit_ids:
        return result
    placeholders = ",".join("?" for _ in outfit_ids)
    with database() as connection:
        rows = connection.execute(
            f"SELECT * FROM outfits WHERE id IN ({placeholders})", outfit_ids
        ).fetchall()
    lookup = {row["id"]: row for row in rows}
    for outfit in result["outfits"]:
        if outfit["id"] in lookup:
            outfit.update(_outfit_runtime_fields(lookup[outfit["id"]]))
    return result


def _tournament_response(snapshot: dict | None) -> dict | None:
    if snapshot is None:
        return None
    response = dict(snapshot)
    raw_results = response.pop("results", [])
    response["ranked_outfits"] = len(raw_results)
    visible_results = raw_results[:8]
    outfits: list[dict] = []
    if visible_results:
        outfit_ids = [result["outfit_id"] for result in visible_results]
        placeholders = ",".join("?" for _ in outfit_ids)
        with database() as connection:
            garment_rows = connection.execute("SELECT * FROM garments").fetchall()
            outfit_rows = connection.execute(
                f"SELECT * FROM outfits WHERE id IN ({placeholders})", outfit_ids
            ).fetchall()
        item_lookup = {row["id"]: _full_item(row) for row in garment_rows}
        outfit_lookup = {row["id"]: row for row in outfit_rows}
        for result in sorted(visible_results, key=lambda row: row["rank"]):
            row = outfit_lookup.get(result["outfit_id"])
            if row is None:
                continue
            outfit = _outfit_response(row, item_lookup)
            score = dict(result)
            score.pop("outfit_id", None)
            score.pop("panel_label", None)
            outfit.update(score)
            outfit.pop("board_path", None)
            outfits.append(outfit)
    response["outfits"] = outfits
    response["protocol_version"] = PROTOCOL_VERSION
    response["evaluation"] = None
    if response["status"] == "complete":
        response["evaluation"] = {
            "id": response["id"],
            "audience": response["audience"],
            "impression": response["impression"],
            "model": response["model"],
            "panel_size": MAX_PANEL_SIZE,
            "api_calls": response["completed_calls"],
            "cache_hit": False,
            "created_at": response["created_at"],
            "outfits": outfits,
        }
    return response


def _launch_tournament(tournament_id: str) -> None:
    existing = tournament_tasks.get(tournament_id)
    if existing and not existing.done():
        return
    task = asyncio.create_task(
        run_tournament(tournament_id), name=f"tournament-{tournament_id}"
    )
    tournament_tasks[tournament_id] = task

    def cleanup(completed: asyncio.Task[None]) -> None:
        tournament_tasks.pop(tournament_id, None)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(cleanup)


async def _read_upload(
    file: UploadFile, *, max_bytes: int | None = None
) -> bytes:
    allowed_mimes = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "image/heic-sequence",
        "image/heif-sequence",
        "application/octet-stream",
    }
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    suffix = Path(file.filename or "").suffix.lower()
    if file.content_type not in allowed_mimes and suffix not in allowed_suffixes:
        raise HTTPException(415, "Use a JPG, PNG, WebP, HEIC, or HEIF image.")
    limit = max_bytes or settings.MAX_UPLOAD_BYTES
    payload = await file.read(limit + 1)
    if not payload:
        raise HTTPException(400, "The uploaded image is empty.")
    if len(payload) > limit:
        raise HTTPException(413, f"Images must be under {limit // (1024 * 1024)} MB.")
    return payload


def _create_garment(payload: bytes, name: str, category: str) -> dict:
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, "Choose a supported garment category.")
    item_id = str(uuid.uuid4())
    destination = settings.WARDROBE_DIR / f"{item_id}.jpg"
    try:
        digest, color = decode_and_normalize(payload, destination)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    color_name, color_hex = color.split("|", 1)
    fallback_name = f"{color_name.title()} {category.replace('_', ' ')}"
    clean_name = (name.strip() or fallback_name)[:80]
    now = utc_now()
    try:
        with database() as connection:
            connection.execute(
                """
                INSERT INTO garments
                    (id, name, category, image_path, image_sha256, dominant_color,
                     color_hex, notes, available, last_worn, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, '', 1, NULL, ?, ?)
                """,
                (
                    item_id,
                    clean_name,
                    category,
                    str(destination),
                    digest,
                    color_name,
                    color_hex,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM garments WHERE id = ?", (item_id,)
            ).fetchone()
    except Exception as exc:
        destination.unlink(missing_ok=True)
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(
                409, "That exact garment image is already in the checklist."
            ) from exc
        raise
    return _row_to_item(row)


def _create_reference(payload: bytes) -> dict:
    reference_id = str(uuid.uuid4())
    original = settings.REFERENCE_DIR / f"{reference_id}.jpg"
    privacy = settings.PRIVACY_DIR / f"{reference_id}.jpg"
    try:
        decode_and_normalize(payload, original)
        method, faces = make_privacy_copy(original, privacy)
    except ValueError as exc:
        original.unlink(missing_ok=True)
        privacy.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    now = utc_now()
    with database() as connection:
        connection.execute("UPDATE reference_photos SET active = 0 WHERE active = 1")
        connection.execute(
            """
            INSERT INTO reference_photos
                (id, image_path, privacy_path, privacy_method, faces_detected, active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (reference_id, str(original), str(privacy), method, faces, now),
        )
        row = connection.execute(
            "SELECT * FROM reference_photos WHERE id = ?", (reference_id,)
        ).fetchone()
    return _reference_response(row)


def _require_local_request(request: Request) -> None:
    client_host = request.client.host if request.client else None
    if not is_loopback_client(client_host):
        raise HTTPException(403, "Start and stop mobile intake from the PC.")


def _access_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Mobile authorization is required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Mobile authorization is required.")
    return token


def _validate_mobile_access(authorization: str | None) -> tuple[str, object]:
    token = _access_token(authorization)
    try:
        session = mobile_sessions.validate_access(token)
    except MobileSessionExpired as exc:
        raise HTTPException(410, str(exc)) from exc
    except MobileAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    return token, session


def _mobile_session_response(info: dict) -> dict:
    result = dict(info)
    qr_path = result.pop("qr_path", None)
    if qr_path:
        result["qr_media_url"] = _media_url(qr_path)
    return result


@app.get("/api/health")
def health() -> dict:
    tryon_ready = settings.CATVTON_READY_MARKER.exists() and settings.CATVTON_PYTHON.exists()
    tryon_verified = settings.CATVTON_WEIGHTS_MARKER.exists()
    mobile_session = mobile_sessions.current(include_secret=False)
    try:
        mobile_lan_ip = discover_private_ipv4()
    except MobileSessionError:
        mobile_lan_ip = None
    return {
        "status": "ok",
        "openai_configured": bool(settings.OPENAI_API_KEY),
        "openai_model": settings.OPENAI_MODEL,
        "storage": str(settings.DATA_DIR),
        "focus_group": {
            "default_panel_size": settings.PANEL_SIZE,
            "max_panel_size": MAX_PANEL_SIZE,
            "tournament_budget_usd": settings.TOURNAMENT_BUDGET_USD,
            "tournament_protocol": PROTOCOL_VERSION,
        },
        "mobile": {
            "lan_ip": mobile_lan_ip,
            "available": mobile_lan_ip is not None,
            "session_active": bool(mobile_session.get("active")),
            "session_uploads": mobile_session.get("uploads", 0),
        },
        "tryon": {
            "ready": tryon_ready,
            "verified": tryon_verified,
            "label": (
                "Quality-tested; weights cached for offline use"
                if tryon_verified
                else "Runtime ready; weights load on first use"
                if tryon_ready
                else "Run Setup_CatVTON.cmd"
            ),
        },
    }


@app.get("/api/tournaments/current")
def current_tournament() -> dict | None:
    return _tournament_response(tournament_snapshot())


@app.get("/api/tournaments/{tournament_id}")
def get_tournament(tournament_id: str) -> dict:
    result = _tournament_response(tournament_snapshot(tournament_id))
    if result is None:
        raise HTTPException(404, "Tournament not found.")
    return result


@app.post("/api/tournaments", status_code=202)
async def create_tournament(request: TournamentRequest) -> dict:
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            409,
            "OpenAI is not configured. Add OPENAI_API_KEY to the project's .env file and restart the local service.",
        )
    try:
        tournament_id = await asyncio.to_thread(
            prepare_tournament,
            audience=request.audience.strip(),
            impression=request.impression.strip(),
            model=settings.OPENAI_MODEL,
            budget_usd=(
                0.0
                if request.mode == FULL_PANEL_MODE
                else settings.TOURNAMENT_BUDGET_USD
            ),
            mode=request.mode,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    _launch_tournament(tournament_id)
    result = _tournament_response(tournament_snapshot(tournament_id))
    if result is None:
        raise HTTPException(500, "The tournament was prepared but could not be loaded.")
    return result


@app.post("/api/tournaments/{tournament_id}/resume", status_code=202)
async def resume_tournament(tournament_id: str) -> dict:
    if not settings.OPENAI_API_KEY:
        raise HTTPException(409, "OpenAI is not configured.")
    snapshot = tournament_snapshot(tournament_id)
    if snapshot is None:
        raise HTTPException(404, "Tournament not found.")
    if snapshot["status"] not in {"paused", "ready"}:
        raise HTTPException(
            409,
            f"A {snapshot['status']} tournament cannot be resumed.",
        )
    _launch_tournament(tournament_id)
    await asyncio.sleep(0)
    result = _tournament_response(tournament_snapshot(tournament_id))
    if result is None:
        raise HTTPException(404, "Tournament not found.")
    return result


@app.get("/api/mobile/session/current")
def current_mobile_session(request: Request) -> dict:
    _require_local_request(request)
    return _mobile_session_response(mobile_sessions.current(include_secret=True))


@app.post("/api/mobile/session")
def start_mobile_session(request: Request) -> dict:
    _require_local_request(request)
    try:
        info = mobile_sessions.create()
    except MobileSessionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _mobile_session_response(info)


@app.delete("/api/mobile/session")
def stop_mobile_session(request: Request) -> dict:
    _require_local_request(request)
    mobile_sessions.stop()
    return {"active": False}


@app.post("/api/mobile/auth")
def authenticate_mobile(request: MobileAuthRequest) -> dict:
    try:
        access_token, info = mobile_sessions.authenticate(
            request.join_token, request.pin
        )
    except MobileSessionExpired as exc:
        raise HTTPException(410, str(exc)) from exc
    except MobileAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"access_token": access_token, "session": info}


@app.get("/api/mobile/status")
def mobile_status(authorization: str | None = Header(default=None)) -> dict:
    _validate_mobile_access(authorization)
    return mobile_sessions.current(include_secret=False)


@app.post("/api/mobile/items")
async def mobile_create_item(
    file: UploadFile = File(...),
    name: str = Form(""),
    category: str = Form("top"),
    authorization: str | None = Header(default=None),
) -> dict:
    token, _ = _validate_mobile_access(authorization)
    payload = await _read_upload(file, max_bytes=settings.MOBILE_MAX_UPLOAD_BYTES)
    item = _create_garment(payload, name, category)
    session = mobile_sessions.record_upload(token)
    return {"item": item, "session": session}


@app.post("/api/mobile/reference")
async def mobile_create_reference(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict:
    token, _ = _validate_mobile_access(authorization)
    payload = await _read_upload(file, max_bytes=settings.MOBILE_MAX_UPLOAD_BYTES)
    reference = _create_reference(payload)
    session = mobile_sessions.record_upload(token, reference=True)
    return {"reference": reference, "session": session}


@app.post("/api/mobile/finish")
def finish_mobile_session(
    authorization: str | None = Header(default=None),
) -> dict:
    token, _ = _validate_mobile_access(authorization)
    mobile_sessions.finish(token)
    return {"active": False}


@app.get("/api/items")
def list_items() -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM garments ORDER BY available DESC, category, created_at DESC"
        ).fetchall()
    return [_row_to_item(row) for row in rows]


@app.post("/api/items")
async def create_item(
    file: UploadFile = File(...),
    name: str = Form(""),
    category: str = Form("top"),
) -> dict:
    payload = await _read_upload(file)
    return _create_garment(payload, name, category)


@app.patch("/api/items/{item_id}")
def update_item(item_id: str, patch: ItemPatch) -> dict:
    changes = patch.model_dump(exclude_unset=True)
    if "last_worn" in changes and changes["last_worn"]:
        try:
            date.fromisoformat(changes["last_worn"])
        except ValueError as exc:
            raise HTTPException(400, "last_worn must be an ISO date.") from exc
    if not changes:
        raise HTTPException(400, "No changes were supplied.")
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{column} = ?" for column in changes)
    values = [int(value) if column == "available" else value for column, value in changes.items()]
    with database() as connection:
        cursor = connection.execute(
            f"UPDATE garments SET {assignments} WHERE id = ?", [*values, item_id]
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Garment not found.")
        row = connection.execute("SELECT * FROM garments WHERE id = ?", (item_id,)).fetchone()
    return _row_to_item(row)


@app.post("/api/reference")
async def create_reference(file: UploadFile = File(...)) -> dict:
    payload = await _read_upload(file)
    return _create_reference(payload)


@app.get("/api/reference/current")
def current_reference() -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM reference_photos WHERE active = 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return _reference_response(row) if row else None


@app.post("/api/outfits/generate")
def create_outfits(request: GenerateRequest) -> dict:
    with database() as connection:
        garment_rows = connection.execute(
            "SELECT * FROM garments WHERE available = 1 ORDER BY category, created_at"
        ).fetchall()
    items = [_full_item(row) for row in garment_rows]
    try:
        candidates, considered = generate_combinations(items, request.limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    responses: list[dict] = []
    item_lookup = {item["id"]: item for item in items}
    with database() as connection:
        for rank, candidate in enumerate(candidates, start=1):
            existing = connection.execute(
                "SELECT * FROM outfits WHERE signature = ?", (candidate["signature"],)
            ).fetchone()
            if existing:
                board_path = Path(existing["board_path"])
                if not board_path.exists():
                    board_path = render_outfit_board(existing["id"], candidate["items"], rank)
                    connection.execute(
                        "UPDATE outfits SET board_path = ?, local_score = ? WHERE id = ?",
                        (str(board_path), candidate["local_score"], existing["id"]),
                    )
                row = connection.execute("SELECT * FROM outfits WHERE id = ?", (existing["id"],)).fetchone()
            else:
                outfit_id = str(uuid.uuid4())
                board_path = render_outfit_board(outfit_id, candidate["items"], rank)
                record = outfit_record(candidate, outfit_id, board_path)
                connection.execute(
                    """
                    INSERT INTO outfits (id, signature, item_ids_json, local_score, board_path, created_at)
                    VALUES (:id, :signature, :item_ids_json, :local_score, :board_path, :created_at)
                    """,
                    record,
                )
                row = connection.execute("SELECT * FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
            responses.append(_outfit_response(row, item_lookup))
    return {"outfits": responses, "combinations_considered": considered}


@app.post("/api/tryon")
async def create_tryons(request: TryOnRequest) -> dict:
    if not settings.CATVTON_READY_MARKER.exists() or not settings.CATVTON_PYTHON.exists():
        raise HTTPException(409, "CatVTON is not installed. Run Setup_CatVTON.cmd first.")
    with database() as connection:
        reference = connection.execute(
            "SELECT * FROM reference_photos WHERE active = 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        garment_rows = connection.execute("SELECT * FROM garments").fetchall()
        placeholders = ",".join("?" for _ in request.outfit_ids)
        outfit_rows = connection.execute(
            f"SELECT * FROM outfits WHERE id IN ({placeholders})", request.outfit_ids
        ).fetchall()
    if not reference:
        raise HTTPException(409, "Add a full-body reference photo before generating try-ons.")
    by_id = {row["id"]: row for row in outfit_rows}
    if len(by_id) != len(set(request.outfit_ids)):
        raise HTTPException(404, "One or more selected outfits no longer exist.")
    item_lookup = {row["id"]: _full_item(row) for row in garment_rows}

    jobs: list[dict] = []
    cached_ids: set[str] = set()
    for outfit_id in request.outfit_ids:
        row = by_id[outfit_id]
        current_path = row["tryon_path"]
        if (
            not request.force
            and row["tryon_status"] == "complete"
            and row["tryon_reference_id"] == reference["id"]
            and current_path
            and Path(current_path).exists()
        ):
            cached_ids.add(outfit_id)
            continue
        items = [
            item_lookup[item_id]
            for item_id in json.loads(row["item_ids_json"])
            if item_id in item_lookup
            and item_lookup[item_id]["category"] in SUPPORTED_CATEGORIES
        ]
        output = settings.TRYON_DIR / f"{outfit_id}-{reference['id'][:8]}.jpg"
        jobs.append(
            {
                "id": outfit_id,
                "person": reference["image_path"],
                "garments": items,
                "output": str(output),
            }
        )

    if jobs:
        with database() as connection:
            for job in jobs:
                connection.execute(
                    "UPDATE outfits SET tryon_status = 'running', tryon_error = NULL WHERE id = ?",
                    (job["id"],),
                )
        manifest = settings.CACHE_DIR / f"tryon-batch-{uuid.uuid4().hex}.json"
        try:
            payload = await asyncio.to_thread(run_tryon_batch, jobs, manifest)
            result_lookup = {result["id"]: result for result in payload["results"]}
            with database() as connection:
                for job in jobs:
                    result = result_lookup.get(job["id"])
                    if not result:
                        raise RuntimeError(f"CatVTON returned no result for outfit {job['id']}.")
                    output = Path(result["output"]).resolve()
                    privacy = settings.PRIVACY_DIR / f"tryon-{job['id']}-{reference['id'][:8]}.jpg"
                    make_privacy_copy(output, privacy)
                    connection.execute(
                        """
                        UPDATE outfits
                        SET tryon_path = ?, tryon_privacy_path = ?, tryon_reference_id = ?,
                            tryon_status = 'complete', tryon_error = NULL
                        WHERE id = ?
                        """,
                        (str(output), str(privacy), reference["id"], job["id"]),
                    )
        except Exception as exc:
            detail = str(exc)[-3000:]
            with database() as connection:
                for job in jobs:
                    connection.execute(
                        "UPDATE outfits SET tryon_status = 'failed', tryon_error = ? WHERE id = ?",
                        (detail, job["id"]),
                    )
            raise HTTPException(502, f"Local try-on failed: {detail}") from exc
        finally:
            manifest.unlink(missing_ok=True)

    with database() as connection:
        refreshed = connection.execute(
            f"SELECT * FROM outfits WHERE id IN ({','.join('?' for _ in request.outfit_ids)})",
            request.outfit_ids,
        ).fetchall()
    refreshed_by_id = {row["id"]: row for row in refreshed}
    outfits = [
        _outfit_response(refreshed_by_id[outfit_id], item_lookup)
        for outfit_id in request.outfit_ids
    ]
    return {
        "outfits": outfits,
        "generated": len(jobs),
        "cache_hits": len(cached_ids),
        "privacy": "Original reference and try-ons stayed local; blurred API-safe copies were created.",
    }


@app.post("/api/evaluations")
async def create_evaluation(request: EvaluationRequest) -> dict:
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            409,
            "OpenAI is not configured. Add OPENAI_API_KEY to the project's .env file and restart the local service.",
        )

    with database() as connection:
        garment_rows = connection.execute("SELECT * FROM garments").fetchall()
        placeholders = ",".join("?" for _ in request.outfit_ids)
        outfit_rows = connection.execute(
            f"SELECT * FROM outfits WHERE id IN ({placeholders})", request.outfit_ids
        ).fetchall()
    by_id = {row["id"]: row for row in outfit_rows}
    if len(by_id) != len(set(request.outfit_ids)):
        raise HTTPException(404, "One or more shortlisted outfits no longer exist.")
    item_lookup = {row["id"]: _full_item(row) for row in garment_rows}
    outfits = [_outfit_response(by_id[outfit_id], item_lookup) for outfit_id in request.outfit_ids]

    sheet_seed = "|".join(outfit["id"] for outfit in outfits)
    contact_sheet = render_contact_sheet(outfits, sheet_seed)
    cache_material = json.dumps(
        {
            "sheet_sha256": hashlib.sha256(contact_sheet.read_bytes()).hexdigest(),
            "audience": request.audience.strip(),
            "impression": request.impression.strip(),
            "model": settings.OPENAI_MODEL,
            "panel_size": request.panel_size,
            "prompt_version": PROMPT_VERSION,
        },
        sort_keys=True,
    )
    cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
    with database() as connection:
        cached = connection.execute(
            "SELECT * FROM evaluations WHERE cache_key = ?", (cache_key,)
        ).fetchone()
    if cached:
        result = json.loads(cached["result_json"])
        result["api_calls"] = 0
        result["cache_hit"] = True
        return _refresh_result_runtime(result)

    labels = [f"OUTFIT {index}" for index in range(1, len(outfits) + 1)]
    try:
        ranking, usage_rows = await run_panel(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
            audience=request.audience.strip(),
            impression=request.impression.strip(),
            contact_sheet=contact_sheet,
            labels=labels,
            panel_size=request.panel_size,
        )
    except Exception as exc:
        raise HTTPException(502, f"OpenAI panel evaluation failed: {exc}") from exc

    ranking_by_label = {row["panel_label"]: row for row in ranking}
    enriched = []
    for index, outfit in enumerate(outfits, start=1):
        score = ranking_by_label[f"OUTFIT {index}"]
        enriched.append({**outfit, **score})
    enriched.sort(key=lambda outfit: (-outfit["overall"], outfit["id"]))
    for rank, outfit in enumerate(enriched, start=1):
        outfit["rank"] = rank
        outfit.pop("board_path", None)
        outfit.pop("panel_label", None)

    evaluation_id = str(uuid.uuid4())
    created_at = utc_now()
    result = {
        "id": evaluation_id,
        "audience": request.audience.strip(),
        "impression": request.impression.strip(),
        "model": settings.OPENAI_MODEL,
        "panel_size": request.panel_size,
        "api_calls": len(usage_rows),
        "cache_hit": False,
        "created_at": created_at,
        "outfits": enriched[:5],
    }
    with database() as connection:
        connection.execute(
            """
            INSERT INTO evaluations
                (id, cache_key, audience, impression, model, panel_size, result_json, api_calls, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                cache_key,
                result["audience"],
                result["impression"],
                settings.OPENAI_MODEL,
                request.panel_size,
                json.dumps(result),
                len(usage_rows),
                created_at,
            ),
        )
        for usage in usage_rows:
            connection.execute(
                """
                INSERT INTO api_usage
                    (evaluation_id, persona_key, model, input_tokens, output_tokens, cached_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    usage["persona_key"],
                    settings.OPENAI_MODEL,
                    usage["input_tokens"],
                    usage["output_tokens"],
                    usage["cached_tokens"],
                    created_at,
                ),
            )
    return _refresh_result_runtime(result)


@app.get("/api/evaluations/latest")
def latest_evaluation() -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT result_json FROM evaluations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return _refresh_result_runtime(json.loads(row["result_json"])) if row else None
