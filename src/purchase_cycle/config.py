"""Single configuration point for paths, model and run mode."""

import os
from pathlib import Path

MODEL_ID = "claude-haiku-4-5"
MAX_TOKENS = 256

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
EVALS_DIR = ROOT / "evals"
RECORDINGS_PATH = EVALS_DIR / "recordings" / "order_line_extraction.jsonl"
MATCHING_RECORDINGS_PATH = EVALS_DIR / "recordings" / "web_form_matching.jsonl"
EMAIL_INTAKE_RECORDINGS_PATH = EVALS_DIR / "recordings" / "email_intake.jsonl"
EMAIL_EXTRACTION_RECORDINGS_PATH = EVALS_DIR / "recordings" / "email_order_extraction.jsonl"
EMAIL_EXTRACTION_MAX_TOKENS = 4096  # one answer lists every line of an email
CLARIFICATION_QUESTION_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_question.jsonl"
CLARIFICATION_ANSWER_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_answers.jsonl"
# The detection evaluation runs the channel steps on its own orders; their answers never go into the phase 02 and 03 files.
CLARIFICATION_DETECTION_MATCHING_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_detection_matching.jsonl"
CLARIFICATION_DETECTION_INTAKE_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_detection_email_intake.jsonl"
CLARIFICATION_DETECTION_EXTRACTION_RECORDINGS_PATH = (
    EVALS_DIR / "recordings" / "clarification_detection_email_extraction.jsonl"
)
CLARIFICATION_MAX_TOKENS = 1024  # a question or a resolution list covers every doubtful line of an order

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
