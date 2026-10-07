"""Email order subgraph: intake, extract, store and reply.

An `.eml` file is parsed with the standard library. The body (plain or HTML)
and each `.txt`, text-based `.pdf` and `.xlsx` attachment become text; other
attachments are listed as ignored. The sender address identifies the customer.
"""

import io
import sqlite3
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from purchase_cycle import db
from purchase_cycle.llm import ModelClient
from purchase_cycle.web_form import build_reply

CHANNEL = "email"
STATUS = "received"
SUPPORTED_ATTACHMENTS = (".txt", ".pdf", ".xlsx")
MAX_EMAIL_TEXT = 50_000  # bounds what one email can send to the model


class EmailRejected(ValueError):
    """The email cannot be processed; the message names the reason."""


class InvalidExtraction(ValueError):
    """The extractor answer fits the schema but not the catalog or the email; nothing is written."""


class EmailOrderState(TypedDict, total=False):
    email_path: str
    errors: list[str]
    email: dict
    customer: dict
    is_order: bool
    reason: str
    lines: list[dict]
    order_id: int | None
    reply: str


class _HTMLText(HTMLParser):
    BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}  # set off by empty lines
    LINES = {"br", "tr", "li"}  # start a new line

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCKS or tag in self.LINES:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return _tidy("".join(parser.parts))


def _tidy(text: str) -> str:
    """Collapse spaces inside each tab-separated cell, keep one empty line at most and trim both ends."""
    lines = ["\t".join(" ".join(cell.split()) for cell in line.split("\t")).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _decode_text(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return _tidy("\n".join(page.extract_text() or "" for page in reader.pages))


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def xlsx_text(data: bytes) -> str:
    """First sheet only, one line per non-empty row, cells separated by tabs."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        rows = []
        for row in workbook.worksheets[0].iter_rows(values_only=True):
            cells = [_cell(v) for v in row]
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append("\t".join(cells))
        return "\n".join(rows)
    finally:
        workbook.close()


def attachment_text(name: str, part: EmailMessage) -> str:
    """Text of one supported attachment; an empty string means nothing readable."""
    suffix = Path(name).suffix.lower()
    if suffix == ".txt":
        return _tidy(_decode_text(part))
    data = part.get_payload(decode=True) or b""
    try:
        return pdf_text(data) if suffix == ".pdf" else xlsx_text(data)
    except Exception as error:  # pypdf and openpyxl raise many types for a broken file
        raise EmailRejected(f"attachment '{name}' cannot be read: {error}") from error


def parse_email(data: bytes) -> dict:
    """Sender, subject, body text, attachment texts and ignored attachment names."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
        addresses = message["From"].addresses if message["From"] is not None else ()
    except Exception as error:  # malformed headers raise from the header parser
        raise EmailRejected(f"the email cannot be parsed: {error}") from error
    sender = addresses[0].addr_spec if addresses else ""
    if "@" not in sender:
        raise EmailRejected("the email cannot be parsed: no sender address in the From header")
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        text = _decode_text(body_part)
        body = html_to_text(text) if body_part.get_content_subtype() == "html" else _tidy(text)
    attachments, ignored = [], []
    for n, part in enumerate(message.iter_attachments(), start=1):
        name = part.get_filename() or f"attachment-{n}"
        if Path(name).suffix.lower() not in SUPPORTED_ATTACHMENTS:
            ignored.append(name)
            continue
        text = attachment_text(name, part)
        if text:
            attachments.append({"name": name, "text": text})
        else:  # for example a scanned PDF with no text layer
            ignored.append(name)
    return {
        "sender": sender,
        "subject": str(message["Subject"] or "").strip(),
        "body": body,
        "attachments": attachments,
        "ignored": ignored,
    }


def model_text(email: dict) -> str:
    """The only text the model sees: subject, body and supported attachments in a fixed layout."""
    out = [f"Subject: {email['subject']}", "", "Body:", email["body"]]
    for attachment in email["attachments"]:
        out += ["", f"Attachment: {attachment['name']}", attachment["text"]]
    return "\n".join(out)


def find_customer(conn: sqlite3.Connection, sender: str) -> dict | None:
    row = conn.execute("SELECT * FROM customers WHERE lower(email) = ?", (sender.lower(),)).fetchone()
    return dict(row) if row else None


def read_email(path: Path | str, conn: sqlite3.Connection) -> dict:
    """Deterministic intake: parse the file, find the customer and bound the text; no model call."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise EmailRejected(f"the email file '{path.name}' cannot be read: {error.strerror}") from error
    email = parse_email(data)
    customer = find_customer(conn, email["sender"])
    if customer is None:
        raise EmailRejected(f"the sender '{email['sender']}' is not a known customer")
    text = model_text(email)
    if len(text) > MAX_EMAIL_TEXT:
        raise EmailRejected(f"the email text has {len(text)} characters, above the limit of {MAX_EMAIL_TEXT}")
    return {**email, "customer": customer, "text": text}


def build_email_reply(email: dict, customer: dict, order_id: int | None, stored: list[dict], lines: list[dict]) -> str:
    """The phase 02 reply, addressed to the sender and quoting the subject."""
    unmatched = [(line["source_text"], line["quantity"]) for line in lines if line["sku"] is None]
    reference = f'email order "{email["subject"]}"' if email["subject"] else "email order"
    body = build_reply(customer, reference, order_id, stored, unmatched)
    return f"To: {email['sender']}\nSubject: Re: {email['subject']}\n\n{body}"


def build_email_order_graph(
    intake_client: ModelClient,
    extraction_client: ModelClient,
    db_path: Path | str,
    checkpointer=None,
    interrupt_after=None,
):
    conn = db.connect(db_path)
    try:
        known_skus = {row["sku"] for row in db.catalog_rows(conn)}
    finally:
        conn.close()

    def intake(state: EmailOrderState) -> EmailOrderState:
        conn = db.connect(db_path)
        try:
            email = read_email(state["email_path"], conn)
        except EmailRejected as error:
            return {"errors": [str(error)]}
        finally:
            conn.close()
        customer = email.pop("customer")
        decision = intake_client.extract(email["text"], case_id=Path(state["email_path"]).stem)
        return {
            "errors": [],
            "email": email,
            "customer": customer,
            "is_order": decision.is_order,
            "reason": decision.reason,
        }

    def extract(state: EmailOrderState) -> EmailOrderState:
        email = state["email"]
        answer = extraction_client.extract(email["text"], case_id=Path(state["email_path"]).stem)
        sources = {"body"} | {attachment["name"] for attachment in email["attachments"]}
        for n, line in enumerate(answer.lines, start=1):
            if line.sku is not None and line.sku not in known_skus:
                raise InvalidExtraction(f"Extracted line {n}: SKU '{line.sku}' is not in the catalog")
            if line.quantity <= 0:
                raise InvalidExtraction(f"Extracted line {n}: quantity {line.quantity} is not a positive whole number")
            if line.source not in sources:
                raise InvalidExtraction(
                    f"Extracted line {n}: source '{line.source}' is not the body or a read attachment"
                )
        return {"lines": [line.model_dump() for line in answer.lines]}

    def store(state: EmailOrderState) -> EmailOrderState:
        matched = [(line["sku"], line["quantity"]) for line in state["lines"] if line["sku"] is not None]
        if not matched:
            return {"order_id": None}
        conn = db.connect(db_path)
        try:
            return {"order_id": db.insert_order(conn, state["customer"]["code"], CHANNEL, STATUS, matched)}
        finally:
            conn.close()

    def reply(state: EmailOrderState) -> EmailOrderState:
        stored = []
        if state["order_id"] is not None:
            conn = db.connect(db_path)
            try:
                stored = db.order_line_details(conn, state["order_id"])
            finally:
                conn.close()
        return {
            "reply": build_email_reply(state["email"], state["customer"], state["order_id"], stored, state["lines"])
        }

    builder = StateGraph(EmailOrderState)
    builder.add_node("intake", intake)
    builder.add_node("extract", extract)
    builder.add_node("store", store)
    builder.add_node("reply", reply)
    builder.add_edge(START, "intake")
    builder.add_conditional_edges(
        "intake", lambda s: END if s["errors"] or not s["is_order"] else "extract", ["extract", END]
    )
    builder.add_edge("extract", "store")
    builder.add_edge("store", "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after, name="email_order")
