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
    return Path(os.environ.get("PURCHASE_CYCLE_DB", DATA_DIR / "purchase_cycle.db"))


def default_checkpoint_path() -> Path:
    return Path(os.environ.get("PURCHASE_CYCLE_CHECKPOINTS", DATA_DIR / "checkpoints.db"))


def configure_tracing(mode: str) -> None:
    """Tracing is only allowed when the real model is called.

    Replay runs, including the ones inside `npm run check`, never send traces
    even when the owner's `.env` enables them.
    """
    if mode == "replay":
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
