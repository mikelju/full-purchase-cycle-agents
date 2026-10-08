"""Email order subgraph: intake, extract, store and reply.

An `.eml` file is parsed with the standard library. The body (plain or HTML)
and each `.txt`, text-based `.pdf` and `.xlsx` attachment become text; other
attachments are listed as ignored. The sender address identifies the customer.
"""

import io
import re
import sqlite3
from email import errors, policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from purchase_cycle import db
from purchase_cycle.llm import ModelClient
from purchase_cycle.web_form import build_reply, clarification_outcome

CHANNEL = "email"
STATUS = "received"
SUPPORTED_ATTACHMENTS = (".txt", ".pdf", ".xlsx")
MAX_EMAIL_TEXT = 50_000  # bounds what one email can send to the model
BROKEN_STRUCTURE = (
    errors.NoBoundaryInMultipartDefect,
    errors.StartBoundaryNotFoundDefect,
    errors.MultipartInvariantViolationDefect,
)


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
    resolutions: list[dict]
    removed: list[dict]
    unresolved: list[dict]
    clarification: str | None


class _HTMLText(HTMLParser):
    BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "pre"}  # set off by empty lines
    LINES = {"br", "tr", "li"}  # start a new line

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0
        self._pre = 0  # open <pre> elements, whose source line breaks are kept
        self._cells = 0  # cells opened in the current line

    def _new_line(self):
        self.parts.append("\n")
        self._cells = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCKS or tag in self.LINES:
            self._pre += tag == "pre"
            self._new_line()
        elif tag in ("td", "th"):
            if self._cells:  # tabs only between cells, so an empty first cell keeps its column
                self.parts.append("\t")
            self._cells += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            if tag == "pre":
                self._pre = max(0, self._pre - 1)
            self._new_line()

    def handle_data(self, data):
        if self._skip:
            return
        if self._pre:  # preformatted text keeps its source line breaks
            self.parts.append(data.replace("\r\n", "\n").replace("\r", "\n"))
        else:  # elsewhere a line break in the HTML source is only a space
            self.parts.append(data.replace("\r", " ").replace("\n", " "))


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return _tidy("".join(parser.parts), keep_cells=True)


def _tidy(text: str, keep_cells: bool = False) -> str:
    """Collapse spaces inside each tab-separated cell, keep one empty line at most and trim both ends.

    With keep_cells, the empty cells at either end of a row stay, so table columns do not shift.
    """
    lines = []
    for line in text.splitlines():
        cells = [" ".join(cell.split()) for cell in line.split("\t")]
        lines.append(("\t".join(cells) if any(cells) else "") if keep_cells else "\t".join(cells).strip())
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
    except (LookupError, ValueError):  # unknown or malformed charset, or bytes that do not fit it
        return payload.decode("utf-8", errors="replace")


class _TooLong(Exception):
    """Text extraction stopped because the email is already above MAX_EMAIL_TEXT."""


def pdf_text(data: bytes, limit: int | None = None) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages, size = [], 0
    for page in reader.pages:
        pages.append(page.extract_text() or "")
        size += len(_tidy(pages[-1]))
        if limit is not None and size > limit:
            raise _TooLong
    return _tidy("\n".join(pages))


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def xlsx_text(data: bytes, limit: int | None = None) -> str:
    """First sheet only, one line per non-empty row, cells separated by tabs."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        rows, size = [], 0
        for row in workbook.worksheets[0].iter_rows(values_only=True):
            cells = [_cell(v) for v in row]
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append("\t".join(cells))
                size += len(rows[-1]) + 1
                if limit is not None and size > limit:
                    raise _TooLong
        return "\n".join(rows)
    finally:
        workbook.close()


def attachment_text(name: str, part: EmailMessage, limit: int | None = None) -> str:
    """Text of one supported attachment; an empty string means nothing readable."""
    suffix = Path(name).suffix.lower()
    if suffix == ".txt":
        return _tidy(_decode_text(part))
    data = part.get_payload(decode=True) or b""
    try:
        return pdf_text(data, limit) if suffix == ".pdf" else xlsx_text(data, limit)
    except _TooLong:
        raise
    except Exception as error:  # pypdf and openpyxl raise many types for a broken file
        raise EmailRejected(f"attachment '{name}' cannot be read: {error}") from error


def _too_long() -> EmailRejected:
    return EmailRejected(
        f"the email text has more than {MAX_EMAIL_TEXT} characters, above the limit of {MAX_EMAIL_TEXT}"
    )


def _parse_message(data: bytes) -> tuple[EmailMessage, str]:
    """The parsed message and its sender address; a broken message is rejected."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
        addresses = message["From"].addresses if message["From"] is not None else ()
    except Exception as error:  # malformed headers raise from the header parser
        raise EmailRejected(f"the email cannot be parsed: {error}") from error
    sender = addresses[0].addr_spec if addresses else ""
    if "@" not in sender:
        raise EmailRejected("the email cannot be parsed: no sender address in the From header")
    for part in message.walk():
        broken = [type(d).__name__ for d in part.defects if isinstance(d, BROKEN_STRUCTURE)]
        if broken:
            raise EmailRejected(f"the email cannot be parsed: broken MIME structure ({', '.join(broken)})")
    return message, sender


def _subject(message: EmailMessage) -> str:
    """The decoded subject on one line: control characters and Unicode line breaks (NEL, LS, PS) become spaces."""
    return re.sub(r"[\x00-\x1f\x7f\x85  ]", " ", str(message["Subject"] or "")).strip()


def _walk(part: EmailMessage):
    """Like walk, but an attached email (message/rfc822) is one part: its body and attachments stay inside it."""
    yield part
    if part.is_multipart() and part.get_content_maintype() != "message":
        for sub in part.iter_parts():
            yield from _walk(sub)


def _attachment_parts(message: EmailMessage) -> list[EmailMessage]:
    """The attachments, plus any part with a file name that the standard library took as a body candidate."""
    parts = list(message.iter_attachments())
    seen = {id(part) for part in parts}
    extra = [p for p in _walk(message) if not p.is_multipart() and p.get_filename() and id(p) not in seen]
    if not extra:
        return parts
    seen |= {id(part) for part in extra}
    return [p for p in _walk(message) if id(p) in seen]


def _body(message: EmailMessage, attachments: list[EmailMessage]) -> str:
    """Plain text preferred over HTML; a part with a file name is never the body and a blank plain part gives way to HTML."""
    part = message.get_body(preferencelist=("plain", "html"))
    if part is not None and not part.get_filename():
        text = _decode_text(part)
        if part.get_content_subtype() == "html":
            return html_to_text(text)
        if text.strip():
            return _tidy(text)
    taken = {id(p) for p in attachments}
    candidates = [
        p
        for p in _walk(message)
        if not p.is_multipart()
        and id(p) not in taken
        and not p.get_filename()
        and p.get_content_disposition() != "attachment"
        and p.get_content_type() in ("text/plain", "text/html")
    ]
    for subtype in ("plain", "html"):
        for candidate in candidates:
            if candidate.get_content_subtype() == subtype:
                text = _decode_text(candidate)
                if subtype == "html":
                    return html_to_text(text)
                if text.strip():
                    return _tidy(text)
    return ""


def _content(message: EmailMessage, limit: int | None = None) -> dict:
    """Subject, body, attachment texts and ignored names; with a limit, stop once the text is above it."""
    parts = _attachment_parts(message)
    body = _body(message, parts)
    size = len(body)
    if limit is not None and size > limit:
        raise _too_long()
    attachments, ignored = [], []
    for n, part in enumerate(parts, start=1):
        name = part.get_filename() or f"attachment-{n}"
        if Path(name).suffix.lower() not in SUPPORTED_ATTACHMENTS:
            ignored.append(name)
            continue
        try:
            text = attachment_text(name, part, None if limit is None else limit - size)
        except _TooLong:
            raise _too_long() from None
        size += len(text)
        if limit is not None and size > limit:
            raise _too_long()
        if text:
            attachments.append({"name": name, "text": text})
        else:  # for example a scanned PDF with no text layer
            ignored.append(name)
    return {"subject": _subject(message), "body": body, "attachments": attachments, "ignored": ignored}


def parse_email(data: bytes) -> dict:
    """Sender, subject, body text, attachment texts and ignored attachment names."""
    message, sender = _parse_message(data)
    return {"sender": sender, **_content(message)}


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
    message, sender = _parse_message(data)
    customer = find_customer(conn, sender)  # before any attachment is read
    if customer is None:
        raise EmailRejected(f"the sender '{sender}' is not a known customer")
    email = {"sender": sender, **_content(message, MAX_EMAIL_TEXT)}
    text = model_text(email)
    if len(text) > MAX_EMAIL_TEXT:
        raise EmailRejected(f"the email text has {len(text)} characters, above the limit of {MAX_EMAIL_TEXT}")
    return {**email, "customer": customer, "text": text}


def build_email_reply(
    email: dict, customer: dict, order_id: int | None, stored: list[dict], lines: list[dict], **left_out
) -> str:
    """The phase 02 reply, addressed to the sender and quoting the subject; `left_out` as in `build_reply`."""
    unmatched = [(line["source_text"], line["quantity"]) for line in lines if line["sku"] is None]
    reference = f'email order "{email["subject"]}"' if email["subject"] else "email order"
    body = build_reply(customer, reference, order_id, stored, unmatched, **left_out)
    subject = email["subject"] if email["subject"].lower().startswith("re:") else f"Re: {email['subject']}"
    return f"To: {email['sender']}\nSubject: {subject}\n\n{body}"


def build_email_order_graph(
    intake_client: ModelClient,
    extraction_client: ModelClient,
    db_path: Path | str,
    checkpointer=None,
    interrupt_after=None,
    clarification=None,
):
    """Phase 03 graph; with `clarification` (ClarificationClients) a `clarify` step runs before `store`."""
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
        sources = {name.strip().lower(): name for name in ["body"] + [a["name"] for a in email["attachments"]]}
        lines = []
        for n, line in enumerate(answer.lines, start=1):
            if line.sku is not None and line.sku not in known_skus:
                raise InvalidExtraction(f"Extracted line {n}: SKU '{line.sku}' is not in the catalog")
            # an unmatched source is kept as returned: only schema, SKU and quantity failures stop the email
            source = sources.get(line.source.strip().lower(), line.source)
            lines.append({**line.model_dump(), "source": source})
        return {"lines": lines}

    def store(state: EmailOrderState, config: RunnableConfig) -> EmailOrderState:
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

    def reply(state: EmailOrderState) -> EmailOrderState:
        stored = []
        if state["order_id"] is not None:
            conn = db.connect(db_path)
            try:
                stored = db.order_line_details(conn, state["order_id"])
            finally:
                conn.close()
        left_out = {
            "removed": state.get("removed", []),
            "unresolved": state.get("unresolved", []),
            "closed": state.get("clarification") == "closed",
        }
        return {
            "reply": build_email_reply(
                state["email"], state["customer"], state["order_id"], stored, state["lines"], **left_out
            )
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
    if clarification is None:
        builder.add_edge("extract", "store")
    else:
        from purchase_cycle.clarification import build_clarification_graph  # it imports the web form module

        builder.add_node("clarify", build_clarification_graph(clarification, db_path, CHANNEL))
        builder.add_edge("extract", "clarify")
        builder.add_edge("clarify", "store")
    builder.add_edge("store", "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after, name="email_order")
