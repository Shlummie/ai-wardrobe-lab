from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import settings


SUPPORTED_CATEGORIES = {"top", "bottom", "one_piece", "outerwear"}
GARMENT_ORDER = {"one_piece": 0, "bottom": 1, "top": 2, "outerwear": 3}


def run_tryon_batch(jobs: list[dict], manifest_path: Path) -> dict:
    """Run multiple CatVTON jobs in one process so the weights load only once."""
    if not settings.CATVTON_PYTHON.exists():
        raise RuntimeError("CatVTON runtime is not installed. Run Setup_CatVTON.cmd.")
    if not settings.CATVTON_DIR.exists():
        raise RuntimeError("CatVTON source is missing. Run Setup_CatVTON.cmd.")
    if not jobs:
        return {"results": [], "device": None, "offline_cache": False}

    normalized_jobs: list[dict] = []
    for job in jobs:
        garments = [
            garment
            for garment in job["garments"]
            if garment["category"] in SUPPORTED_CATEGORIES
        ]
        garments.sort(key=lambda garment: GARMENT_ORDER[garment["category"]])
        if not garments:
            raise RuntimeError(
                f"Outfit {job['id']} has no garment category supported by CatVTON."
            )
        normalized_jobs.append({**job, "garments": garments})

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"jobs": normalized_jobs}, indent=2), encoding="utf-8"
    )
    command = [
        str(settings.CATVTON_PYTHON),
        str(settings.BASE_DIR / "backend" / "adapters" / "catvton_worker.py"),
        "--manifest",
        str(manifest_path),
        "--repo",
        str(settings.CATVTON_DIR),
        "--cache",
        str(settings.CATVTON_CACHE_DIR),
        "--weights-marker",
        str(settings.CATVTON_WEIGHTS_MARKER),
        "--width",
        "768",
        "--height",
        "1024",
        "--steps",
        "35",
    ]
    work_dir = settings.CACHE_DIR / "catvton-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        cwd=work_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Unknown CatVTON error").strip()
        raise RuntimeError(detail[-3000:])
    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "results" in payload:
            return payload
    raise RuntimeError("CatVTON finished without returning a result manifest.")
