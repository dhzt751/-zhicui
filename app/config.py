import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR") or str(ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
WORK_DIR = DATA_DIR / "work"
DB_PATH = DATA_DIR / "app.db"

for _d in (DATA_DIR, UPLOAD_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_EMBEDDING_MODEL = os.getenv("LLM_EMBEDDING_MODEL", "")
VISION_MODEL = os.getenv("VISION_MODEL", "")

ASR_BASE_URL = (os.getenv("ASR_BASE_URL") or LLM_BASE_URL).rstrip("/")
ASR_API_KEY = os.getenv("ASR_API_KEY") or LLM_API_KEY
ASR_MODEL = os.getenv("ASR_MODEL") or "whisper-1"

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
BILIBILI_COOKIE_FILE = os.getenv("BILIBILI_COOKIE_FILE", "")

MAX_AUDIO_BYTES = 24 * 1024 * 1024
MAX_VIDEO_UPLOAD = 2 * 1024 * 1024 * 1024
MAX_DOCUMENT_UPLOAD = 60 * 1024 * 1024


def _real_key(value: str) -> bool:
    key = (value or "").strip()
    return bool(key) and "your-key" not in key.lower() and key.lower() not in {"sk-xxx", "sk-xxxx"}


def llm_ready() -> bool:
    return bool(LLM_BASE_URL and LLM_MODEL) and _real_key(LLM_API_KEY)


def asr_ready() -> bool:
    return bool(ASR_BASE_URL and ASR_MODEL) and _real_key(ASR_API_KEY)


def proxy_map() -> dict:
    if not PROXY:
        return {}
    return {"http": PROXY, "https": PROXY}