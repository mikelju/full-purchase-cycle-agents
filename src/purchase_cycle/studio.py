"""LangGraph Studio entry points (change 004): the three channel graphs for `langgraph dev`.

Each factory compiles its channel graph with the clarify step and recovery on, as `purchase-cycle route` wires them,
but without a checkpointer, because the dev server supplies persistence.
The graphs run on their own database and outbox under `data/studio/`, never on the business database.
Replay is the default, on the recorded answers of the sample inbox; `PURCHASE_CYCLE_STUDIO_MODE=live` calls the model.
LangSmith tracing is forced off in both modes.
"""

import json
import os

from dotenv import load_dotenv

from purchase_cycle import config, db
from purchase_cycle.clarification import ClarificationClients
from purchase_cycle.email_order import build_email_order_graph
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
    ModelClient,
)
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph

STUDIO_DIR = config.DATA_DIR / "studio"
DB_PATH = STUDIO_DIR / "purchase_cycle.db"
OUTBOX_DIR = STUDIO_DIR / "outbox"
RECORDINGS_PATH = config.EVALS_DIR / "recordings" / "orders_demo.jsonl"  # one file for every task
# Answers only Studio needs, such as the plain first email extraction the demo deliberately breaks; recorded once.
STUDIO_RECORDINGS_PATH = config.EVALS_DIR / "recordings" / "studio.jsonl"
STUDIO_MODES = ("replay", "live")


class StudioModelClient(ModelClient):
    """Replays from the demo recordings plus the Studio-only recordings file."""

    def _load(self) -> dict[str, dict]:
        records = super()._load()
        if STUDIO_RECORDINGS_PATH.exists():
            with STUDIO_RECORDINGS_PATH.open(encoding="utf-8") as fh:
                records.update({r["key"]: r for r in (json.loads(line) for line in fh if line.strip())})
        return records


def studio_mode() -> str:
    mode = os.environ.get("PURCHASE_CYCLE_STUDIO_MODE") or "replay"
    if mode not in STUDIO_MODES:
        raise ValueError(f"PURCHASE_CYCLE_STUDIO_MODE must be one of {', '.join(STUDIO_MODES)}, not {mode!r}")
    return mode


def build(channel: str, checkpointer=None):
    """The channel graph (`web_form`, `email` or `whatsapp`) on the Studio database, seeded on first use."""
    mode = studio_mode()
    if mode == "live":
        load_dotenv()  # the key lives in .env, which the dev server does not load
    config.configure_tracing("replay")  # forces LangSmith tracing off, also in live mode; Phoenix keeps its opt-in
    conn = db.connect(DB_PATH)
    try:
        # First use is a database without catalog, also one a failed first seed left behind.
        has_catalog = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'products'").fetchone()
        if not has_catalog or not db.catalog_rows(conn):
            db.seed(conn)
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()

    def client(task):
        return StudioModelClient(mode, catalog, RECORDINGS_PATH, task=task)

    clarification = ClarificationClients(client(CLARIFICATION_QUESTION), client(CLARIFICATION_ANSWER))
    if channel == "web_form":
        return build_web_form_graph(client(MATCHING), DB_PATH, checkpointer, clarification=clarification, recovery=True)
    if channel == "email":
        return build_email_order_graph(
            client(EMAIL_INTAKE), client(EMAIL_EXTRACTION), DB_PATH, checkpointer,
            clarification=clarification, recovery=True,
        )  # fmt: skip
    if channel == "whatsapp":
        return build_whatsapp_order_graph(
            client(WHATSAPP_INTAKE), client(WHATSAPP_EXTRACTION), DB_PATH, OUTBOX_DIR, checkpointer,
            clarification=clarification,
        )  # fmt: skip
    raise ValueError(f"unknown channel {channel!r}")


def web_form_graph():
    return build("web_form")


def email_graph():
    return build("email")


def whatsapp_graph():
    return build("whatsapp")
