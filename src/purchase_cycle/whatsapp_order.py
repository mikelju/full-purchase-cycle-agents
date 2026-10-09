"""WhatsApp order subgraph: intake, extract, optional clarify, store and reply.

A message file is a small subset of one message of the WhatsApp Cloud API webhook payload:
`message_id`, `from` (phone number), `timestamp`, `type` and, for `type` `text`, `text.body`.
The sender phone number, kept to its digits, identifies the customer.
Only text messages are read; a reply is written to the outbox as one JSON file per answered message.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from purchase_cycle import db
from purchase_cycle.email_order import InvalidExtraction
from purchase_cycle.llm import ModelClient
from purchase_cycle.web_form import build_reply, clarification_outcome, validation_errors

CHANNEL = "whatsapp"
STATUS = "received"
SOURCE = "message"  # the only valid line source of a WhatsApp extraction
MAX_WHATSAPP_TEXT = 4096  # the WhatsApp limit for one text message
TEXT_ONLY_REPLY = (
    "Hello,\n\nWe can only read text messages. Please send your order as text, "
    "with each product and its quantity.\n\nKind regards,\nCustomer service"
)
NOT_ORDER_REPLY = (
    "Dear {name},\n\nThank you for your message. It does not look like an order, so nothing was registered. "
    "To place an order, send the products and quantities you need in one message.\n\nKind regards,\nCustomer service"
)


class WhatsAppRejected(ValueError):
    """The message cannot be processed; the message names the reason.

    `reply_to` holds the recipient, the answered message id and the customer when the sender is a known
    customer who should get the text-only reply, otherwise None.
    """

    def __init__(self, reason: str, reply_to: dict | None = None):
        super().__init__(reason)
        self.reply_to = reply_to


class WhatsAppText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MAX_WHATSAPP_TEXT)


class WhatsAppMessage(BaseModel):
    """One inbox message file; media objects of non-text messages are ignored."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._=-]+$")
    sender: str = Field(alias="from", min_length=1, max_length=40, pattern=r"^\+?[0-9][0-9 -]*$")
    timestamp: str = Field(min_length=1, pattern=r"^[0-9]+$")
    type: str = Field(min_length=1)
    text: WhatsAppText | None = None

    @model_validator(mode="after")
    def _text_has_body(self):
        if self.type == "text" and self.text is None:
            raise ValueError("a text message needs text.body")
        return self


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def find_customer(conn: sqlite3.Connection, sender: str) -> dict | None:
    digits = phone_digits(sender)
    for row in conn.execute("SELECT * FROM customers ORDER BY code"):
        if phone_digits(row["phone"]) == digits:
            return dict(row)
    return None


def read_whatsapp(path: Path | str, conn: sqlite3.Connection) -> dict:
    """Deterministic intake: parse and validate the file, find the customer, accept text only; no model call."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WhatsAppRejected(f"the message file '{path.name}' cannot be read: {error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WhatsAppRejected(f"malformed message file '{path.name}': it is not valid JSON ({error.msg})") from error
    if not isinstance(data, dict):
        raise WhatsAppRejected(f"malformed message file '{path.name}': it is not a WhatsApp message object")
    try:
        message = WhatsAppMessage.model_validate(data)
    except ValidationError as error:
        details = "; ".join(validation_errors(error))
        raise WhatsAppRejected(f"malformed message file '{path.name}': {details}") from error
    customer = find_customer(conn, message.sender)
    if customer is None:
        raise WhatsAppRejected(f"the number '{phone_digits(message.sender)}' is not a known customer")
    if message.type != "text":
        reply_to = {"to": message.sender, "message_id": message.message_id, "customer": customer}
        raise WhatsAppRejected(f"the message type '{message.type}' is not text; only text is read", reply_to)
    return {
        "message_id": message.message_id,
        "from": message.sender,
        "timestamp": message.timestamp,
        "body": message.text.body,
        "customer": customer,
    }


def write_outbox(outbox: Path | str, to: str, message_id: str, text: str) -> Path:
    """Write the reply to one message; the file name is keyed by the answered message, so a re-run overwrites it."""
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"reply-{message_id}.json"
    content = json.dumps({"to": to, "in_reply_to": message_id, "text": text}, indent=2, sort_keys=True)
    path.write_text(content + "\n", encoding="utf-8", newline="\n")
    return path


def model_text(message: dict) -> str:
    """The only text the model sees, in a fixed layout with no id or timestamp, so recording keys depend on the text."""
    return f"WhatsApp message:\n{message['body']}"


class WhatsAppOrderState(TypedDict, total=False):
    message_path: str
    errors: list[str]
    message: dict
    customer: dict
    is_order: bool
    reason: str
    lines: list[dict]
    order_id: int | None
    reply: str
    outbox_file: str
    resolutions: list[dict]
    removed: list[dict]
    unresolved: list[dict]
    clarification: str | None


def build_whatsapp_order_graph(
    intake_client: ModelClient,
    extraction_client: ModelClient,
    db_path: Path | str,
    outbox: Path | str,
    checkpointer=None,
    interrupt_after=None,
    clarification=None,
):
    """Mirror of the email graph for one inbox message; with `clarification` a `clarify` step runs before `store`."""
    conn = db.connect(db_path)
    try:
        known_skus = {row["sku"] for row in db.catalog_rows(conn)}
    finally:
        conn.close()

    def intake(state: WhatsAppOrderState) -> WhatsAppOrderState:
        conn = db.connect(db_path)
        try:
            message = read_whatsapp(state["message_path"], conn)
        except WhatsAppRejected as error:
            if error.reply_to is None:
                return {"errors": [str(error)]}
            sender = {"message_id": error.reply_to["message_id"], "from": error.reply_to["to"]}
            return {"errors": [str(error)], "message": sender, "customer": error.reply_to["customer"]}
        finally:
            conn.close()
        customer = message.pop("customer")
        decision = intake_client.extract(model_text(message), case_id=Path(state["message_path"]).stem)
        return {
            "errors": [],
            "message": message,
            "customer": customer,
            "is_order": decision.is_order,
            "reason": decision.reason,
        }

    def after_intake(state: WhatsAppOrderState) -> str:
        if state["errors"]:
            return "reply" if "customer" in state else END
        return "extract" if state["is_order"] else "reply"

    def extract(state: WhatsAppOrderState) -> WhatsAppOrderState:
        answer = extraction_client.extract(model_text(state["message"]), case_id=Path(state["message_path"]).stem)
        lines = []
        for n, line in enumerate(answer.lines, start=1):
            if line.sku is not None and line.sku not in known_skus:
                raise InvalidExtraction(f"Extracted line {n}: SKU '{line.sku}' is not in the catalog")
            if line.source.strip().lower() != SOURCE:
                raise InvalidExtraction(f"Extracted line {n}: source '{line.source}' is not the message")
            lines.append({**line.model_dump(), "source": SOURCE})
        return {"lines": lines}

    def store(state: WhatsAppOrderState, config: RunnableConfig) -> WhatsAppOrderState:
        matched = [(line["sku"], line["quantity"]) for line in state["lines"] if line["sku"] is not None]
        outcome = clarification_outcome(state, config)
        conn = db.connect(db_path)
        try:
            if not matched:
                if outcome:
                    with conn:
                        db.finish_clarification(conn, *outcome)
                return {"order_id": None}
            order_id = db.insert_order(conn, state["customer"]["code"], CHANNEL, STATUS, matched, outcome)
            return {"order_id": order_id}
        finally:
            conn.close()

    def reply(state: WhatsAppOrderState) -> WhatsAppOrderState:
        customer, message = state["customer"], state["message"]
        if state["errors"]:
            text = TEXT_ONLY_REPLY
        elif not state["is_order"]:
            text = NOT_ORDER_REPLY.format(name=customer["contact_name"])
        else:
            stored = []
            if state["order_id"] is not None:
                conn = db.connect(db_path)
                try:
                    stored = db.order_line_details(conn, state["order_id"])
                finally:
                    conn.close()
            unmatched = [(line["source_text"], line["quantity"]) for line in state["lines"] if line["sku"] is None]
            left_out = {
                "removed": state.get("removed", []),
                "unresolved": state.get("unresolved", []),
                "closed": state.get("clarification") == "closed",
                "answered": state.get("clarification") == "answered",
            }
            text = build_reply(customer, "WhatsApp order", state["order_id"], stored, unmatched, **left_out)
        path = write_outbox(outbox, message["from"], message["message_id"], text)
        return {"reply": text, "outbox_file": str(path)}

    builder = StateGraph(WhatsAppOrderState)
    builder.add_node("intake", intake)
    builder.add_node("extract", extract)
    builder.add_node("store", store)
    builder.add_node("reply", reply)
    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", after_intake, ["extract", "reply", END])
    if clarification is None:
        builder.add_edge("extract", "store")
    else:
        from purchase_cycle.clarification import build_clarification_graph  # it imports the web form module

        builder.add_node("clarify", build_clarification_graph(clarification, db_path, CHANNEL))
        builder.add_edge("extract", "clarify")
        builder.add_edge("clarify", "store")
    builder.add_edge("store", "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after, name="whatsapp_order")
