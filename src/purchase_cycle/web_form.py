"""Web form order subgraph: validate, match, store and reply.

A web form submission names a customer and up to 20 lines of product text and
quantity in sale units. Lines whose text is a catalog SKU or name are matched
without the model; the rest go to the model, one call per line.
"""

import re
import unicodedata
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from purchase_cycle import db
from purchase_cycle.llm import ModelClient

CHANNEL = "web_form"
STATUS = "received"
MAX_LINES = 20


class FormLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product: str = Field(min_length=1, description="Product as the customer typed it")
    quantity: int = Field(gt=0, strict=True, description="Quantity in catalog sale units")


class Submission(BaseModel):
    """One web form submission, as stored in the submission file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    submission_id: str = Field(min_length=1)
    customer_code: str = Field(min_length=1)
    lines: list[FormLine] = Field(min_length=1, max_length=MAX_LINES)


class WebFormState(TypedDict, total=False):
    submission: dict
    errors: list[str]
    customer: dict
    lines: list[dict]
    order_id: int | None
    reply: str


def match_key(text: str) -> str:
    """Comparison key that ignores case, accents, punctuation and extra spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def catalog_index(catalog: list) -> dict[str, str]:
    """Map the key of every catalog SKU and name to its SKU."""
    index = {}
    for row in catalog:
        index[match_key(row["sku"])] = row["sku"]
        index[match_key(row["name"])] = row["sku"]
    return index


def validation_errors(error: ValidationError) -> list[str]:
    return [f"field '{'.'.join(str(p) for p in e['loc']) or 'submission'}': {e['msg']}" for e in error.errors()]


def build_reply(customer: dict, submission: dict, order_id: int | None, stored: list[dict], lines: list[dict]) -> str:
    """Fixed reply template; every figure comes from the stored rows."""
    out = [f"Dear {customer['contact_name']},", ""]
    if order_id is not None:
        out.append(f"Thank you. Your web form order {submission['submission_id']} is registered as order {order_id}:")
        total = 0.0
        for row in stored:
            line_total = round(row["price_eur"] * row["quantity"], 2)
            total += line_total
            out.append(
                f"- {row['name']}: {row['quantity']} x {row['sale_unit']} at {row['price_eur']:.2f} EUR = {line_total:.2f} EUR"
            )
        out.append(f"Order total: {total:.2f} EUR")
    else:
        out.append(f"We could not register your web form order {submission['submission_id']}.")
    unmatched = [line for line in lines if line["sku"] is None]
    if unmatched:
        out += ["", "We could not find these products in our catalog, so they are not part of the order:"]
        out += [f'- "{line["product"]}" ({line["quantity"]})' for line in unmatched]
    out += ["", "Kind regards,", "Customer service"]
    return "\n".join(out)


def build_web_form_graph(client: ModelClient, db_path: Path | str, checkpointer=None, interrupt_after=None):
    conn = db.connect(db_path)
    try:
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()
    index = catalog_index(catalog)
    known_skus = {row["sku"] for row in catalog}

    def validate(state: WebFormState) -> WebFormState:
        try:
            submission = Submission.model_validate(state["submission"])
        except ValidationError as error:
            return {"errors": validation_errors(error)}
        conn = db.connect(db_path)
        try:
            row = conn.execute("SELECT * FROM customers WHERE code = ?", (submission.customer_code,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"errors": [f"field 'customer_code': unknown customer code '{submission.customer_code}'"]}
        return {"errors": [], "submission": submission.model_dump(), "customer": dict(row)}

    def match(state: WebFormState) -> WebFormState:
        submission = state["submission"]
        lines = []
        for n, line in enumerate(submission["lines"], start=1):
            sku = index.get(match_key(line["product"]))
            source = "deterministic"
            if sku is None:
                answer = client.extract(line["product"], case_id=f"{submission['submission_id']}-{n}")
                # A SKU the catalog does not hold is treated as no match.
                sku = answer.sku if answer.sku in known_skus else None
                source = "model"
            lines.append({"product": line["product"], "quantity": line["quantity"], "sku": sku, "source": source})
        return {"lines": lines}

    def store(state: WebFormState) -> WebFormState:
        matched = [line for line in state["lines"] if line["sku"] is not None]
        if not matched:
            return {"order_id": None}
        conn = db.connect(db_path)
        try:
            with conn:  # one transaction: the order and its lines, or nothing
                cursor = conn.execute(
                    "INSERT INTO orders (customer_code, channel, status) VALUES (?, ?, ?)",
                    (state["customer"]["code"], CHANNEL, STATUS),
                )
                order_id = cursor.lastrowid
                conn.executemany(
                    "INSERT INTO order_lines (order_id, sku, quantity) VALUES (?, ?, ?)",
                    [(order_id, line["sku"], line["quantity"]) for line in matched],
                )
        finally:
            conn.close()
        return {"order_id": order_id}

    def reply(state: WebFormState) -> WebFormState:
        stored = []
        if state["order_id"] is not None:
            conn = db.connect(db_path)
            try:
                stored = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT p.name, p.sale_unit, p.price_eur, l.quantity FROM order_lines l "
                        "JOIN products p USING (sku) WHERE l.order_id = ? ORDER BY l.id",
                        (state["order_id"],),
                    )
                ]
            finally:
                conn.close()
        text = build_reply(state["customer"], state["submission"], state["order_id"], stored, state["lines"])
        return {"reply": text}

    builder = StateGraph(WebFormState)
    builder.add_node("validate", validate)
    builder.add_node("match", match)
    builder.add_node("store", store)
    builder.add_node("reply", reply)
    builder.add_edge(START, "validate")
    builder.add_conditional_edges("validate", lambda s: END if s["errors"] else "match", ["match", END])
    builder.add_edge("match", "store")
    builder.add_edge("store", "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after, name="web_form_order")
