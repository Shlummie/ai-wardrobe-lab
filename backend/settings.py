from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("AIWL_DATA_DIR", BASE_DIR / "data")).resolve()
DATABASE_PATH = DATA_DIR / "ai-wardrobe-lab.db"
WARDROBE_DIR = DATA_DIR / "wardrobe"
REFERENCE_DIR = DATA_DIR / "reference"
PRIVACY_DIR = DATA_DIR / "privacy"
OUTFIT_DIR = DATA_DIR / "outfits"
CACHE_DIR = DATA_DIR / "cache"
TOURNAMENT_DIR = CACHE_DIR / "tournaments"
TRYON_DIR = DATA_DIR / "tryon"
MODEL_DIR = BASE_DIR / "models"
CATVTON_DIR = MODEL_DIR / "CatVTON"
CATVTON_READY_MARKER = MODEL_DIR / ".catvton-ready.json"
CATVTON_WEIGHTS_MARKER = MODEL_DIR / ".catvton-weights-ready.json"
CATVTON_CACHE_DIR = MODEL_DIR / "cache" / "catvton"
CATVTON_PYTHON = BASE_DIR / ".venv-catvton" / "Scripts" / "python.exe"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip()
PANEL_SIZE = max(1, min(6, int(os.getenv("AIWL_PANEL_SIZE", "6"))))
TOURNAMENT_BUDGET_USD = max(
    0.25, min(25.0, float(os.getenv("AIWL_TOURNAMENT_BUDGET_USD", "3.00")))
)
TOURNAMENT_SCREEN_CONCURRENCY = max(
    1, min(4, int(os.getenv("AIWL_TOURNAMENT_SCREEN_CONCURRENCY", "3")))
)
MAX_UPLOAD_BYTES = max(1, int(os.getenv("AIWL_MAX_UPLOAD_MB", "12"))) * 1024 * 1024
MOBILE_MAX_UPLOAD_BYTES = max(
    MAX_UPLOAD_BYTES,
    max(1, int(os.getenv("AIWL_MOBILE_MAX_UPLOAD_MB", "30"))) * 1024 * 1024,
)
MOBILE_IDLE_SECONDS = max(
    300, min(3600, int(os.getenv("AIWL_MOBILE_IDLE_MINUTES", "15")) * 60)
)
MOBILE_MAX_SECONDS = max(
    MOBILE_IDLE_SECONDS,
    min(4 * 3600, int(os.getenv("AIWL_MOBILE_MAX_MINUTES", "90")) * 60),
)
MOBILE_WEB_PORT = max(1024, min(65535, int(os.getenv("AIWL_MOBILE_WEB_PORT", "3000"))))


def ensure_data_dirs() -> None:
    for directory in (
        DATA_DIR,
        WARDROBE_DIR,
        REFERENCE_DIR,
        PRIVACY_DIR,
        OUTFIT_DIR,
        CACHE_DIR,
        TOURNAMENT_DIR,
        TRYON_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
