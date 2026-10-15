"""Deterministic `goods_receipt` evaluation (phase 10, D8, C11).

Each scenario is one versioned JSON file: the starting stock, the customer orders, the supplier delivery notes as
extracted rows (with their purchase order, invoice reconciliation and claim, no model) and a script of `run`, `scan`,
`quotes_scan` and `crash` steps. A `run` goes through `receipts_cli.cmd_receipts_run` (a `new_process` run through
`python -m purchase_cycle.cli`), a `crash` through `receipts.run_receipts(..., interrupt_after=[node])` and the next
`run` resumes it, a `scan` inserts a later delivery note with its reconciliation and a `quotes_scan` reads
`quotes.shortfalls`. A model client double that counts and fails on any call is installed for every scenario.

The expected final state and the printed lines are computed by `_simulate`, a small simulator of the written rules
(D1 to D5) that shares no code with `receipts.py`, and are stored in the scenario file. The grader reads the database,
the delivery note folder, the `failures` table and the printed output. The critical-error counter checks the final
state against an oracle apart from `receipts`. The gate is 100% success and 0 critical errors.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.goods_receipt_eval`.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from purchase_cycle import db, llm, quotes, receipts, receipts_cli
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer

SUITE = "goods_receipt"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "goods_receipt"
OPS = ("run", "scan", "quotes_scan", "crash")
TAGS = (
    "matched_received", "discrepancies_received", "claim_status", "no_receipt_awaiting_invoice",
    "no_receipt_unlinked", "no_receipt_no_invoice", "later_scan", "short_delivery", "shortfall_scan",
    "over_delivery", "sku_not_on_po", "parked_unknown_sku", "parked_fractional", "serve_in_order",
    "large_skipped", "covered_before_receipt", "multi_line_short", "rerun", "crash_resume", "restart",
)  # fmt: skip
# C11 minimums: scenarios in all and per coverage tag.
MINIMUMS = {"scenarios": 30, **dict.fromkeys(TAGS, 2)}
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "negative_stock",
    "stock_change_mismatch",
    "receipt_repeated_or_not_candidate",
    "order_served_twice_or_two_notes",
    "served_not_covered",
    "note_lines_differ",
    "order_out_of_arrival_order",
    "file_row_mismatch",
    "model_call",
)
SKUS = [p.sku for p in PRODUCTS[1:6]]
A, B, C, D, E = SKUS
X = A  # the SKU the critical-error injections of the tests change
CUST = [c.code for c in CUSTOMERS[:3]]
UNKNOWN_TEXT = "Unknown item"
NOTES_MARK = "<notes>"


# Builders of the notes, orders, steps and scenarios.


def N(result="matched", lines=(), po=None, claim=None, later=False) -> dict:
    """A supplier delivery note: result is matched, discrepancies, awaiting (an invoice with no note yet),
    unlinked (a document no invoice references) or no_invoice (a linked note with no invoice)."""
    if po is None:
        po = (lines[0][0] or A, lines[0][1]) if lines else (A, 1)
    return {"result": result, "lines": [list(x) for x in lines], "po": list(po), "claim": claim, "later": later}


def ORD(customer: int, *lines) -> dict:
    return {"customer": CUST[customer - 1], "lines": [list(x) for x in lines]}


RUN = {"op": "run"}
RUNP = {"op": "run", "new_process": True}
QS = {"op": "quotes_scan"}


def SCAN(*numbers) -> dict:
    return {"op": "scan", "notes": [f"DN-{n}" for n in numbers]}


def CRASH(after: str) -> dict:
    return {"op": "crash", "after": after}


def S(stock=None, orders=(), notes=(), steps=(RUN,), what="") -> dict:
    notes = [{**n, "number": f"DN-{i}"} for i, n in enumerate(notes, 1)]
    return {
        "stock": dict(stock or {}), "orders": list(orders), "notes": notes, "steps": [dict(s) for s in steps],
        "description": what,
    }  # fmt: skip


SCENARIOS = [
    S({}, [ORD(1, (A, 10))], [N("matched", [(A, 10)])], what="one matched note serves the order that waited"),
    S({}, [], [N("matched", [(B, 20)])], what="a matched note with no waiting order only adds stock"),
    S({}, [ORD(1, (A, 10))], [N("discrepancies", [(A, 10)])], what="a discrepancies note is received with no claim"),
    S({}, [ORD(1, (A, 10))], [N("discrepancies", [(A, 10)], claim="awaiting_approval")], what="claim awaiting approval"),
    S({}, [ORD(1, (B, 4))], [N("discrepancies", [(B, 4)], claim="sent")], what="claim sent does not hold the goods"),
    S({}, [ORD(1, (C, 7))], [N("discrepancies", [(C, 7)], claim="rejected")], what="claim rejected"),
    S({}, [ORD(1, (A, 10))], [N("awaiting", [], po=(A, 10))], what="an invoice awaiting its delivery note receives nothing"),
    S({}, [ORD(1, (A, 10))], [N("awaiting", [], po=(A, 10)), N("matched", [(B, 5)])], what="only the matched note is received"),
    S({}, [ORD(1, (A, 10))], [N("unlinked", [(A, 10)])], what="an unlinked document receives nothing"),
    S({}, [], [N("unlinked", [(A, 10)]), N("matched", [(C, 3)])], what="an unlinked document next to a matched note"),
    S({}, [ORD(1, (A, 10))], [N("no_invoice", [(A, 10)])], what="a linked note with no invoice receives nothing"),
    S({}, [], [N("no_invoice", [(D, 10)]), N("discrepancies", [(D, 6)])], what="a note with no invoice next to a received one"),
    S({}, [ORD(1, (A, 10))], [N("matched", [(A, 10)], later=True)], [RUN, SCAN(1), RUN], what="a note linked by a later scan"),
    S({}, [ORD(1, (B, 5))], [N("matched", [(B, 5)]), N("discrepancies", [(C, 8)], later=True)], [RUN, SCAN(2), RUN], what="a second note arrives later"),
    S({}, [ORD(1, (A, 100))], [N("matched", [(A, 60)], po=(A, 100))], what="a short delivery leaves the order skipped"),
    S({}, [ORD(1, (A, 100))], [N("matched", [(A, 60)], po=(A, 100))], [QS, RUN, QS], what="a short delivery leaves a shortfall"),
    S({}, [ORD(1, (A, 100))], [N("matched", [(A, 100)])], [QS, RUN, QS], what="an exact delivery leaves no shortfall"),
    S({}, [ORD(1, (A, 30)), ORD(2, (A, 30))], [N("matched", [(A, 70)], po=(A, 50))], what="an over-delivery serves two orders"),
    S({}, [], [N("matched", [(E, 70)], po=(E, 50))], what="an over-delivery adds the delivered units"),
    S({}, [ORD(1, (B, 5))], [N("matched", [(A, 50), (B, 8)], po=(A, 50))], what="a SKU not on the purchase order is received"),
    S({}, [], [N("discrepancies", [(B, 10)], po=(A, 50))], what="the only line is not on the purchase order"),
    S({}, [ORD(1, (A, 5))], [N("matched", [(None, 5)], po=(A, 5)), N("matched", [(A, 5)])], what="an unknown SKU is parked, the next note is received"),
    S({}, [], [N("matched", [(A, 5), (None, 2)], po=(A, 5))], what="a note with one unknown SKU adds nothing"),
    S({}, [ORD(1, (A, 3))], [N("matched", [(A, 2.5)], po=(A, 3))], what="a fractional quantity is parked"),
    S({}, [ORD(1, (A, 3))], [N("discrepancies", [(A, 2.5), (B, 4)], po=(A, 3)), N("matched", [(C, 1)])], what="a fractional quantity next to a received note"),
    S({}, [ORD(1, (A, 10)), ORD(2, (A, 10)), ORD(3, (A, 10))], [N("matched", [(A, 30)])], what="three orders served in arrival order in one run"),
    S({B: 50}, [ORD(1, (B, 10))], [], what="an order stock covered before any receipt"),
    S({C: 5}, [ORD(1, (C, 5))], [N("awaiting", [], po=(A, 10))], what="covered stock is served even if nothing was received"),
    S({}, [ORD(1, (A, 50)), ORD(2, (A, 20))], [N("matched", [(A, 30)], po=(A, 30))], what="a large older order is skipped, a later one served"),
    S({}, [ORD(1, (A, 10), (B, 10)), ORD(2, (A, 8))], [N("matched", [(A, 12)], po=(A, 12))], what="an older two-line order skipped, a later one served"),
    S({A: 10, B: 4}, [ORD(1, (A, 10), (B, 10))], [], what="a two-line order with one short line is skipped"),
    S({A: 10, B: 4}, [ORD(1, (A, 10), (B, 10))], [N("matched", [(B, 6)], later=True, po=(B, 6))], [RUN, SCAN(1), RUN], what="the short line arrives later"),
    S({}, [ORD(1, (A, 10))], [N("matched", [(A, 10)])], [RUN, RUN], what="a second run changes nothing"),
    S({}, [ORD(1, (A, 50)), ORD(2, (A, 10))], [N("matched", [(A, 20)], po=(A, 20))], [RUN, RUN, RUN], what="reruns keep listing the skipped order"),
    S({}, [ORD(1, (A, 10))], [N("matched", [(A, 10)])], [CRASH("receive"), RUN], what="a crash after receive and a resumption"),
    S({}, [ORD(1, (A, 10)), ORD(2, (B, 4))], [N("matched", [(A, 10)]), N("matched", [(B, 4)])], [CRASH("serve"), RUN], what="a crash after serve and a resumption"),
    S({}, [ORD(1, (A, 10))], [N("matched", [(A, 10)], later=True)], [SCAN(1), RUNP], what="a process restart between the scan and the run"),
    S({}, [ORD(1, (A, 10))], [N("matched", [(A, 10)]), N("discrepancies", [(B, 3)], later=True)], [RUN, SCAN(2), RUNP], what="a restart between a later scan and the run"),
    S({}, [ORD(1, (A, 6), (A, 6))], [N("matched", [(A, 14)], po=(A, 14))], what="lines of the same SKU are summed to serve"),
]  # fmt: skip


# The simulator of the written rules, apart from `receipts.py`.


def _units(items) -> str:
    return " ".join(f"{sku}={quantity}" for sku, quantity in items)


def _problem(lines: list) -> str | None:
    if not lines:
        return "the delivery note has no lines"
    for i, (sku, quantity) in enumerate(lines, 1):
        if sku is None:
            return f"line {i} has no catalog SKU: {UNKNOWN_TEXT}"
        if quantity != int(quantity) or quantity <= 0:
            return f"line {i} has quantity {quantity:g}, not a whole positive number"
    return None


def _simulate(item: dict) -> dict:
    stock = {sku: item["stock"].get(sku, 0) for sku in SKUS}
    orders = item["orders"]
    status = ["received"] * len(orders)
    notes = {n["number"]: n for n in item["notes"]}
    live, done, made = [], set(), []  # live notes in document order, received numbers, receipts (number, status)
    po_of, order_notes, shortfalls, outs, runs = {}, [], [], [], []

    def insert(number):
        n = notes[number]
        if n["result"] != "unlinked":
            po_of[number] = len(po_of) + 1
        live.append(number)

    for n in item["notes"]:
        if not n["later"]:
            insert(n["number"])

    def run() -> tuple[list, dict]:
        out, ran = [], {"received": [], "served": [], "skipped": []}
        for number in live:
            n = notes[number]
            if n["result"] not in ("matched", "discrepancies") or number in done:
                continue
            done.add(number)
            problem = _problem(n["lines"])
            made.append((number, "failed" if problem else "received"))
            head = f"receipt RCP-{len(made):04d}  {number}  PO-{po_of[number]:04d}  "
            if problem:
                out.append(f"{head}failed  reason: {problem}")
            else:
                out.append(f"{head}received  {_units((s, int(q)) for s, q in n['lines'])}")
                for sku, quantity in n["lines"]:
                    stock[sku] += int(quantity)
            ran["received"].append((number, made[-1][1], problem))
        files = []
        for index, order in enumerate(orders):
            if status[index] != "received":
                continue
            needs = Counter()
            for sku, quantity in order["lines"]:
                needs[sku] += quantity
            lacking = {sku: need - stock[sku] for sku, need in sorted(needs.items()) if need > stock[sku]}
            if lacking:
                ran["skipped"].append((index + 1, lacking))
                continue
            for sku, need in needs.items():
                stock[sku] -= need
            status[index] = "served"
            order_notes.append(index + 1)
            files.append(len(order_notes))
            ran["served"].append(index + 1)
            out.append(f"served ORD-{index + 1:04d}  CDN-{len(order_notes):04d}  {_units(sorted(needs.items()))}")
        out += [f"skipped ORD-{o:04d}  missing {_units(m.items())}" for o, m in ran["skipped"]]
        out += [f"delivery note file: {NOTES_MARK}/CDN-{n:04d}.txt" for n in files]
        if not (ran["received"] or ran["served"]):
            out.append("nothing changed")
        return out, ran

    def shortfall() -> list:
        demand = Counter()
        for index, order in enumerate(orders):
            if status[index] == "received":
                for sku, quantity in order["lines"]:
                    demand[sku] += quantity
        found = []
        for sku in sorted(demand):
            asked = sum(
                notes[num]["po"][1] for num in po_of if notes[num]["po"][0] == sku and (num, "received") not in made
            )
            if demand[sku] > stock[sku] + asked:
                found.append([sku, demand[sku] - stock[sku] - asked])
        return found

    for step in item["steps"]:
        if step["op"] == "scan":
            for number in step["notes"]:
                insert(number)
            outs.append(None)
        elif step["op"] == "quotes_scan":
            shortfalls.append(shortfall())
            outs.append(None)
        elif step["op"] == "crash":
            outs.append(None)
        else:
            out, ran = run()
            outs.append(out)
            runs.append(ran)
    return {
        "orders": status, "stock": stock, "receipts": [list(m) for m in made],
        "failures": sum(1 for m in made if m[1] == "failed"), "notes": order_notes, "shortfalls": shortfalls,
        "outs": outs, "runs": runs, "made": made,
    }  # fmt: skip


def derive_tags(item: dict) -> list[str]:
    """The coverage tags a scenario carries, read from its notes, orders and steps."""
    sim = _simulate(item)
    notes = {n["number"]: n for n in item["notes"]}
    tags = set()
    received = [(notes[num], state, why) for run in sim["runs"] for num, state, why in run["received"]]
    for n, state, why in received:
        if state == "received":
            if n["result"] == "matched":
                tags.add("matched_received")
            if n["result"] == "discrepancies":
                tags.add("discrepancies_received")
            total = sum(q for s, q in n["lines"] if s == n["po"][0])
            if total < n["po"][1]:
                tags.add("short_delivery")
            if total > n["po"][1]:
                tags.add("over_delivery")
            if any(s != n["po"][0] for s, _ in n["lines"]):
                tags.add("sku_not_on_po")
        elif "no catalog SKU" in why:
            tags.add("parked_unknown_sku")
        else:
            tags.add("parked_fractional")
    if any(n["claim"] for n in item["notes"]):
        tags.add("claim_status")
    for result, tag in (("awaiting", "no_receipt_awaiting_invoice"), ("unlinked", "no_receipt_unlinked"),
                        ("no_invoice", "no_receipt_no_invoice")):  # fmt: skip
        if any(n["result"] == result for n in item["notes"]):
            tags.add(tag)
    ops = [s["op"] for s in item["steps"]]
    tags |= {"later_scan"} if "scan" in ops else set()
    tags |= {"shortfall_scan"} if "quotes_scan" in ops else set()
    tags |= {"crash_resume"} if "crash" in ops else set()
    tags |= {"restart"} if any(s.get("new_process") for s in item["steps"]) else set()
    tags |= {"rerun"} if any(a == b == "run" for a, b in zip(ops, ops[1:], strict=False)) else set()
    for run in sim["runs"]:
        if len(run["served"]) >= 2:
            tags.add("serve_in_order")
        if any(s < v for s, _ in run["skipped"] for v in run["served"]):
            tags.add("large_skipped")
        for index, lacking in run["skipped"]:
            if len({sku for sku, _ in item["orders"][index - 1]["lines"]}) >= 2 and len(lacking) == 1:
                tags.add("multi_line_short")
        for index in run["served"]:
            if all(item["stock"].get(sku, 0) >= q for sku, q in item["orders"][index - 1]["lines"]):
                tags.add("covered_before_receipt")
    return [tag for tag in TAGS if tag in tags]


def build_items() -> list[dict]:
    items = []
    for n, scenario in enumerate(SCENARIOS, start=1):
        item = {"id": f"GR-{n:03d}", "dataset_version": DATASET_VERSION, **scenario}
        sim = _simulate(item)
        item["steps"] = [
            {**s, "out": out} if out is not None else s for s, out in zip(item["steps"], sim["outs"], strict=True)
        ]
        item["expected"] = {
            "orders": sim["orders"], "stock": sim["stock"], "receipts": sim["receipts"],
            "failures": sim["failures"], "notes": sim["notes"], "shortfalls": sim["shortfalls"], "model_calls": 0,
        }  # fmt: skip
        item["tags"] = derive_tags(item)
        items.append(json.loads(json.dumps(item)))
    return items


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("GR-*.json"))]


# Runner: the scenario setup through SQL inserts of extracted rows, no model.


def _insert_note(conn, n: dict) -> None:
    number, result = n["number"], n["result"]
    po = None
    if result != "unlinked":
        count = conn.execute("SELECT COUNT(*) FROM quote_requests").fetchone()[0]
        request = conn.execute(
            "INSERT INTO quote_requests (sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
            "VALUES (?, ?, ?, 'ordered', 't', 't', 't')",
            (n["po"][0], n["po"][1], f"th-{count}"),
        ).lastrowid
        po = conn.execute(
            "INSERT INTO purchase_orders (request_id, supplier_code, sku, quantity, unit_price, currency, total, "
            "expected_delivery, issued_at, status) VALUES (?, (SELECT code FROM suppliers LIMIT 1), ?, ?, 1.0, 'EUR', "
            "1.0, 't', 't', 'issued')",
            (request, n["po"][0], n["po"][1]),
        ).lastrowid
    note = None
    if result != "awaiting":
        note = conn.execute(
            "INSERT INTO supplier_documents (kind, number, purchase_order_id, source_file, content_hash, status, "
            "created_at) VALUES ('delivery_note', ?, ?, 'f.txt', ?, ?, 't')",
            (number, po, f"hash-{number}", "unlinked" if result == "unlinked" else "linked"),
        ).lastrowid
        for line_no, (sku, quantity) in enumerate(n["lines"], 1):
            conn.execute(
                "INSERT INTO document_lines (document_id, line_no, sku, product_text, quantity) VALUES (?, ?, ?, ?, ?)",
                (note, line_no, sku, UNKNOWN_TEXT, quantity),
            )
    if result in ("matched", "discrepancies", "awaiting"):
        invoice = conn.execute(
            "INSERT INTO supplier_documents (kind, number, purchase_order_id, source_file, content_hash, status, "
            "created_at) VALUES ('invoice', ?, ?, 'f.txt', ?, 'linked', 't')",
            (f"INV-{number}", po, f"hash-INV-{number}"),
        ).lastrowid
        rec = conn.execute(
            "INSERT INTO reconciliations (invoice_id, purchase_order_id, delivery_note_id, result, reconciled_at) "
            "VALUES (?, ?, ?, ?, 't')",
            (invoice, po, note, "awaiting_delivery_note" if result == "awaiting" else result),
        ).lastrowid
        if n["claim"]:
            conn.execute(
                "INSERT INTO claims (reconciliation_id, recipient, subject, draft_body, draft_source, status, "
                "drafted_at) VALUES (?, 'a@b.c', 's', 'b', 'template', ?, 't')",
                (rec, n["claim"]),
            )


def _stock(conn) -> dict:
    return {r["sku"]: r["on_hand"] for r in conn.execute("SELECT sku, on_hand FROM stock")}


def _setup(item: dict, folder: Path) -> dict:
    paths = {"db": folder / "business.db", "checkpoints": folder / "checkpoints.sqlite", "notes": folder / "notes"}
    conn = db.connect(paths["db"])
    try:
        db.seed(conn)
        for sku in SKUS:
            conn.execute("UPDATE stock SET on_hand = ? WHERE sku = ?", (item["stock"].get(sku, 0), sku))
        for order in item["orders"]:
            db.insert_order(conn, order["customer"], "demo", "received", [tuple(x) for x in order["lines"]])
        for n in item["notes"]:
            if not n["later"]:
                _insert_note(conn, n)
        conn.commit()
        paths["stock0"] = _stock(conn)
    finally:
        conn.close()
    return paths


def _mask(text: str, paths: dict) -> list[str]:
    text = text.replace(str(paths["notes"]), NOTES_MARK).replace("\\", "/")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _argv(paths: dict) -> list[str]:
    return ["--checkpoints", str(paths["checkpoints"]), "--notes-dir", str(paths["notes"])]


def _run_step(item: dict, step: dict, paths: dict) -> list[str] | None:
    """The printed lines of a `run`; the other steps print nothing."""
    if step["op"] == "run" and step.get("new_process"):
        argv = ["--db", str(paths["db"]), "receipts", "run", *_argv(paths)]
        done = subprocess.run(
            [sys.executable, "-m", "purchase_cycle.cli", *argv], capture_output=True, text=True, timeout=120
        )
        return _mask(done.stdout + (f"exit {done.returncode}\n" if done.returncode else ""), paths)
    if step["op"] == "run":
        args = SimpleNamespace(
            db=str(paths["db"]), notes_dir=str(paths["notes"]), checkpoints=str(paths["checkpoints"])
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = receipts_cli.cmd_receipts_run(args)
        return _mask(buffer.getvalue() + (f"exit {code}\n" if code else ""), paths)
    if step["op"] == "crash":
        checkpointer = sqlite_checkpointer(paths["checkpoints"])
        try:
            receipts.run_receipts(paths["db"], paths["notes"], checkpointer, interrupt_after=[step["after"]])
        finally:
            checkpointer.conn.close()
        return None
    conn = db.connect(paths["db"])
    try:
        if step["op"] == "scan":
            for n in item["notes"]:
                if n["number"] in step["notes"]:
                    _insert_note(conn, n)
            conn.commit()
            return None
        paths.setdefault("shortfalls", []).append([[r["sku"], r["shortfall"]] for r in quotes.shortfalls(conn)])
        return None
    finally:
        conn.close()


def _measure(paths: dict, item: dict) -> dict:
    """The final state read from the database and the delivery note folder."""
    conn = db.connect(paths["db"])
    try:
        notes = []
        for row in conn.execute("SELECT * FROM customer_delivery_notes ORDER BY id").fetchall():
            lines = conn.execute(
                "SELECT sku, quantity FROM customer_delivery_note_lines WHERE note_id = ? ORDER BY line_no",
                (row["id"],),
            ).fetchall()
            notes.append(
                {
                    "id": row["id"],
                    "order": row["order_id"],
                    "lines": [[r["sku"], r["quantity"]] for r in lines],
                    "file_exists": Path(row["document_file"]).exists(),
                }  # fmt: skip
            )
        listed = [r["status"] for r in conn.execute("SELECT status FROM orders ORDER BY id")]
        return {
            "stock": _stock(conn), "stock0": paths["stock0"], "orders": listed, "notes": notes,
            "receipts": [r[0] for r in conn.execute("SELECT delivery_note_id FROM goods_receipts ORDER BY id")],
            "receipt_rows": [
                [r["number"], r["status"]]
                for r in conn.execute(
                    "SELECT d.number, g.status FROM goods_receipts g JOIN supplier_documents d "
                    "ON d.id = g.delivery_note_id ORDER BY g.id"
                )
            ],
            "candidates": [
                r[0]
                for r in conn.execute(
                    "SELECT d.id FROM supplier_documents d WHERE d.kind = 'delivery_note' AND d.status = 'linked' "
                    "AND d.id IN (SELECT delivery_note_id FROM reconciliations WHERE result IN ('matched', 'discrepancies'))"
                )
            ],
            "received_units": {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT l.sku, SUM(l.quantity) FROM goods_receipt_lines l JOIN goods_receipts g "
                    "ON g.id = l.receipt_id WHERE g.status = 'received' GROUP BY l.sku"
                )
            },
            "failures": conn.execute("SELECT COUNT(*) FROM failures WHERE step = 'goods_receipt'").fetchone()[0],
            "files": sorted(p.name for p in Path(paths["notes"]).glob("*.txt")) if Path(paths["notes"]).exists() else [],
        }  # fmt: skip
    finally:
        conn.close()


def _errors(item: dict, final: dict) -> dict[str, int]:
    """The critical errors of one scenario, from the final state and an oracle apart from `receipts`."""
    found = dict.fromkeys(CRITICAL_KINDS, 0)
    found["negative_stock"] = sum(v < 0 for v in final["stock"].values()) + sum(
        v < 0 for snapshot in final["stock_steps"] for v in snapshot.values()
    )
    served = Counter()
    for order in {n["order"] for n in final["notes"]}:
        for sku, quantity in item["orders"][order - 1]["lines"]:
            served[sku] += quantity
    for sku, on_hand in final["stock"].items():
        if on_hand != final["stock0"][sku] + final["received_units"].get(sku, 0) - served[sku]:
            found["stock_change_mismatch"] += 1
    made = final["receipts"]
    found["receipt_repeated_or_not_candidate"] = (len(made) - len(set(made))) + sum(
        1 for doc in made if doc not in final["candidates"]
    )
    per_order = Counter(n["order"] for n in final["notes"])
    found["order_served_twice_or_two_notes"] = sum(c - 1 for c in per_order.values() if c > 1)
    for snapshot, orders in zip(final["stock_steps"], final["served_steps"], strict=True):
        for position, order in enumerate(orders):
            need = Counter()
            for sku, quantity in item["orders"][order - 1]["lines"]:
                need[sku] += quantity
            before = Counter(snapshot)
            for later in orders[position:]:  # the stock this order saw: what is left plus it and the later ones
                for sku, quantity in item["orders"][later - 1]["lines"]:
                    before[sku] += quantity
            if any(before[sku] < quantity for sku, quantity in need.items()):
                found["served_not_covered"] += 1
        found["order_out_of_arrival_order"] += sum(1 for a, b in zip(orders, orders[1:], strict=False) if a > b)
    for n in final["notes"]:
        wanted = [list(x) for x in item["orders"][n["order"] - 1]["lines"]]
        if n["lines"] != wanted:
            found["note_lines_differ"] += 1
    rows = {f"CDN-{n['id']:04d}.txt" for n in final["notes"]}
    found["file_row_mismatch"] = (
        len(set(final["files"]) - rows)
        + len(rows - set(final["files"]))
        + sum(1 for n in final["notes"] if not n["file_exists"])
    )
    found["model_call"] = final["calls"]
    return found


@contextlib.contextmanager
def _no_model():
    """A model client double: any call is counted and fails."""
    counter = {"calls": 0}

    def refuse(*args, **kwargs):
        counter["calls"] += 1
        raise RuntimeError("goods_receipt must not call a model")

    with (
        mock.patch.object(llm.ModelClient, "extract", refuse),
        mock.patch.object(llm.ModelClient, "_call_model", refuse),
    ):
        yield counter


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    folder.mkdir(parents=True)
    paths = _setup(item, folder)
    outs, served_steps, stock_steps = [], [], []
    with _no_model() as counter:
        for step in item["steps"]:
            conn = db.connect(paths["db"])
            before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM customer_delivery_notes").fetchone()[0]
            conn.close()
            outs.append(_run_step(item, step, paths))
            if step["op"] in ("run", "crash"):
                conn = db.connect(paths["db"])
                served_steps.append(
                    [
                        r[0]
                        for r in conn.execute(
                            "SELECT order_id FROM customer_delivery_notes WHERE id > ? ORDER BY id", (before,)
                        )
                    ]
                )
                stock_steps.append(_stock(conn))
                conn.close()
    final = _measure(paths, item)
    final.update(calls=counter["calls"], served_steps=served_steps, stock_steps=stock_steps)
    measured = {
        "orders": final["orders"], "stock": {sku: final["stock"][sku] for sku in SKUS},
        "receipts": final["receipt_rows"], "failures": final["failures"],
        "notes": [n["order"] for n in final["notes"]], "shortfalls": paths.get("shortfalls", []),
        "model_calls": final["calls"],
    }  # fmt: skip
    wanted = dict(item["expected"])
    names = [f"CDN-{i:04d}.txt" for i in range(1, len(wanted["notes"]) + 1)]
    correct = (
        measured == wanted
        and final["files"] == names
        and outs == [s.get("out") for s in item["steps"]]
        and all(n["file_exists"] for n in final["notes"])
    )
    return {
        "id": item["id"], "tags": item["tags"], "expected": wanted, **measured, "out": outs, "correct": correct,
        "critical": _errors(item, final),
    }  # fmt: skip


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
    """Exit 1 when any scenario holds a critical error, naming each one."""
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
    """Run every scenario and print the report; exit 1 below 100% success or with a critical error."""
    items = load_dataset(dataset_dir)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = [run_item(item, Path(tmp)) for item in items]
    s = _rate(results)
    failed = zero_event_note(_rate([{"correct": not r["correct"]} for r in results]))
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    errors = critical_totals(results)
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: model double, no model call, no split, no baseline)")  # fmt: skip
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    print(
        f"{'scenario_success':<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNIT:<16}{target:<9}{met:<12}"
        f"{_pct(TARGET):<11}{gate}" + zero_event_note(s) + (f"  failed scenarios{failed}" if failed else "")
    )
    print_critical(errors, len(results))
    print("per tag:")
    for tag in TAGS:
        rows = [r for r in results if tag in r["tags"]]
        if rows:
            g = _rate(rows)
            print(f"  {tag:<24} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        got = {
            k: r[k] for k in ("orders", "stock", "receipts", "failures", "notes", "shortfalls", "model_calls", "out")
        }
        print(f"  {r['id']}  expected {r['expected']}  got {got}")
    code = 0
    if failures:
        print(f"GATE FAILED: scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        code = 1
    return max(code, critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} goods receipt scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
