"""Phase 09: claims to the supplier for the invoices that phase 08 left with discrepancies."""

import json
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from purchase_cycle import config, db, llm, quotes, reconciliation, review, sim_clock

CLAIM_SUBJECT = "Claim on invoice {invoice} for purchase order {order}"
MAX_CLAIM_BODY = 3000
CHANNEL = "reconcile"
STEP = "claim_drafting"
AMOUNT_TOLERANCE = 0.005  # half a cent
_CURRENCY = r"(?:EUR|euros?|\u20ac)(?![A-Za-z])"
_NUMBER = r"\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
# A money amount: a number with decimals, or any number next to EUR, euro(s) or the euro sign; bare integers are not
# money. A match never starts inside a word, a number or a SKU and never ends inside a number (dates are not amounts).
AMOUNT_PATTERN = re.compile(
    rf"(?<![\w.,])(?<!\w-)(?:(?P<pre>{_CURRENCY})\s*)?(?P<num>{_NUMBER})(?!\d|[.,]\d)(?:\s*(?P<post>{_CURRENCY}))?",
    re.IGNORECASE,
)
CLAIM_TEMPLATE = """Dear {supplier},

We have found differences between your invoice {invoice_number} and our purchase order {order}:
{lines}

The amount in dispute is {amount_in_dispute:.2f} EUR.
Please send us a corrected invoice or a credit note.

Best regards,
Purchasing department"""
MONEY_KEYS = ("unit_amount", "amount")

ROW_KEYS = reconciliation.DISCREPANCY_COLUMNS  # sku, type, expected, found, unit_amount, amount, text


def candidates(conn: sqlite3.Connection) -> list[int]:
    """Invoice ids whose reconciliation is `discrepancies` and that have no claim yet, in invoice order."""
    rows = conn.execute(
        "SELECT r.invoice_id FROM reconciliations r LEFT JOIN claims c ON c.reconciliation_id = r.id "
        "WHERE r.result = 'discrepancies' AND c.id IS NULL ORDER BY r.invoice_id"
    )
    return [row[0] for row in rows]


def amount_in_dispute(differences: list[dict]) -> float:
    """Sum of the differences that make us pay more: the positive amounts of price, quantity and extra lines."""
    total = 0.0
    for row in differences:
        amount = row["amount"]
        if amount is None:
            continue
        if row["type"] in ("price", "quantity", "extra_line") and amount > 0:
            total += amount
    return round(total, 2)


def claim_facts(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    """The references, supplier, every discrepancy row and the amount in dispute; None without discrepancies."""
    head = conn.execute(
        "SELECT r.id, r.purchase_order_id, d.number, s.name, s.email FROM reconciliations r "
        "JOIN supplier_documents d ON d.id = r.invoice_id "
        "JOIN purchase_orders o ON o.id = r.purchase_order_id JOIN suppliers s ON s.code = o.supplier_code "
        "WHERE r.invoice_id = ? AND r.result = 'discrepancies'",
        (invoice_id,),
    ).fetchone()
    if head is None:
        return None
    rows = conn.execute(
        f"SELECT {', '.join(ROW_KEYS)} FROM discrepancies WHERE reconciliation_id = ? ORDER BY id", (head[0],)
    )
    differences = [dict(row) for row in rows]
    return {
        "invoice": reconciliation.invoice_reference(invoice_id),
        "invoice_number": head[2],
        "order": reconciliation.order_reference(head[1]),
        "supplier": head[3],
        "recipient": head[4],
        "differences": differences,
        "amount_in_dispute": amount_in_dispute(differences),
    }


def facts_prompt(facts: dict) -> str:
    """The facts as one JSON object with sorted keys and two-decimal amounts; no recipient, so the key is stable."""
    differences = []
    for row in facts["differences"]:
        row = dict(row)
        for key in MONEY_KEYS + (("expected", "found") if row["type"] == "price" else ()):
            if row[key] is not None:
                row[key] = f"{row[key]:.2f}"
        differences.append(row)
    shown = {k: v for k, v in facts.items() if k != "recipient"}
    shown |= {"differences": differences, "amount_in_dispute": f"{facts['amount_in_dispute']:.2f}"}
    return json.dumps(shown, sort_keys=True)


def _read_number(text: str) -> tuple[float, bool]:
    """The value of a number written with thousands separators and a decimal point or comma, and whether it has decimals."""
    text = text.replace(" ", "")
    marks = [i for i, c in enumerate(text) if c in ".,"]
    if not marks:
        return float(text), False
    last = marks[-1]
    head, tail = text[:last], text[last + 1 :]
    other_kind = any(c in ".," and c != text[last] for c in head)
    if len(tail) != 3 or other_kind or head == "0":
        return float(re.sub(r"[.,]", "", head) + "." + tail), True
    return float(re.sub(r"[.,]", "", text)), False


def _money_spans(text: str) -> list[tuple[float, int]]:
    """Every money amount of the text with the position where it starts."""
    spans = []
    for m in AMOUNT_PATTERN.finditer(text):
        value, decimals = _read_number(m["num"])
        if m["pre"] or m["post"] or decimals:
            spans.append((value, m.start()))
    return spans


def money_amounts(text: str) -> list[float]:
    """Every money amount in the text, read by AMOUNT_PATTERN."""
    return [value for value, _ in _money_spans(text)]


def _near(a: float, b: float) -> bool:
    return abs(a - b) < AMOUNT_TOLERANCE


def mentions(text: str, token: str) -> bool:
    """Whether the token appears in the text as a whole token, not inside a longer SKU or number."""
    return bool(re.search(_token(token), text))


def _sentences(body: str) -> list[str]:
    return [part for line in body.splitlines() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]


def _token(token: str) -> str:
    return rf"(?<![A-Za-z0-9])(?<![A-Za-z0-9]-){re.escape(token)}(?![A-Za-z0-9]|-[A-Za-z0-9])"


def amount_stated(row: dict, body: str, rows: list[dict]) -> bool:
    """Whether the amount of a row is written in its item's segment: from the item to the next other item."""
    if row["amount"] is None:
        return True
    item = row["sku"] or row["text"]
    amount = abs(row["amount"])
    if not item:
        return False
    others = {name for r in rows for name in (r["sku"], r["text"]) if name and name != item}
    stops = sorted(m.start() for name in others for m in re.finditer(_token(name), body))
    for m in re.finditer(_token(item), body):
        end = next((pos for pos in stops if pos > m.start()), len(body))
        if any(_near(v, amount) for v in money_amounts(body[m.start() : end])):
            return True
    return False


def check_draft(body: str, facts: dict) -> list[str]:
    """Deterministic fact check of a draft; an empty list means it passes."""
    problems = []
    total = abs(facts["amount_in_dispute"])
    sentences = _sentences(body)
    for label, reference in (("invoice number", facts["invoice_number"]), ("purchase order", facts["order"])):
        if reference and not mentions(body, reference):
            problems.append(f"missing {label} {reference}")
    money = {abs(total)}
    for row in facts["differences"]:
        item = row["sku"] or row["text"]
        if item and not mentions(body, item):
            problems.append(f"missing difference {item}")
        if not amount_stated(row, body, facts["differences"]):
            problems.append(f"missing amount {abs(row['amount']):.2f} of {item}")
        keys = ("unit_amount", "amount") + (("expected", "found") if row["type"] == "price" else ())
        money |= {abs(row[key]) for key in keys if row[key] is not None}
    disputed = [part for part in sentences if re.search(r"disput", part, re.IGNORECASE)]
    stated = [money_amounts(part) for part in disputed]
    if not any(_near(v, total) for values in stated for v in values):
        problems.append(f"missing amount in dispute {facts['amount_in_dispute']:.2f}")
    if not re.search(r"corrected invoice|credit note", body, re.IGNORECASE):
        problems.append("missing request for a corrected invoice or a credit note")
    unnamed = body  # a SKU or product text may hold numbers such as "2.5 cm"; they are names, not amounts
    names = {name for row in facts["differences"] for name in (row["sku"], row["text"]) if name}
    for name in sorted(names, key=len, reverse=True):
        unnamed = unnamed.replace(name, " ")
    for value in money_amounts(unnamed):
        if not any(_near(value, known) for known in money):
            problems.append(f"invented amount {value:.2f}")
    if not body.isascii():
        problems.append("body is not ASCII")
    if len(body) > MAX_CLAIM_BODY:
        problems.append(f"body longer than {MAX_CLAIM_BODY} characters")
    return problems


def _template_line(row: dict) -> str:
    item = row["sku"] or row["text"] or "unnamed line"
    amount = "no amount stated" if row["amount"] is None else f"{abs(row['amount']):.2f} EUR"
    if row["type"] == "price":
        detail = "price"
        if row["expected"] is not None and row["found"] is not None:
            detail += f" {row['expected']:.2f} EUR on the order, {row['found']:.2f} EUR on the invoice"
    elif row["type"] == "quantity":
        detail = f"quantity {row['expected']:g} expected, {row['found']:g} invoiced"
    elif row["type"] == "extra_line":
        detail = "invoiced but not on the order"
    else:
        detail = "ordered but not invoiced"
    return f"- {item}: {detail}, difference {amount}"


def fill_template(facts: dict) -> str:
    lines = "\n".join(_template_line(row) for row in facts["differences"])
    return CLAIM_TEMPLATE.format(
        supplier=facts["supplier"],
        invoice_number=facts["invoice_number"] or facts["invoice"],
        order=facts["order"],
        lines=lines,
        amount_in_dispute=facts["amount_in_dispute"],
    )


def draft_claim(conn: sqlite3.Connection, client: llm.ModelClient, facts: dict) -> dict:
    """One model call; recipient and subject come from the facts, never from the model.

    An invalid answer or a failed fact check stores the template instead and parks the invoice as needs_review.
    A transient model error and a missing recording propagate.
    """
    reason = None
    try:
        body = client.extract(facts_prompt(facts), case_id=f"claim-{facts['invoice']}").body
        problems = check_draft(body, facts)
        reason = "; ".join(problems) or None
    except llm.InvalidModelOutput as error:
        reason = str(error)
    source = "model"
    if reason is not None:
        body, source = fill_template(facts), "template"
        db.park_failure(conn, f"claim-{facts['invoice']}", CHANNEL, facts["invoice"], STEP, reason)
    return {
        "recipient": facts["recipient"],
        "subject": CLAIM_SUBJECT.format(invoice=facts["invoice_number"] or facts["invoice"], order=facts["order"]),
        "body": body,
        "source": source,
    }


AWAITING, SENT, REJECTED = "awaiting_approval", "sent", "rejected"


class ClaimState(TypedDict, total=False):
    invoice_id: int
    message: dict
    decision: str
    final_body: str | None


def claim_thread_id(invoice_id: int) -> str:
    return f"claim-{reconciliation.invoice_reference(invoice_id)}"


def _claim_row(conn: sqlite3.Connection, invoice_id: int):
    return conn.execute(
        "SELECT c.* FROM claims c JOIN reconciliations r ON r.id = c.reconciliation_id WHERE r.invoice_id = ?",
        (invoice_id,),
    ).fetchone()


def draft_message(db_path: Path | str, client: llm.ModelClient | None, invoice_id: int) -> dict:
    """The stored claim as a message; a claim without a row is drafted and stored first, a stored one calls no model."""
    conn = db.connect(db_path)
    try:
        row = _claim_row(conn, invoice_id)
        if row is None:
            facts = claim_facts(conn, invoice_id)
            draft = draft_claim(conn, client, facts)
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO claims (reconciliation_id, recipient, subject, draft_body, draft_source, "
                    "status, drafted_at) SELECT id, ?, ?, ?, ?, ?, ? FROM reconciliations WHERE invoice_id = ?",
                    (
                        draft["recipient"],
                        draft["subject"],
                        draft["body"],
                        draft["source"],
                        AWAITING,
                        sim_clock.now(conn).strftime(sim_clock.FORMAT),
                        invoice_id,
                    ),
                )
            row = _claim_row(conn, invoice_id)
        return {"to": row["recipient"], "subject": row["subject"], "body": row["draft_body"]}
    finally:
        conn.close()


def send_claim(db_path: Path | str, outbox: Path | str, state: ClaimState) -> None:
    """Write the keyed email once and set the claim `sent`; a claim that is no longer awaiting is left alone."""
    conn = db.connect(db_path)
    try:
        row = _claim_row(conn, state["invoice_id"])
        if row["status"] != AWAITING:
            return
        code = conn.execute(
            "SELECT o.supplier_code FROM reconciliations r JOIN purchase_orders o ON o.id = r.purchase_order_id "
            "WHERE r.id = ?",
            (row["reconciliation_id"],),
        ).fetchone()[0]
        name = f"CLAIM-{reconciliation.invoice_reference(state['invoice_id'])}-{code}"
        quotes._write_email(
            Path(outbox), name, row["recipient"], row["subject"], state["final_body"], row["drafted_at"]
        )
        with conn:
            conn.execute(
                "UPDATE claims SET status = ?, final_body = ?, edited = ?, decided_at = ?, outbox_file = ? "
                "WHERE id = ? AND status = ?",
                (
                    SENT,
                    state["final_body"],
                    int(state["decision"] == review.EDIT),
                    sim_clock.now(conn).strftime(sim_clock.FORMAT),
                    str(Path(outbox) / f"{name}.eml"),
                    row["id"],
                    AWAITING,
                ),
            )
    finally:
        conn.close()


def close_claim(db_path: Path | str, state: ClaimState) -> None:
    """Set the claim `rejected` and write nothing; a claim that is no longer awaiting is left alone."""
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE claims SET status = ?, decided_at = ? WHERE status = ? AND reconciliation_id = "
                "(SELECT id FROM reconciliations WHERE invoice_id = ?)",
                (REJECTED, sim_clock.now(conn).strftime(sim_clock.FORMAT), AWAITING, state["invoice_id"]),
            )
    finally:
        conn.close()


def build_claim_graph(
    db_path: Path | str, client: llm.ModelClient | None = None, outbox: Path | str | None = None, checkpointer=None
):
    """One thread per invoice: draft, review (the pause), then send or close."""

    def draft(state: ClaimState) -> ClaimState:
        return {"message": draft_message(db_path, client, state["invoice_id"])}

    def send(state: ClaimState) -> ClaimState:
        send_claim(db_path, outbox or config.SUPPLIER_OUTBOX_DIR, state)
        return {}

    def close(state: ClaimState) -> ClaimState:
        close_claim(db_path, state)
        return {}

    builder = StateGraph(ClaimState)
    builder.add_node("draft", draft, retry_policy=llm.MODEL_RETRY)
    builder.add_node("review", review.review)
    builder.add_node("send", send)
    builder.add_node("close", close)
    builder.add_edge(START, "draft")
    builder.add_edge("draft", "review")
    builder.add_conditional_edges(
        "review", lambda s: "close" if s["decision"] == review.REJECT else "send", ["send", "close"]
    )
    builder.add_edge("send", END)
    builder.add_edge("close", END)
    return builder.compile(checkpointer=checkpointer, name="claim")


class DecisionRefused(Exception):
    """A command that the claim cannot take now; nothing was changed."""


def _config(invoice_id: int) -> dict:
    return {"configurable": {"thread_id": claim_thread_id(invoice_id)}}


def claim_of(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    """The stored claim of an invoice as a dict, or None."""
    row = _claim_row(conn, invoice_id)
    return None if row is None else dict(row)


def draft_claims(
    db_path: Path | str, checkpointer, client: llm.ModelClient, outbox: Path | str | None = None
) -> list[dict]:
    """Draft one claim per candidate and stop each thread at the review; a thread that already exists is resumed."""
    graph = build_claim_graph(db_path, client, outbox, checkpointer)
    results = []
    conn = db.connect(db_path)
    try:
        for invoice_id in candidates(conn):
            run = _config(invoice_id)
            graph.invoke(None if graph.get_state(run).next else {"invoice_id": invoice_id}, run)
            results.append(
                {"invoice_id": invoice_id, "facts": claim_facts(conn, invoice_id), "claim": claim_of(conn, invoice_id)}
            )
    finally:
        conn.close()
    return results


def _check_awaiting(conn: sqlite3.Connection, invoice_id: int) -> None:
    ref = reconciliation.invoice_reference(invoice_id)
    row = claim_of(conn, invoice_id)
    if row is None:
        raise DecisionRefused(f"no claim for {ref}; run reconcile claims first")
    if row["status"] != AWAITING:
        raise DecisionRefused(f"claim {ref} is {row['status']}, not {AWAITING}")


def _paused(db_path: Path | str, checkpointer, invoice_id: int) -> str | None:
    """The node the claim thread waits at, or None when it has no checkpoint or is finished."""
    state = build_claim_graph(db_path, None, None, checkpointer).get_state(_config(invoice_id))
    return state.next[0] if state.next else None


def _resume(db_path: Path | str, checkpointer, outbox, invoice_id: int, answer: dict | None) -> None:
    """Resume the thread with an answer for the pause, or with none to finish a node that stopped halfway."""
    graph = build_claim_graph(db_path, None, outbox, checkpointer)
    graph.invoke(None if answer is None else Command(resume=answer), _config(invoice_id))


def _decided(db_path: Path | str, invoice_id: int) -> dict:
    conn = db.connect(db_path)
    try:
        return claim_of(conn, invoice_id)
    finally:
        conn.close()


def _no_pause(ref: str) -> DecisionRefused:
    return DecisionRefused(f"no paused claim for {ref} in the checkpoints file; run reconcile claims first")


def approve_claim(
    db_path: Path | str, checkpointer, outbox: Path | str, invoice_id: int, body: str | None = None
) -> dict:
    """Send the draft, or `body` in its place; refused commands change nothing."""
    ref = reconciliation.invoice_reference(invoice_id)
    conn = db.connect(db_path)
    try:
        _check_awaiting(conn, invoice_id)
    finally:
        conn.close()
    if body is not None and not body.strip():
        raise DecisionRefused("the body text is empty")
    node = _paused(db_path, checkpointer, invoice_id)
    if node == "close":
        raise DecisionRefused(f"claim {ref} is being rejected; run reject to finish it")
    if node not in ("review", "send"):
        raise _no_pause(ref)
    answer = None
    if node == "review":
        answer = {"action": review.APPROVE} if body is None else {"action": review.EDIT, "body": body}
    _resume(db_path, checkpointer, outbox, invoice_id, answer)
    return _decided(db_path, invoice_id)


def reject_claim(db_path: Path | str, checkpointer, invoice_id: int) -> dict:
    """Close an awaiting claim as `rejected`, with no email."""
    ref = reconciliation.invoice_reference(invoice_id)
    conn = db.connect(db_path)
    try:
        _check_awaiting(conn, invoice_id)
    finally:
        conn.close()
    node = _paused(db_path, checkpointer, invoice_id)
    if node == "send":
        raise DecisionRefused(f"claim {ref} is being sent; run approve to finish it")
    if node not in ("review", "close"):
        raise _no_pause(ref)
    _resume(db_path, checkpointer, None, invoice_id, {"action": review.REJECT} if node == "review" else None)
    return _decided(db_path, invoice_id)
