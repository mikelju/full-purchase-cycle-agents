"""Single configuration point for paths, model and run mode."""

import os
from pathlib import Path

MODEL_ID = "claude-haiku-4-5"
MAX_TOKENS = 256

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
EVALS_DIR = ROOT / "evals"
RECORDINGS_PATH = EVALS_DIR / "recordings" / "order_line_extraction.jsonl"

MODES = ("live", "record", "replay")


def default_db_path() -> Path:
    return Path(os.environ.get("PURCHASE_CYCLE_DB") or DATA_DIR / "purchase_cycle.db")


def default_checkpoint_path() -> Path:
    return Path(os.environ.get("PURCHASE_CYCLE_CHECKPOINTS") or DATA_DIR / "checkpoints.db")


def configure_tracing(mode: str) -> None:
    """Tracing is only allowed when the real model is called.

    Replay runs, including the ones inside `npm run check`, never send traces
    even when the owner's `.env` enables them.
    """
    if mode == "replay":
        from langsmith.utils import get_env_var

        # LangSmith reads TRACING_V2 before TRACING and caches what it reads.
        for name in ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
            os.environ[name] = "false"
        get_env_var.cache_clear()
