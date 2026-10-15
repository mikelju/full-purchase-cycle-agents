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
WHATSAPP_INTAKE_RECORDINGS_PATH = EVALS_DIR / "recordings" / "whatsapp_intake.jsonl"
WHATSAPP_EXTRACTION_RECORDINGS_PATH = EVALS_DIR / "recordings" / "whatsapp_order_extraction.jsonl"
CLARIFICATION_QUESTION_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_question.jsonl"
CLARIFICATION_ANSWER_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_answers.jsonl"
# The detection evaluation runs the channel steps on its own orders; their answers never go into the phase 02 and 03 files.
CLARIFICATION_DETECTION_MATCHING_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_detection_matching.jsonl"
CLARIFICATION_DETECTION_INTAKE_RECORDINGS_PATH = EVALS_DIR / "recordings" / "clarification_detection_email_intake.jsonl"
CLARIFICATION_DETECTION_EXTRACTION_RECORDINGS_PATH = (
    EVALS_DIR / "recordings" / "clarification_detection_email_extraction.jsonl"
)
OFFER_EXTRACTION_RECORDINGS_PATH = EVALS_DIR / "recordings" / "offer_extraction.jsonl"
SUPPLIER_DOCUMENT_RECORDINGS_PATH = EVALS_DIR / "recordings" / "document_extraction.jsonl"
CLAIM_DRAFTING_RECORDINGS_PATH = EVALS_DIR / "recordings" / "claim_drafting.jsonl"
CLAIM_DRAFTING_MAX_TOKENS = 1024  # one claim body per call
SUPPLIER_DOCUMENT_MAX_TOKENS = 4096  # one answer lists every line of an invoice or delivery note
OFFER_EXTRACTION_MAX_TOKENS = 512  # one offer per supplier reply
CLARIFICATION_MAX_TOKENS = 1024  # a question or a resolution list covers every doubtful line of an order

MODES = ("live", "record", "replay")
OUTBOX_DIR = DATA_DIR / "outbox"  # simulated WhatsApp replies
SUPPLIER_OUTBOX_DIR = DATA_DIR / "supplier_outbox"  # simulated quote request and reminder emails
CUSTOMER_DELIVERY_NOTES_DIR = DATA_DIR / "customer_delivery_notes"  # customer delivery note text files


def default_db_path() -> Path:
    return Path(os.environ.get("PURCHASE_CYCLE_DB") or DATA_DIR / "purchase_cycle.db")


def default_checkpoint_path() -> Path:
    return Path(os.environ.get("PURCHASE_CYCLE_CHECKPOINTS") or DATA_DIR / "checkpoints.db")


def configure_tracing(mode: str) -> None:
    """LangSmith tracing is only allowed when the real model is called.

    Replay runs, including the ones inside `npm run check`, never send traces
    even when the owner's `.env` enables them.
    Local Phoenix tracing has its own opt-in, checked last.
    """
    if mode == "replay":
        from langsmith.utils import get_env_var

        # LangSmith reads TRACING_V2 before TRACING and caches what it reads.
        for name in ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
            os.environ[name] = "false"
        get_env_var.cache_clear()
    configure_phoenix_tracing()


PHOENIX_ENDPOINT = "http://127.0.0.1:6006/v1/traces"  # the server started by `npm run phoenix`
PHOENIX_PROJECT = "purchase-cycle"
_phoenix_provider = None


def phoenix_tracing_allowed() -> bool:
    """Local Phoenix tracing is opt-in and never runs inside pytest or `npm run check`."""
    if os.environ.get("PHOENIX_TRACING", "").strip().lower() != "true":
        return False
    if "PYTEST_VERSION" in os.environ:  # pytest sets it for the whole session
        return False
    # npm names the running script; `npm run check` runs its Python steps and the replay eval through these.
    return os.environ.get("npm_lifecycle_event", "") not in ("check", "check:puerta", "check:python", "eval")


def configure_phoenix_tracing():
    """Send LangChain and LangGraph spans to the local Phoenix when `PHOENIX_TRACING=true`.

    The imports stay inside the function so nothing loads without the variable.
    """
    global _phoenix_provider
    if _phoenix_provider is not None or not phoenix_tracing_allowed():
        return _phoenix_provider
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import register

    # Spans are exported one by one so a short CLI run loses none at exit.
    _phoenix_provider = register(project_name=PHOENIX_PROJECT, endpoint=PHOENIX_ENDPOINT, batch=False, verbose=False)
    LangChainInstrumentor().instrument(tracer_provider=_phoenix_provider)
    return _phoenix_provider
