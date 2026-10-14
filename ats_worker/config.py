"""Settings, read from the environment (and a .env file next to the project)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name, default):
    return int(os.environ.get(name, default))


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    portal_url: str
    worker_token: str
    drive_credentials: Path
    llm_backend: str        # "ollama" or "openai" (any OpenAI-compatible server: vLLM, llama.cpp, LM Studio)
    llm_url: str
    llm_model: str
    llm_api_key: str
    llm_timeout: int
    llm_context: int
    poll_seconds: int
    max_pages: int
    max_chars: int
    ocr: bool
    log_level: str


def load():
    backend = os.environ.get("LLM_BACKEND", "ollama").strip().lower()
    if backend not in {"ollama", "openai"}:
        raise SystemExit("LLM_BACKEND must be 'ollama' or 'openai'.")
    default_url = "http://localhost:11434" if backend == "ollama" else "http://localhost:8000/v1"
    creds = Path(os.environ.get("DRIVE_CREDENTIALS", "token.json"))
    return Config(
        portal_url=os.environ.get("PORTAL_URL", "https://learn.parottasalna.com").rstrip("/"),
        worker_token=os.environ.get("ATS_WORKER_TOKEN", "").strip(),
        drive_credentials=creds if creds.is_absolute() else ROOT / creds,
        llm_backend=backend,
        llm_url=os.environ.get("LLM_URL", default_url).rstrip("/"),
        llm_model=os.environ.get("LLM_MODEL", "qwen2.5:14b"),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_timeout=_int("LLM_TIMEOUT", 600),
        llm_context=_int("LLM_CONTEXT", 16384),
        poll_seconds=_int("POLL_SECONDS", 30),
        max_pages=_int("MAX_PAGES", 6),
        max_chars=_int("MAX_CHARS", 20000),
        ocr=_bool("OCR", False),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
