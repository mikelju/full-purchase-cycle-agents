"""Deterministic channel router: the item type picks the graph, with no model call.

A `.eml` file goes to the email graph, a JSON file with `submission_id` to the web form graph and a JSON file
with `message_id` and `from` to the WhatsApp graph; any other item is rejected with a reason.
A WhatsApp text from a known customer whose most recent pending clarification is a WhatsApp thread is that
thread's answer; any other WhatsApp message starts a new order, and the WhatsApp graph rejects what it cannot read.
"""

import json
import sqlite3
from pathlib import Path
from typing import NamedTuple

from langgraph.types import Command

from purchase_cycle import db
from purchase_cycle.whatsapp_order import WhatsAppRejected, read_whatsapp

WEB_FORM = "web_form"
EMAIL = "email"
WHATSAPP_NEW = "whatsapp_new"
WHATSAPP_ANSWER = "whatsapp_answer"
REJECTED = "rejected"
CHANNELS = {WEB_FORM: "web_form", EMAIL: "email", WHATSAPP_NEW: "whatsapp", WHATSAPP_ANSWER: "whatsapp"}


class Route(NamedTuple):
    kind: str
    thread_id: str | None = None  # the paused thread a WhatsApp answer resumes
    reason: str | None = None  # why the item was rejected


def _whatsapp_route(path: Path, conn: sqlite3.Connection) -> Route:
    try:
        message = read_whatsapp(path, conn)
    except WhatsAppRejected:
        return Route(WHATSAPP_NEW)
    pending = db.latest_pending_clarification(conn, message["customer"]["code"])
    if pending is not None and pending["channel"] == "whatsapp":
        return Route(WHATSAPP_ANSWER, pending["thread_id"])
    return Route(WHATSAPP_NEW)


def route(item: Path | str, conn: sqlite3.Connection) -> Route:
    """The route of one inbox item, read from the item and the `clarifications` table only."""
    path = Path(item)
    suffix = path.suffix.lower()
    if suffix == ".eml":
        return Route(EMAIL)
    if suffix != ".json":
        return Route(REJECTED, reason=f"the file type '{path.suffix or path.name}' fits no channel")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as error:
        return Route(REJECTED, reason=f"the file '{path.name}' cannot be read: {error}")
    except json.JSONDecodeError as error:
        return Route(REJECTED, reason=f"the file '{path.name}' is not valid JSON ({error.msg})")
    if isinstance(data, dict) and "submission_id" in data:
        return Route(WEB_FORM)
    if isinstance(data, dict) and "message_id" in data and "from" in data:
        return _whatsapp_route(path, conn)
    return Route(REJECTED, reason=f"the file '{path.name}' is neither a web form submission nor a WhatsApp message")


def graph_input(path: Path, kind: str):
    """The input that starts or resumes the graph of a routed item."""
    if kind == WEB_FORM:
        return {"submission": json.loads(path.read_text(encoding="utf-8-sig"))}
    if kind == EMAIL:
        return {"email_path": str(path)}
    if kind == WHATSAPP_ANSWER:
        return Command(resume={"answer": json.loads(path.read_text(encoding="utf-8-sig"))["text"]["body"].strip()})
    return {"message_path": str(path)}


def run_inbox(folder: Path | str, graphs: dict, db_path: Path | str, run_id: str) -> list[dict]:
    """Route each file of the folder, in name order, and run it through the graph of its channel.

    `graphs` maps a channel (`web_form`, `email`, `whatsapp`) to its compiled graph. Each result holds the
    item name, its route, the thread id, and the final graph state or the error that stopped the item.
    A new thread id is `<channel>-<run_id>-<file stem>`; a WhatsApp answer resumes the paused thread.
    """
    results = []
    for path in sorted(p for p in Path(folder).iterdir() if p.is_file()):
        conn = db.connect(db_path)
        try:
            routed = route(path, conn)
        finally:
            conn.close()
        result = {"item": path.name, "route": routed, "thread_id": None, "state": None, "error": None}
        results.append(result)
        if routed.kind == REJECTED:
            continue
        channel = CHANNELS[routed.kind]
        result["thread_id"] = routed.thread_id or f"{channel}-{run_id}-{path.stem}"
        run_config = {"configurable": {"thread_id": result["thread_id"]}, "run_name": f"{channel}_order"}
        try:
            result["state"] = graphs[channel].invoke(graph_input(path, routed.kind), run_config)
        except Exception as error:  # a failure stops this item only
            result["error"] = error
    return results
