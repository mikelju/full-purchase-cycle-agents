"""Simulated WhatsApp order channel: inbox message files, customer lookup by phone and outbox replies.

A message file is a small subset of one message of the WhatsApp Cloud API webhook payload:
`message_id`, `from` (phone number), `timestamp`, `type` and, for `type` `text`, `text.body`.
Only text messages are read; a reply is written to the outbox as one JSON file per answered message.
"""

import json
import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from purchase_cycle.web_form import validation_errors

CHANNEL = "whatsapp"
MAX_WHATSAPP_TEXT = 4096  # the WhatsApp limit for one text message
TEXT_ONLY_REPLY = (
    "Hello,\n\nWe can only read text messages. Please send your order as text, "
    "with each product and its quantity.\n\nKind regards,\nCustomer service"
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
