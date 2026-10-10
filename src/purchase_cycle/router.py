"""Deterministic channel router: the item type picks the graph, with no model call.

A `.eml` file goes to the email graph, a JSON file with `submission_id` to the web form graph and a JSON file
with `message_id` and `from` to the WhatsApp graph; any other item is rejected with a reason.
A WhatsApp text from a known customer whose most recent pending clarification is a WhatsApp thread is that
thread's answer; any other WhatsApp message starts a new order, and the WhatsApp graph rejects what it cannot read.
An email or WhatsApp message whose message id is the source of a pending thread of the same customer and channel
is a re-delivery of that paused order: a duplicate, which runs no graph.
A WhatsApp message whose message id is an answer already applied to a thread of the same customer, pending or
finished, is a re-delivery of that answer: a duplicate too, so no thread ever reads the same answer twice.
An email or WhatsApp message whose message id is already stored is checked before any extraction or question:
from the same customer it is a duplicate of the stored order, from another customer it is rejected.
A duplicate of a stored order runs no graph and writes no row, but gets the channel's reply naming that order again.
"""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from langgraph.types import Command

from purchase_cycle import db, email_order, whatsapp_order
from purchase_cycle.email_order import EmailRejected, read_email
from purchase_cycle.whatsapp_order import WhatsAppRejected, read_whatsapp, write_outbox

WEB_FORM = "web_form"
EMAIL = "email"
WHATSAPP_NEW = "whatsapp_new"
WHATSAPP_ANSWER = "whatsapp_answer"
REJECTED = "rejected"
DUPLICATE = "duplicate"
CHANNELS = {WEB_FORM: "web_form", EMAIL: "email", WHATSAPP_NEW: "whatsapp", WHATSAPP_ANSWER: "whatsapp"}


class Route(NamedTuple):
    kind: str
    thread_id: str | None = None  # the paused thread a WhatsApp answer resumes or a duplicate repeats
    reason: str | None = None  # why the item was rejected, or the stored order a duplicate repeats


SourceLookup = Callable[[str, str], str | None]  # (channel, thread id) -> message id of the thread's order
AnswerLookup = Callable[[str, str], list[str]]  # (channel, thread id) -> message ids of the answers applied to it


def _paused_duplicate(conn: sqlite3.Connection, channel: str, message: dict, paused_source: SourceLookup | None):
    """The pending thread of the same customer and channel whose order came from this message id, or None."""
    if paused_source is None:
        return None
    for pending in db.pending_clarifications(conn):
        if (pending["channel"], pending["customer_code"]) != (channel, message["customer"]["code"]):
            continue
        if paused_source(channel, pending["thread_id"]) == message["message_id"]:
            return pending["thread_id"]
    return None


def _applied_answer(conn: sqlite3.Connection, message: dict, applied_answers: AnswerLookup | None):
    """The WhatsApp thread of the same customer, of any status, that already applied this message as an answer."""
    if applied_answers is None:
        return None
    for thread in db.customer_threads(conn, "whatsapp", message["customer"]["code"]):
        if message["message_id"] in applied_answers("whatsapp", thread):
            return thread
    return None


def _stored_route(conn: sqlite3.Connection, channel: str, message: dict) -> Route | None:
    """A duplicate of the order stored from this message id, a rejection if another customer's, or None."""
    stored = db.stored_source(conn, channel, message["message_id"])
    if stored is None:
        return None
    if stored["customer_code"] == message["customer"]["code"]:
        return Route(DUPLICATE, stored["thread_id"], f"already stored as order {stored['order_id']}")
    reason = f"the {channel} message id '{message['message_id']}' is already stored for another customer"
    return Route(REJECTED, reason=reason)


def _email_route(path: Path, conn: sqlite3.Connection, paused_source: SourceLookup | None) -> Route:
    try:
        email = read_email(path, conn)
    except EmailRejected:
        return Route(EMAIL)
    stored = _stored_route(conn, "email", email)
    if stored:
        return stored
    duplicate = _paused_duplicate(conn, "email", email, paused_source)
    return Route(DUPLICATE, duplicate) if duplicate else Route(EMAIL)


def _whatsapp_route(
    path: Path, conn: sqlite3.Connection, paused_source: SourceLookup | None, applied_answers: AnswerLookup | None
) -> Route:
    try:
        message = read_whatsapp(path, conn)
    except WhatsAppRejected:
        return Route(WHATSAPP_NEW)
    stored = _stored_route(conn, "whatsapp", message)
    if stored:
        return stored
    duplicate = _paused_duplicate(conn, "whatsapp", message, paused_source)
    if duplicate:
        return Route(DUPLICATE, duplicate)
    answered = _applied_answer(conn, message, applied_answers)
    if answered:
        return Route(DUPLICATE, answered, "already applied as an answer to this thread")
    pending = db.latest_pending_clarification(conn, message["customer"]["code"])
    if pending is not None and pending["channel"] == "whatsapp":
        return Route(WHATSAPP_ANSWER, pending["thread_id"])
    return Route(WHATSAPP_NEW)


def route(
    item: Path | str,
    conn: sqlite3.Connection,
    paused_source: SourceLookup | None = None,
    applied_answers: AnswerLookup | None = None,
) -> Route:
    """The route of one inbox item, read from the item and the `clarifications` table only.

    `paused_source`, when given, returns the message id a pending thread started from, to spot a re-delivery;
    `applied_answers`, when given, returns the message ids of the answers a thread already applied.
    """
    path = Path(item)
    suffix = path.suffix.lower()
    if suffix == ".eml":
        return _email_route(path, conn, paused_source)
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
        return _whatsapp_route(path, conn, paused_source, applied_answers)
    return Route(REJECTED, reason=f"the file '{path.name}' is neither a web form submission nor a WhatsApp message")


def graph_input(path: Path, kind: str):
    """The input that starts or resumes the graph of a routed item."""
    if kind == WEB_FORM:
        return {"submission": json.loads(path.read_text(encoding="utf-8-sig"))}
    if kind == EMAIL:
        return {"email_path": str(path)}
    if kind == WHATSAPP_ANSWER:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return Command(resume={"answer": data["text"]["body"].strip(), "message_id": data["message_id"]})
    return {"message_path": str(path)}


def stored_reply(path: Path, routed: Route, graph, db_path: Path | str, outbox: Path | str | None) -> tuple:
    """The reply to a duplicate of a stored order, built as its thread built it, or (None, False) for another duplicate.

    The reply reads the stored order rows and the left-out lines kept in the stored thread's checkpoint; a WhatsApp
    reply is written to the same outbox file as the first one. Returns the reply state (with the stored order id) and whether that thread
    has not finished (a crash after its order was stored), so the caller can point to `resume`.
    """
    conn = db.connect(db_path)
    try:
        email = path.suffix.lower() == ".eml"
        message = read_email(path, conn) if email else read_whatsapp(path, conn)
        stored = db.stored_source(conn, "email" if email else "whatsapp", message["message_id"])
    finally:
        conn.close()
    if stored is None or stored["customer_code"] != message["customer"]["code"]:
        return None, False
    snapshot = graph.get_state({"configurable": {"thread_id": routed.thread_id}}) if routed.thread_id else None
    values = dict(snapshot.values) if snapshot else {}
    order = stored["order_id"]
    state = {**values, "customer": message.pop("customer"), "order_id": order, "errors": [], "is_order": True}
    state["lines"] = values.get("lines", [])
    if email:
        reply = {"reply": email_order.order_reply({**state, "email": message}, db_path), "order_id": order}
    else:
        text = whatsapp_order.order_reply({**state, "message": message}, db_path)
        reply = {"reply": text, "order_id": order}
        if outbox is not None:  # with no outbox folder given, the reply text is only returned
            reply["outbox_file"] = str(write_outbox(outbox, message["from"], message["message_id"], text))
    return reply, bool(snapshot and snapshot.next)


def run_inbox(
    folder: Path | str, graphs: dict, db_path: Path | str, run_id: str, outbox: Path | str | None = None
) -> list[dict]:
    """Route each file of the folder, in name order, and run it through the graph of its channel.

    `graphs` maps a channel (`web_form`, `email`, `whatsapp`) to its compiled graph. Each result holds the
    item name, its route, the thread id, and the final graph state or the error that stopped the item.
    A new thread id is `<channel>-<run_id>-<file stem>`; a WhatsApp answer resumes the paused thread and a
    duplicate of a paused order or of an applied answer runs no graph. A new thread id that already has a checkpoint
    (a run id reused with the same file name) runs no graph either: the item gets an error asking for a new run id.
    A duplicate of a stored order runs no graph either: its state holds the reply naming that order (WhatsApp
    writes it to `outbox`) and `unfinished` says whether the stored thread still has steps to resume.
    """

    def paused_source(channel: str, thread: str) -> str | None:
        values = graphs[channel].get_state({"configurable": {"thread_id": thread}}).values
        return (values.get("message") or values.get("email") or {}).get("message_id")

    def applied_answers(channel: str, thread: str) -> list[str]:
        # A paused clarify subgraph keeps the applied answer ids in its own state, read through its task;
        # once it finishes they are in the thread's own state.
        snapshot = graphs[channel].get_state({"configurable": {"thread_id": thread}}, subgraphs=True)
        paused = [m for task in snapshot.tasks if task.state for m in task.state.values.get("applied_answers", [])]
        return snapshot.values.get("applied_answers", []) + paused

    results = []
    for path in sorted(p for p in Path(folder).iterdir() if p.is_file()):
        conn = db.connect(db_path)
        try:
            routed = route(path, conn, paused_source, applied_answers)
        finally:
            conn.close()
        result = {
            "item": path.name,
            "route": routed,
            "thread_id": None,
            "state": None,
            "error": None,
            "unfinished": False,
        }
        results.append(result)
        if routed.kind == REJECTED:
            continue
        if routed.kind == DUPLICATE:
            result["thread_id"] = routed.thread_id
            channel = "email" if path.suffix.lower() == ".eml" else "whatsapp"
            result["state"], result["unfinished"] = stored_reply(path, routed, graphs[channel], db_path, outbox)
            continue
        channel = CHANNELS[routed.kind]
        result["thread_id"] = routed.thread_id or f"{channel}-{run_id}-{path.stem}"
        run_config = {"configurable": {"thread_id": result["thread_id"]}, "run_name": f"{channel}_order"}
        snapshot = graphs[channel].get_state(run_config) if routed.thread_id is None else None
        if snapshot is not None and snapshot.values:
            # A new item never runs on a checkpoint left by an earlier run with the same run id and file name.
            if snapshot.next and not snapshot.interrupts:  # a crashed run: `resume` finishes it
                hint = f"it has not finished; use purchase-cycle resume {result['thread_id']}"
            else:
                hint = "pass a new --run-id"
            result["error"] = ValueError(f"the thread id '{result['thread_id']}' is already used; {hint}")
            continue
        try:
            result["state"] = graphs[channel].invoke(graph_input(path, routed.kind), run_config, durability="sync")
        except Exception as error:  # a failure stops this item only
            result["error"] = error
    return results
