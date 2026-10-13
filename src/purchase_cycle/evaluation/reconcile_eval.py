"""Deterministic `reconciliation_matching` evaluation (phase 08, D5, D9, C13).

Each case is one versioned JSON file: a purchase order, the supplier documents with the hand-written answer the
extraction would give for each one (the test double `ScriptedExtractor` returns it, so no model is called), the scans
that bring the documents in, and the expected result and set of (SKU, type, amount) discrepancies, written by hand
from the matching rules (price tolerance 0.01 per unit, quantity tolerance 0, delivery checked against the invoice).

The runner seeds a temporary database per case, creates the order, runs `reconciliation.scan` once per scan step
(so the documents go through read, extract, link, match and store), then `reconcile list` and `reconcile show` through
the CLI functions (a `restart` case runs them in a new process). The grader reads the database and the printed
result. The critical-error counter compares the rows stored and the rows printed with the expected ones.
The gate is 100% case success and 0 critical errors.

Build the versioned case files with `uv run python -m purchase_cycle.evaluation.reconcile_eval`.
"""

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from purchase_cycle import cli, db, llm, reconcile_match, reconciliation
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer

SUITE = "reconciliation_matching"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "reconciliation_matching"
TAGS = (
    "perfect_match",
    "price_under_tolerance",
    "price_over_tolerance",
    "quantity_above_order",
    "quantity_below_order",
    "invoiced_above_delivered",
    "extra_line",
    "missing_line",
    "price_and_quantity_one_line",
    "several_types",
    "no_delivery_note",
    "unknown_order",
    "wrong_supplier",
    "duplicate_scan",
    "restart",
)
MINIMUMS = {"cases": 40, **dict.fromkeys(TAGS, 3)}
TARGET = 1.0  # deterministic: every case must end as scripted
UNIT = "case"
CRITICAL_KINDS = (
    "difference_not_reported",
    "difference_invented",
    "wrong_type",
    "two_reconciliations",
    "reconciliation_for_unlinked_document",
    "model_call_during_matching",
)
INITIAL_STATE = {
    "seed": "db.seed: catalog, stock, customers, suppliers and the clock; one issued purchase order PO-0001"
}
NAMES = {
    "SUP-001": "Suministros Medicos Iberia S.L.",
    "SUP-002": "Distribuciones Sanitarias del Norte S.A.",
    "SUP-003": "Material Clinico Levante S.L.",
}
PRODUCTS = {
    "GLV-NIT-XS": "Nitrile examination gloves, powder-free, size XS",
    "GLV-NIT-S": "Nitrile examination gloves, powder-free, size S",
    "GLV-NIT-M": "Nitrile examination gloves, powder-free, size M",
}
SKU_OF = {name: sku for sku, name in PRODUCTS.items()}
XS, S, M = "GLV-NIT-XS", "GLV-NIT-S", "GLV-NIT-M"

# ---------------------------------------------------------------------------------------------------------------
# The test double: the extraction answer of each document text, with a count of the calls made.


class ScriptedExtractor:
    """Stands in for the model client: returns the stored answer of a document text, counts every call."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.calls = 0

    def extract(self, text: str, case_id: str | None = None):
        self.calls += 1
        if text not in self.answers:
            raise llm.MissingRecording(f"No scripted answer for the document: {text[:60]!r}")
        answer = json.loads(json.dumps(self.answers[text]))
        return SimpleNamespace(model_dump=lambda: answer)


_ACTIVE: list[ScriptedExtractor] = []  # the double of the case being run (lets a test call it from inside matching)

# ---------------------------------------------------------------------------------------------------------------
# The cases, written by hand. Amounts follow the rules: price = (invoiced - ordered) * invoiced quantity,
# quantity = (invoiced - expected) * ordered price, extra line = its total, missing line = ordered quantity * price.


def order(supplier, sku, quantity, unit_price) -> dict:
    return {"supplier": supplier, "sku": sku, "quantity": quantity, "unit_price": unit_price}


OA = order("SUP-001", XS, 10, 4.10)
OB = order("SUP-002", S, 25, 3.50)
OC = order("SUP-003", M, 40, 1.20)


def ln(sku, quantity, price=None) -> dict:
    total = None if price is None else round(quantity * price, 2)
    return {"product": PRODUCTS[sku], "quantity": quantity, "unit_price": price, "line_total": total}


def raw(text, quantity, price) -> dict:
    return {"product": text, "quantity": quantity, "unit_price": price, "line_total": round(quantity * price, 2)}


def inv(o, lines, supplier=None, po="PO-0001", number="INV-77") -> dict:
    code = supplier or o["supplier"]
    return {"kind": "invoice", "number": number, "po_reference": po, "issue_date": "2026-10-20", "currency": "EUR",
            "supplier_text": NAMES.get(code, code), "lines": lines}  # fmt: skip


def note(o, lines, supplier=None, po="PO-0001") -> dict:
    code = supplier or o["supplier"]
    return {"kind": "delivery_note", "number": "DN-5", "po_reference": po, "issue_date": "2026-10-21", "currency": None,
            "supplier_text": NAMES.get(code, code), "lines": lines}  # fmt: skip


def delivered(sku, quantity) -> dict:
    return ln(sku, quantity)


INV, N = "a-invoice.txt", "b-note.txt"
LINKED = ("linked", None)


def case(o, files, scans=None, recs=None, docs=None, progress=None, restart=False) -> dict:
    return {"order": o, "files": files, "scans": scans or [list(files)], "recs": recs or {}, "docs": docs or {},
            "progress": progress, "restart": restart}  # fmt: skip


def pair(o, invoice_lines, note_lines, recs, **kw):
    """An invoice and its delivery note scanned together."""
    return case(o, {INV: inv(o, invoice_lines), N: note(o, note_lines)}, recs={INV: recs}, **kw)


def only(o, invoice_lines, recs, **kw):
    """An invoice with no delivery note yet."""
    return case(o, {INV: inv(o, invoice_lines)}, recs={INV: recs}, **kw)


MATCHED = ("matched", [])
CASES = [
    # perfect match
    pair(OA, [ln(XS, 10, 4.10)], [delivered(XS, 10)], MATCHED),
    pair(OB, [ln(S, 25, 3.50)], [delivered(S, 25)], MATCHED),
    pair(OC, [ln(M, 40, 1.20)], [delivered(M, 40)], MATCHED),
    case(OA, {"a-note.txt": note(OA, [delivered(XS, 10)]), "b-invoice.txt": inv(OA, [ln(XS, 10, 4.10)])},
         recs={"b-invoice.txt": MATCHED}),  # fmt: skip
    pair(OA, [ln(XS, 5, 4.10), ln(XS, 5, 4.10)], [delivered(XS, 10)], MATCHED),
    # price difference under the tolerance (0.01 per unit) is no difference
    pair(OA, [ln(XS, 10, 4.11)], [delivered(XS, 10)], MATCHED),
    pair(OA, [ln(XS, 10, 4.09)], [delivered(XS, 10)], MATCHED),
    pair(OB, [ln(S, 25, 3.51)], [delivered(S, 25)], MATCHED),
    pair(OC, [ln(M, 40, 1.21)], [delivered(M, 40)], MATCHED),
    # price difference over the tolerance
    pair(OA, [ln(XS, 10, 4.12)], [delivered(XS, 10)], ("discrepancies", [(XS, "price", 0.20)])),
    pair(OA, [ln(XS, 10, 4.50)], [delivered(XS, 10)], ("discrepancies", [(XS, "price", 4.00)])),
    pair(OA, [ln(XS, 10, 3.90)], [delivered(XS, 10)], ("discrepancies", [(XS, "price", -2.00)])),
    pair(OB, [ln(S, 25, 3.60)], [delivered(S, 25)], ("discrepancies", [(S, "price", 2.50)])),
    # quantity above the order
    pair(OA, [ln(XS, 12, 4.10)], [delivered(XS, 12)], ("discrepancies", [(XS, "quantity", 8.20)])),
    pair(OA, [ln(XS, 15, 4.10)], [delivered(XS, 15)], ("discrepancies", [(XS, "quantity", 20.50)])),
    pair(OB, [ln(S, 30, 3.50)], [delivered(S, 30)], ("discrepancies", [(S, "quantity", 17.50)])),
    # quantity below the order
    pair(OA, [ln(XS, 8, 4.10)], [delivered(XS, 8)], ("discrepancies", [(XS, "quantity", -8.20)])),
    pair(OA, [ln(XS, 5, 4.10)], [delivered(XS, 5)], ("discrepancies", [(XS, "quantity", -20.50)])),
    pair(OB, [ln(S, 20, 3.50)], [delivered(S, 20)], ("discrepancies", [(S, "quantity", -17.50)])),
    # invoiced above delivered with the order met
    pair(OA, [ln(XS, 10, 4.10)], [delivered(XS, 8)], ("discrepancies", [(XS, "quantity", 8.20)])),
    pair(OA, [ln(XS, 10, 4.10)], [delivered(XS, 6)], ("discrepancies", [(XS, "quantity", 16.40)])),
    pair(OB, [ln(S, 25, 3.50)], [delivered(S, 20)], ("discrepancies", [(S, "quantity", 17.50)])),
    # extra line
    pair(OA, [ln(XS, 10, 4.10), ln(S, 2, 3.50)], [delivered(XS, 10)], ("discrepancies", [(S, "extra_line", 7.00)])),
    pair(OA, [ln(XS, 10, 4.10), raw("Freight charge", 1, 5.00)], [delivered(XS, 10)],
         ("discrepancies", [(None, "extra_line", 5.00)])),  # fmt: skip
    pair(OB, [ln(S, 25, 3.50), ln(XS, 3, 4.10)], [delivered(S, 25)], ("discrepancies", [(XS, "extra_line", 12.30)])),
    # missing line (the invoice states other products only, so each also holds an extra line)
    pair(OA, [ln(S, 4, 3.50)], [delivered(XS, 10)], ("discrepancies", [(S, "extra_line", 14.00), (XS, "missing_line", 41.00)])),
    pair(OA, [raw("Courier fee", 1, 9.90)], [delivered(XS, 10)],
         ("discrepancies", [(None, "extra_line", 9.90), (XS, "missing_line", 41.00)])),  # fmt: skip
    pair(OB, [ln(M, 10, 1.20)], [delivered(S, 25)], ("discrepancies", [(M, "extra_line", 12.00), (S, "missing_line", 87.50)])),
    # price and quantity on one line
    pair(OA, [ln(XS, 12, 4.50)], [delivered(XS, 12)], ("discrepancies", [(XS, "price", 4.80), (XS, "quantity", 8.20)])),
    pair(OA, [ln(XS, 8, 4.00)], [delivered(XS, 8)], ("discrepancies", [(XS, "price", -0.80), (XS, "quantity", -8.20)])),
    pair(OB, [ln(S, 30, 3.40)], [delivered(S, 30)], ("discrepancies", [(S, "price", -3.00), (S, "quantity", 17.50)])),
    pair(OA, [ln(XS, 10, 4.50)], [delivered(XS, 8)], ("discrepancies", [(XS, "price", 4.00), (XS, "quantity", 8.20)])),
    # several types in one invoice
    pair(OA, [ln(XS, 10, 4.50), ln(S, 2, 3.50)], [delivered(XS, 10)],
         ("discrepancies", [(XS, "price", 4.00), (S, "extra_line", 7.00)])),  # fmt: skip
    pair(OA, [ln(XS, 12, 4.10), raw("Freight charge", 1, 5.00)], [delivered(XS, 12)],
         ("discrepancies", [(XS, "quantity", 8.20), (None, "extra_line", 5.00)])),  # fmt: skip
    pair(OA, [ln(XS, 8, 4.50), ln(M, 1, 2.00)], [delivered(XS, 8)],
         ("discrepancies", [(XS, "price", 3.20), (XS, "quantity", -8.20), (M, "extra_line", 2.00)])),  # fmt: skip
    # no delivery note yet: the price, quantity and line checks are already reported
    only(OA, [ln(XS, 10, 4.10)], ("awaiting_delivery_note", [])),
    only(OA, [ln(XS, 10, 4.50)], ("awaiting_delivery_note", [(XS, "price", 4.00)])),
    only(OA, [ln(XS, 12, 4.10)], ("awaiting_delivery_note", [(XS, "quantity", 8.20)])),
    only(OA, [ln(XS, 10, 4.10), ln(S, 2, 3.50)], ("awaiting_delivery_note", [(S, "extra_line", 7.00)])),
    # the delivery note arrives in a second scan and updates the same reconciliation
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 10)])}, scans=[[INV], [N]],
         recs={INV: MATCHED}, progress=[{INV: "awaiting_delivery_note"}, {INV: "matched"}]),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.50)]), N: note(OA, [delivered(XS, 10)])}, scans=[[INV], [N]],
         recs={INV: ("discrepancies", [(XS, "price", 4.00)])},
         progress=[{INV: "awaiting_delivery_note"}, {INV: "discrepancies"}]),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 8)])}, scans=[[INV], [N]],
         recs={INV: ("discrepancies", [(XS, "quantity", 8.20)])},
         progress=[{INV: "awaiting_delivery_note"}, {INV: "discrepancies"}]),  # fmt: skip
    # unknown order
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)], po="PO-0099")}, docs={INV: ("unlinked", "unknown_order")}),
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 10)], po="PO-0042")},
         recs={INV: ("awaiting_delivery_note", [])}, docs={N: ("unlinked", "unknown_order")}),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)], po="PO-0007"), N: note(OA, [delivered(XS, 10)], po="PO-0007")},
         docs={INV: ("unlinked", "unknown_order"), N: ("unlinked", "unknown_order")}),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)], po=None)}, docs={INV: ("unlinked", "no_reference")}),
    # wrong supplier
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)], supplier="SUP-002")}, docs={INV: ("unlinked", "supplier_mismatch")}),
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 10)], supplier="SUP-003")},
         recs={INV: ("awaiting_delivery_note", [])}, docs={N: ("unlinked", "supplier_mismatch")}),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)], supplier="Unknown Company Ltd")},
         docs={INV: ("unlinked", "supplier_mismatch")}),  # fmt: skip
    # a duplicate scan: the same documents again add nothing and call no model
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 10)])}, scans=[[INV, N], [], []],
         recs={INV: MATCHED}),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.50)]), N: note(OA, [delivered(XS, 10)])}, scans=[[INV, N], [], []],
         recs={INV: ("discrepancies", [(XS, "price", 4.00)])}),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 10)]),
              "c-copy.txt": inv(OA, [ln(XS, 10, 4.10)])}, recs={INV: MATCHED}),  # fmt: skip
    case(OA, {INV: inv(OA, [ln(XS, 10, 4.10)]), N: note(OA, [delivered(XS, 10)]),
              "c-second.txt": inv(OA, [ln(XS, 10, 4.10)], number="INV-78")},
         recs={INV: MATCHED}, docs={"c-second.txt": ("unlinked", "duplicate")}),  # fmt: skip
    # a restart between the scan and the show: `list` and `show` run in a new process
    pair(OA, [ln(XS, 10, 4.10)], [delivered(XS, 10)], MATCHED, restart=True),
    pair(OA, [ln(XS, 10, 4.50)], [delivered(XS, 10)], ("discrepancies", [(XS, "price", 4.00)]), restart=True),
    only(OA, [ln(XS, 10, 4.10)], ("awaiting_delivery_note", []), restart=True),
    pair(OA, [ln(XS, 8, 4.50), ln(M, 1, 2.00)], [delivered(XS, 8)],
         ("discrepancies", [(XS, "price", 3.20), (XS, "quantity", -8.20), (M, "extra_line", 2.00)]), restart=True),
]  # fmt: skip


def render(answer: dict) -> str:
    """The text of a document: a short readable rendering of its answer (identical answers give identical text)."""
    rows = [f"{answer['kind'].replace('_', ' ').title()} {answer['number'] or ''}".strip()]
    rows.append(f"Order reference {answer['po_reference'] or 'none'}  Supplier {answer['supplier_text']}")
    for line in answer["lines"]:
        price = "" if line["unit_price"] is None else f"  {line['unit_price']:.2f}"
        rows.append(f"{line['product']}  {line['quantity']}{price}")
    return "\n".join(rows) + "\n"


def derive_tags(item: dict) -> list[str]:
    """The coverage tags follow from the documents and the expected results: the files hold what the case really is."""
    o, exp = item["order"], item["expected"]
    tags, texts, seen = set(), [], set()
    recs = exp["reconciliations"]
    rows = [(name, tuple(d)) for name, rec in recs.items() for d in rec["discrepancies"]]
    for name, rec in recs.items():
        types = [d[1] for d in rec["discrepancies"]]
        answer = item["documents"][name]["answer"]
        for line in answer["lines"]:
            price = line["unit_price"]
            if SKU_OF.get(line["product"]) == o["sku"] and price is not None:
                gap = round(abs(price - o["unit_price"]), 6)
                if 0 < gap <= reconcile_match.PRICE_TOLERANCE and "price" not in types:
                    tags.add("price_under_tolerance")
        if rec["result"] == "matched" and all(
            ln_["unit_price"] == o["unit_price"] for ln_ in answer["lines"] if SKU_OF.get(ln_["product"]) == o["sku"]
        ):
            tags.add("perfect_match")
        if len(set(types)) >= 2:
            tags.add("several_types")
        for sku in {d[0] for d in rec["discrepancies"]}:
            if {"price", "quantity"} <= {d[1] for d in rec["discrepancies"] if d[0] == sku}:
                tags.add("price_and_quantity_one_line")
    for name, (sku, kind, _amount) in rows:
        found = sum(
            line["quantity"]
            for line in item["documents"][name]["answer"]["lines"]
            if SKU_OF.get(line["product"]) == sku
        )
        if kind == "quantity" and found > o["quantity"]:
            tags.add("quantity_above_order")
        elif kind == "quantity" and found < o["quantity"]:
            tags.add("quantity_below_order")
        elif kind == "quantity":
            tags.add("invoiced_above_delivered")
        elif kind in ("price", "extra_line", "missing_line"):
            if kind != "price":
                tags.add(kind)
            else:
                tags.add("price_over_tolerance")
    if any(result == "awaiting_delivery_note" for step in exp["progress"] for result in step.values()):
        tags.add("no_delivery_note")
    reasons = {reason for _, reason in exp["documents"].values()}
    if "unknown_order" in reasons:
        tags.add("unknown_order")
    if "supplier_mismatch" in reasons:
        tags.add("wrong_supplier")
    for number, scan in enumerate(item["scans"]):
        fresh = [item["documents"][n]["text"] for n in scan if item["documents"][n]["text"] not in seen]
        if (
            (number and not fresh)
            or len(fresh) != len(set(fresh))
            or len(scan) != len({item["documents"][n]["text"] for n in scan})
        ):
            tags.add("duplicate_scan")
        seen |= {item["documents"][n]["text"] for n in scan}
        texts += scan
    if item["restart"]:
        tags.add("restart")
    return [t for t in TAGS if t in tags]


def _amounts(rows) -> list[list]:
    return [[sku, kind, round(amount, 2)] for sku, kind, amount in rows]


def build_items() -> list[dict]:
    items = []
    for n, spec in enumerate(CASES, start=1):
        documents = {name: {"text": render(answer), "answer": answer} for name, answer in spec["files"].items()}
        names = [name for scan in spec["scans"] for name in scan]
        invoices = [name for name in dict.fromkeys(names) if documents[name]["answer"]["kind"] == "invoice"]
        first = {}
        for name in names:
            first.setdefault(documents[name]["text"], name)  # a copy under another name is the same stored document
        final = {}
        for name in invoices:
            canon = first[documents[name]["text"]]
            final[name] = spec["recs"][canon][0] if canon in spec["recs"] else None
        progress = spec["progress"]
        if progress is None:
            progress = []
            for number in range(len(spec["scans"])):
                present = set(name for scan in spec["scans"][: number + 1] for name in scan)
                progress.append({name: result for name, result in final.items() if name in present})
        expected = {
            "documents": {name: list(spec["docs"].get(name, LINKED)) for name in dict.fromkeys(names)},
            "reconciliations": {
                name: {"result": result, "discrepancies": _amounts(rows)}
                for name, (result, rows) in spec["recs"].items()
            },
            "progress": progress,
            "model_calls": len({documents[name]["text"] for name in names}),
        }
        item = {"id": f"RM-{n:03d}", "dataset_version": DATASET_VERSION, "initial_state": INITIAL_STATE,
                "order": spec["order"], "documents": documents, "scans": spec["scans"], "restart": spec["restart"],
                "expected": expected}  # fmt: skip
        item["tags"] = derive_tags(item)
        items.append(item)
    return items


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("RM-*.json"))]


# ---------------------------------------------------------------------------------------------------------------
# The runner: product path on a temporary database.


def _create_order(db_path: Path, o: dict) -> None:
    conn = db.connect(db_path)
    try:
        db.seed(conn)
        conn.execute(
            "INSERT INTO quote_requests (id, sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
            "VALUES (1, ?, ?, 'quote-1', 'ordered', 't', 't', 't')",
            (o["sku"], o["quantity"]),
        )
        conn.execute(
            "INSERT INTO purchase_orders (id, request_id, supplier_code, sku, quantity, unit_price, currency, total, "
            "expected_delivery, issued_at, status) VALUES (1, 1, ?, ?, ?, ?, 'EUR', ?, '2026-10-20', 't', 'issued')",
            (o["supplier"], o["sku"], o["quantity"], o["unit_price"], round(o["quantity"] * o["unit_price"], 2)),
        )
        conn.commit()
    finally:
        conn.close()


def _command(db_path: Path, *words: str, new_process: bool = False) -> tuple[int, str, str]:
    argv = ["--db", str(db_path), "reconcile", *words]
    if new_process:
        done = subprocess.run(
            [sys.executable, "-m", "purchase_cycle.cli", *argv], capture_output=True, text=True, timeout=120
        )
        return done.returncode, done.stdout, done.stderr
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


SHOWN_ROW = re.compile(r"^\s+(price|quantity|extra_line|missing_line)\s+sku=(\S+).*?amount=(-?[\d.]+|-)$")


def parse_show(output: str) -> dict | None:
    """The printed result and discrepancy triples of `reconcile show`."""
    head = re.search(r"result=(\w+)", output)
    if head is None:
        return None
    rows = []
    for line in output.splitlines():
        found = SHOWN_ROW.match(line)
        if found:
            kind, sku, amount = found.groups()
            rows.append([None if sku == "-" else sku, kind, None if amount == "-" else round(float(amount), 2)])
    return {"result": head.group(1), "discrepancies": rows}


def parse_list(output: str) -> dict | None:
    head = re.search(
        r"reconciliations: (\d+)\s+matched=(\d+)\s+discrepancies=(\d+)\s+awaiting_delivery_note=(\d+)", output
    )
    if head is None:
        return None
    count, matched, differences, awaiting = map(int, head.groups())
    return {"count": count, "matched": matched, "discrepancies": differences, "awaiting_delivery_note": awaiting}


def _measure(item: dict, paths: dict, double: ScriptedExtractor, progress: list[dict], new_total: int) -> dict:
    """Everything the grader reads: the stored documents and reconciliations, the printed show and list."""
    conn = db.connect(paths["db"])
    try:
        docs = {}
        for name in item["documents"]:
            path = paths["docs"] / name
            if path.exists():
                row = reconciliation.stored_document(conn, reconciliation.content_hash(path))
                docs[name] = {"id": row["id"], "status": row["status"], "reason": row["reason"]}
        by_id = {}
        for name, d in docs.items():
            by_id.setdefault(d["id"], name)
        recs = []
        for row in conn.execute("SELECT * FROM reconciliations ORDER BY id"):
            found = conn.execute(
                "SELECT sku, type, amount FROM discrepancies WHERE reconciliation_id = ? ORDER BY id", (row["id"],)
            )
            invoice = conn.execute(
                "SELECT status FROM supplier_documents WHERE id = ?", (row["invoice_id"],)
            ).fetchone()
            recs.append({"invoice_id": row["invoice_id"], "invoice": by_id.get(row["invoice_id"]),
                         "invoice_status": invoice["status"], "result": row["result"],
                         "discrepancies": [[r["sku"], r["type"], round(r["amount"], 2)] for r in found]})  # fmt: skip
    finally:
        conn.close()
    restart = item["restart"]
    code, out, _ = _command(paths["db"], "list", new_process=restart)
    listed = parse_list(out) if code == 0 else None
    shown = {}
    for rec in recs:
        code, out, _ = _command(
            paths["db"], "show", reconciliation.invoice_reference(rec["invoice_id"]), new_process=restart
        )
        shown[rec["invoice"]] = parse_show(out) if code == 0 else None
    return {"docs": docs, "recs": recs, "shown": shown, "listed": listed, "progress": progress,
            "calls": double.calls, "new_documents": new_total}  # fmt: skip


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    docs_dir = folder / "docs"
    docs_dir.mkdir(parents=True)
    paths = {"db": folder / "case.db", "docs": docs_dir, "checkpoints": folder / "cp.sqlite"}
    _create_order(paths["db"], item["order"])
    double = ScriptedExtractor({reconciliation.model_text(d["text"]): d["answer"] for d in item["documents"].values()})
    _ACTIVE.append(double)
    progress, new_total = [], 0
    try:
        for scan in item["scans"]:
            for name in scan:
                (docs_dir / name).write_text(item["documents"][name]["text"], encoding="utf-8", newline="\n")
            results = reconciliation.scan(paths["db"], sqlite_checkpointer(paths["checkpoints"]), docs_dir, double)
            progress.append({r["file"]: r["result"] for r in results if r["kind"] == "invoice"})
            new_total += sum(r["new"] for r in results)
        final = _measure(item, paths, double, progress, new_total)
    finally:
        _ACTIVE.pop()
    expected = item["expected"]
    correct = _correct(expected, final)
    return {"id": item["id"], "tags": item["tags"], "expected": expected, "got": _got(final), "correct": correct,
            "critical": _errors(item, final)}  # fmt: skip


def _rows(rows) -> list:
    return sorted(([sku, kind, amount] for sku, kind, amount in rows), key=repr)


def _got(final: dict) -> dict:
    return {
        "documents": {n: [d["status"], d["reason"]] for n, d in final["docs"].items()},
        "reconciliations": {
            r["invoice"]: {"result": r["result"], "discrepancies": r["discrepancies"]} for r in final["recs"]
        },
        "progress": final["progress"],
        "model_calls": final["calls"],
    }


def _correct(expected: dict, final: dict) -> bool:
    """The case passes when documents, results and the set of (SKU, type, amount) match in the database and the print."""
    got = _got(final)
    if got["documents"] != expected["documents"] or got["progress"] != expected["progress"]:
        return False
    if got["model_calls"] != expected["model_calls"] or len(final["recs"]) != len(expected["reconciliations"]):
        return False
    want = expected["reconciliations"]
    if {r["invoice"] for r in final["recs"]} != set(want):
        return False
    for rec in final["recs"]:
        goal = want[rec["invoice"]]
        shown = final["shown"].get(rec["invoice"])
        if rec["result"] != goal["result"] or _rows(rec["discrepancies"]) != _rows(goal["discrepancies"]):
            return False
        if (
            shown is None
            or shown["result"] != goal["result"]
            or _rows(shown["discrepancies"]) != _rows(goal["discrepancies"])
        ):
            return False
    counts = Counter(goal["result"] for goal in want.values())
    listed = final["listed"]
    return listed is not None and listed == {
        "count": len(want),
        "matched": counts["matched"],
        "discrepancies": counts["discrepancies"],
        "awaiting_delivery_note": counts["awaiting_delivery_note"],
    }


def compare_rows(expected, actual) -> tuple[int, int, int]:
    """(not reported, invented, wrong type) between two lists of (SKU, type, amount); the amounts are not compared."""
    skus = {row[0] for row in expected} | {row[0] for row in actual}
    missed = invented = wrong = 0
    for sku in skus:
        want = Counter(row[1] for row in expected if row[0] == sku)
        have = Counter(row[1] for row in actual if row[0] == sku)
        lacking, extra = sum((want - have).values()), sum((have - want).values())
        swapped = min(lacking, extra)
        wrong += swapped
        missed += lacking - swapped
        invented += extra - swapped
    return missed, invented, wrong


def _errors(item: dict, final: dict) -> dict[str, int]:
    """The critical errors of one case, from the stored rows and from the printed ones (the larger count of each)."""
    expected = item["expected"]
    want = expected["reconciliations"]
    errors = dict.fromkeys(CRITICAL_KINDS, 0)
    per_invoice = Counter(r["invoice_id"] for r in final["recs"])
    errors["two_reconciliations"] = sum(n - 1 for n in per_invoice.values() if n > 1)
    first = {}
    for rec in final["recs"]:
        first.setdefault(rec["invoice"], rec)
        unlinked = (
            rec["invoice_status"] != "linked" or expected["documents"].get(rec["invoice"], [None])[0] == "unlinked"
        )
        errors["reconciliation_for_unlinked_document"] += int(unlinked)
    for name, goal in want.items():
        sources = [
            first[name]["discrepancies"] if name in first else None,
            (final["shown"].get(name) or {}).get("discrepancies"),
        ]
        counts = [compare_rows(goal["discrepancies"], rows or []) for rows in sources]
        for index, kind in enumerate(("difference_not_reported", "difference_invented", "wrong_type")):
            errors[kind] += max(c[index] for c in counts)
    errors["model_call_during_matching"] = max(0, final["calls"] - expected["model_calls"])
    return errors


def critical_totals(results: list[dict]) -> dict[str, int]:
    return {kind: sum(r["critical"][kind] for r in results) for kind in CRITICAL_KINDS}


def print_critical(errors: dict[str, int], n: int) -> None:
    """The critical-error row: a count gated at 0 with no averaging, then the count per kind."""
    total = sum(errors.values())
    target, met = target_cells(total, 0, "<=")
    print(
        f"{'critical_errors':<26}{total:<8}{'-':<16}{n:<6}{UNIT:<16}{'=0':<9}{met:<12}{'0':<11}"
        f"{'PASS' if total == 0 else 'FAIL'}"
    )
    print("  by kind: " + "  ".join(f"{kind}={errors[kind]}" for kind in CRITICAL_KINDS))


def critical_gate(errors: dict[str, int], results: list[dict]) -> int:
    """Exit 1 when any case holds a critical error, naming each one."""
    if not sum(errors.values()):
        return 0
    for r in results:
        found = {kind: c for kind, c in r["critical"].items() if c}
        if found:
            print(f"  {r['id']}  critical errors {found}")
    print(f"GATE FAILED: critical_errors {sum(errors.values())} above threshold 0")
    return 1


def evaluate(
    mode: str, split: str, *, dataset_dir: Path = DATASET_DIR, set_baseline: bool = False, workers: int = 1
) -> int:
    """Run every case and print the report; exit 1 below 100% success or with a critical error."""
    items = load_dataset(dataset_dir)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = [run_item(item, Path(tmp)) for item in items]
    s = _rate(results)
    # At 100% success the zero-event rate is the failed cases, so its Wilson upper bound follows the row.
    failed = zero_event_note(_rate([{"correct": not r["correct"]} for r in results]))
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    errors = critical_totals(results)
    print(f"{SUITE}  mode={mode}  cases={len(results)}  (deterministic: scripted extraction answers, no model call, no split, no baseline)")  # fmt: skip
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    print(
        f"{'case_success':<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNIT:<16}{target:<9}{met:<12}"
        f"{_pct(TARGET):<11}{gate}" + zero_event_note(s) + (f"  failed cases{failed}" if failed else "")
    )
    print_critical(errors, len(results))
    print("per tag:")
    for tag in TAGS:
        rows = [r for r in results if tag in r["tags"]]
        if rows:
            g = _rate(rows)
            print(f"  {tag:<28} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        print(f"  {r['id']}  expected {r['expected']}  got {r['got']}")
    code = 0
    if failures:
        print(f"GATE FAILED: case_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        code = 1
    return max(code, critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} reconciliation matching cases -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
