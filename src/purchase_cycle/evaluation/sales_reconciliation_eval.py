"""Deterministic `sales_reconciliation` evaluation (phase 11, D6, C13, C1).

Each scenario is one versioned JSON file: the delivered orders, the steps (`sales invoice`, `sales scan` with the signed
note files, an order served later, a crash inside the sales graph) and the expected final state written by hand from
the rules: one invoice per delivery note at delivery note units times catalog price, accepted units = delivered minus
missing minus damaged plus extra, difference amount = (invoiced - accepted) units times the unit price, credit note
for invoiced above accepted and corrective invoice for accepted above invoiced.

The runner seeds a temporary database per scenario and runs the CLI functions (a `restart` scenario runs every command
in a new process). The grader reads the new tables, the invoice folder, the `failures` table and the printed output of
`sales list` and `sales show`. No model is called: the model client is replaced by a counter that records any call.
The gate is 100% scenario success and 0 critical errors.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.sales_reconciliation_eval`.
"""

import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from purchase_cycle import cli, db, llm, receipts, sales
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer

SUITE = "sales_reconciliation"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "sales_reconciliation"
TAGS = (
    "invoice_per_note",
    "received_in_full",
    "awaiting_delivery_note",
    "missing",
    "damaged",
    "extra_delivered_sku",
    "extra_sku_not_on_note",
    "several_types",
    "several_lines_one_sku",
    "credit_note_only",
    "corrective_invoice_only",
    "both_documents",
    "unknown_note",
    "already_signed",
    "other_customer",
    "form_failure",
    "second_invoice",
    "second_scan",
    "crash_resume",
    "restart",
)
MINIMUMS = {"scenarios": 30, **dict.fromkeys(TAGS, 2)}
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "invoice_amount_wrong",
    "duplicate_invoice",
    "difference_amount_wrong",
    "incident_missed",
    "incident_invented",
    "correction_total_wrong",
    "note_linked_wrong",
    "model_call",
)
INITIAL_STATE = {"seed": "db.seed: catalog, stock, customers and the clock; the orders of each scenario served"}
X, Y, Z = "GLV-NIT-XS", "GLV-NIT200-S", "GLV-VIN-S"
PRICES = {X: 6.90, Y: 12.50, Z: 4.10}  # the catalog prices the oracle uses, written by hand
NOTE_TEXT = {"unknown": "unknown delivery note", "signed": "already signed", "customer": "another customer"}

# ---------------------------------------------------------------------------------------------------------------
# The scenarios, written by hand. Delivery notes are numbered in the order the orders are served.


def order(customer, *lines) -> dict:
    return {"customer": customer, "lines": [list(x) for x in lines]}


O1 = order("CLI-001", (X, 5), (Y, 4))  # net total 84.50
O2 = order("CLI-002", (Z, 10))  # net total 41.00
O3 = order("CLI-001", (X, 3))  # net total 20.70


def form(note, incidents=None, customer="CLI-001") -> dict:
    """A signed note file: `incidents` None is `Received in full`, else a list of (sku, type, units)."""
    body = (
        "Received in full"
        if incidents is None
        else "Incidents:\n" + "\n".join(" ".join(map(str, i)) for i in incidents)
    )
    text = f"Signed delivery note\nDelivery note: CDN-{note:04d}\nCustomer: {customer}\nSigned by: Laura Etxeberria\n"
    return {"text": text + f"Signing date: 2026-10-14\n{body}\n", "note": note, "incidents": incidents}


def raw(text) -> dict:
    return {"text": text, "note": None, "incidents": None}


def scan(files=None, stop=None) -> dict:
    return {"do": "scan", "files": files or {}, "stop": stop}


INVOICE = {"do": "invoice"}


def serve(o) -> dict:
    return {"do": "serve", "order": o}


def case(orders, steps, results, notes=None, failures=0, restart=False) -> dict:
    return {"orders": orders, "steps": steps, "results": results, "notes": notes or {}, "failures": failures,
            "restart": restart}  # fmt: skip


def rec(result, diffs=(), docs=()) -> dict:
    return {"result": result, "diffs": [list(d) for d in diffs], "docs": [list(d) for d in docs]}


AWAIT = rec("awaiting_delivery_note")
FULL = rec("matched")
LINKED1 = {"n1": ["linked", 1]}


def one(o, incidents, result, **kw):
    """One order, one signed note with the incidents, scanned once."""
    return case([o], [scan({"n1": form(1, incidents, o["customer"])})], {"SINV-0001": result},
                {"n1": ["linked", 1]}, **kw)  # fmt: skip


CASES = [
    # one invoice per delivery note, with catalog prices and totals
    case([O1], [INVOICE], {}),
    case([O1, O2], [INVOICE], {}),
    case([O1, O2, O3], [scan()], {"SINV-0001": AWAIT, "SINV-0002": AWAIT, "SINV-0003": AWAIT}),
    # received in full
    one(O1, None, FULL),
    one(O2, None, FULL),
    one(O3, None, FULL),
    case([O1, O2], [scan({"n1": form(1), "n2": form(2, None, "CLI-002")})], {"SINV-0001": FULL, "SINV-0002": FULL},
         {"n1": ["linked", 1], "n2": ["linked", 2]}),  # fmt: skip
    # missing units: credit note only
    one(O1, [(X, "missing", 2)], rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])),
    one(O1, [(Y, "missing", 4)], rec("discrepancies", [(Y, "missing", 50.00)], [("credit_note", -50.00, [(Y, -4)])])),
    one(O2, [(Z, "missing", 3)], rec("discrepancies", [(Z, "missing", 12.30)], [("credit_note", -12.30, [(Z, -3)])])),
    # damaged units
    one(O1, [(X, "damaged", 1)], rec("discrepancies", [(X, "damaged", 6.90)], [("credit_note", -6.90, [(X, -1)])])),
    one(O1, [(Y, "damaged", 2)], rec("discrepancies", [(Y, "damaged", 25.00)], [("credit_note", -25.00, [(Y, -2)])])),
    one(O2, [(Z, "damaged", 10)], rec("discrepancies", [(Z, "damaged", 41.00)], [("credit_note", -41.00, [(Z, -10)])])),
    # extra units of a delivered SKU: corrective invoice only
    one(O1, [(X, "extra", 3)], rec("discrepancies", [(X, "extra", -20.70)], [("corrective_invoice", 20.70, [(X, 3)])])),
    one(O1, [(Y, "extra", 1)], rec("discrepancies", [(Y, "extra", -12.50)], [("corrective_invoice", 12.50, [(Y, 1)])])),
    # an extra SKU not on the note
    one(O1, [(Z, "extra", 2)], rec("discrepancies", [(Z, "extra", -8.20)], [("corrective_invoice", 8.20, [(Z, 2)])])),
    one(O3, [(Y, "extra", 1)], rec("discrepancies", [(Y, "extra", -12.50)], [("corrective_invoice", 12.50, [(Y, 1)])])),
    # several incident types on one note: both documents
    one(O1, [(X, "missing", 1), (Y, "damaged", 2), (Z, "extra", 1)],
        rec("discrepancies", [(X, "missing", 6.90), (Y, "damaged", 25.00), (Z, "extra", -4.10)],
            [("credit_note", -31.90, [(X, -1), (Y, -2)]), ("corrective_invoice", 4.10, [(Z, 1)])])),  # fmt: skip
    one(O1, [(Y, "missing", 1), (Z, "extra", 3)],
        rec("discrepancies", [(Y, "missing", 12.50), (Z, "extra", -12.30)],
            [("credit_note", -12.50, [(Y, -1)]), ("corrective_invoice", 12.30, [(Z, 3)])])),  # fmt: skip
    # several incident lines on one SKU: the net direction and the larger loss decide
    one(O1, [(X, "missing", 1), (X, "damaged", 2), (X, "extra", 1)],
        rec("discrepancies", [(X, "damaged", 13.80)], [("credit_note", -13.80, [(X, -2)])])),  # fmt: skip
    one(O1, [(X, "missing", 1), (X, "damaged", 1)],
        rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])),  # fmt: skip
    one(O1, [(X, "missing", 1), (X, "extra", 1)], FULL),
    # an unlinked signed note
    case([O1], [scan({"n1": form(99)})], {"SINV-0001": AWAIT}, {"n1": ["unlinked", "unknown"]}),
    case([O1], [scan({"a1": form(1), "b2": form(1, [(X, "missing", 2)])})], {"SINV-0001": FULL},
         {"a1": ["linked", 1], "b2": ["unlinked", "signed"]}),  # fmt: skip
    case([O1], [scan({"n1": form(1, None, "CLI-002")})], {"SINV-0001": AWAIT}, {"n1": ["unlinked", "customer"]}),
    case([O1], [scan({"a1": form(99), "b2": form(1, [(X, "missing", 2)])})],
         {"SINV-0001": rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])},
         {"a1": ["unlinked", "unknown"], "b2": ["linked", 1]}),  # fmt: skip
    case([O1, O2], [scan({"a2": form(2, None, "CLI-002"), "b2": form(2, [(Z, "missing", 1)], "CLI-002")})],
         {"SINV-0001": AWAIT, "SINV-0002": FULL}, {"a2": ["linked", 2], "b2": ["unlinked", "signed"]}),  # fmt: skip
    case([O1, O2], [scan({"n2": form(2, None, "CLI-001")})], {"SINV-0001": AWAIT, "SINV-0002": AWAIT},
         {"n2": ["unlinked", "customer"]}),  # fmt: skip
    # a form failure parked as needs_review
    case([O1], [scan({"n1": raw("not a signed note\n")})], {"SINV-0001": AWAIT}, {"n1": ["needs_review", None]}, 1),
    case([O1], [scan({"n1": form(1, [(X, "broken", 1)])})], {"SINV-0001": AWAIT}, {"n1": ["needs_review", None]}, 1),
    case([O1], [scan({"n1": form(1, [(X, "missing", 9)])})], {"SINV-0001": AWAIT}, {"n1": ["needs_review", None]}, 1),
    case([O1], [scan({"n1": form(1, [(X, "missing", "1.5")])})], {"SINV-0001": AWAIT}, {"n1": ["needs_review", None]}, 1),
    case([O1], [scan({"n1": form(1, [("NO-SUCH-SKU", "missing", 1)])})], {"SINV-0001": AWAIT},
         {"n1": ["needs_review", None]}, 1),  # fmt: skip
    # a second `sales invoice`, a new order served in between, and a second `sales scan`
    case([O1], [INVOICE, INVOICE], {}),
    case([O1], [INVOICE, serve(O2), INVOICE], {}),
    case([O1], [scan({"n1": form(1, [(X, "missing", 2)])}), scan(), scan({"n1": form(1, [(X, "missing", 2)])})],
         {"SINV-0001": rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])},
         LINKED1),  # fmt: skip
    case([O1], [scan(), scan({"n1": form(1)})], {"SINV-0001": FULL}, LINKED1),
    case([O1], [scan({"n1": form(1, [(Z, "extra", 2)])}), serve(O2), scan({"n2": form(2, None, "CLI-002")})],
         {"SINV-0001": rec("discrepancies", [(Z, "extra", -8.20)], [("corrective_invoice", 8.20, [(Z, 2)])]),
          "SINV-0002": FULL},
         {"n1": ["linked", 1], "n2": ["linked", 2]}),  # fmt: skip
    # a crash inside the sales graph followed by a resumption
    case([O1], [scan({"n1": form(1, [(X, "missing", 2)])}, stop="link"), scan()],
         {"SINV-0001": rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])}, LINKED1),
    case([O1], [scan({"n1": form(1)}, stop="invoice"), scan()], {"SINV-0001": FULL}, LINKED1),
    case([O1], [scan({"n1": form(1, [(X, "missing", 1), (Y, "damaged", 2), (Z, "extra", 1)])}, stop="match"), scan()],
         {"SINV-0001": rec("discrepancies", [(X, "missing", 6.90), (Y, "damaged", 25.00), (Z, "extra", -4.10)],
                           [("credit_note", -31.90, [(X, -1), (Y, -2)]), ("corrective_invoice", 4.10, [(Z, 1)])])},
         LINKED1),  # fmt: skip
    case([O1], [scan({"n1": form(1, [(X, "missing", 2)])}, stop="read"), scan(), scan()],
         {"SINV-0001": rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])}, LINKED1),
    # a process restart between invoicing and the scan: every command in a new process
    case([O1], [INVOICE, scan({"n1": form(1, [(X, "missing", 2)])})],
         {"SINV-0001": rec("discrepancies", [(X, "missing", 13.80)], [("credit_note", -13.80, [(X, -2)])])}, LINKED1,
         restart=True),  # fmt: skip
    case([O1], [INVOICE, scan({"n1": form(1, [(Y, "missing", 1), (Z, "extra", 3)])})],
         {"SINV-0001": rec("discrepancies", [(Y, "missing", 12.50), (Z, "extra", -12.30)],
                           [("credit_note", -12.50, [(Y, -1)]), ("corrective_invoice", 12.30, [(Z, 3)])])},
         LINKED1, restart=True),  # fmt: skip
    case([O1, O2], [INVOICE, scan(), scan({"n1": form(1), "n2": form(2, None, "CLI-002")})],
         {"SINV-0001": FULL, "SINV-0002": FULL}, {"n1": ["linked", 1], "n2": ["linked", 2]}, restart=True),
    case([O1], [INVOICE], {}, restart=True),
]  # fmt: skip


def invoices_of(item_orders: list[dict]) -> dict:
    """The invoice every served order must get, from catalog prices: lines and net total."""
    out = {}
    for n, o in enumerate(item_orders, start=1):
        lines = [[sku, qty, PRICES[sku], round(qty * PRICES[sku], 2)] for sku, qty in o["lines"]]
        out[f"SINV-{n:04d}"] = {"note": n, "lines": lines, "net_total": round(sum(x[3] for x in lines), 2)}
    return out


def all_orders(spec: dict) -> list[dict]:
    return spec["orders"] + [s["order"] for s in spec["steps"] if s["do"] == "serve"]


def derive_tags(item: dict) -> list[str]:
    """The coverage tags follow from the files and the expected results: the files hold what the scenario really is."""
    tags = {"invoice_per_note"} if item["expected"]["invoices"] else set()
    steps, expected = item["steps"], item["expected"]
    notes = [lines for o in all_orders(item) for lines in [{sku for sku, _ in o["lines"]}]]
    for step in steps:
        for name, file in step.get("files", {}).items():
            if expected["notes"].get(name, [None])[0] != "linked":
                continue
            incidents = file["incidents"] or []
            kinds = [kind for _, kind, _ in incidents]
            tags |= {kind for kind in kinds if kind in ("missing", "damaged")}
            for sku, kind, _ in incidents:
                if kind == "extra":
                    tags.add("extra_delivered_sku" if sku in notes[file["note"] - 1] else "extra_sku_not_on_note")
            if len(set(kinds)) >= 2:
                tags.add("several_types")
            if any(n >= 2 for n in Counter(sku for sku, _, _ in incidents).values()):
                tags.add("several_lines_one_sku")
    for result in expected["results"].values():
        kinds = {doc[0] for doc in result["docs"]}
        if result["result"] == "matched" and any(
            expected["notes"].get(n, [None])[0] == "linked" for n in expected["notes"]
        ):
            tags.add("received_in_full")
        if result["result"] == "awaiting_delivery_note":
            tags.add("awaiting_delivery_note")
        tags |= {"credit_note_only"} if kinds == {"credit_note"} else set()
        tags |= {"corrective_invoice_only"} if kinds == {"corrective_invoice"} else set()
        tags |= {"both_documents"} if len(kinds) == 2 else set()
    for status, detail in expected["notes"].values():
        if status == "unlinked":
            tags.add({"unknown": "unknown_note", "signed": "already_signed", "customer": "other_customer"}[detail])
        if status == "needs_review":
            tags.add("form_failure")
    invoice_steps = sum(1 for s in steps if s["do"] == "invoice") + sum(1 for s in steps if s["do"] == "serve")
    if invoice_steps >= 2 and any(s["do"] in ("invoice", "serve") for s in steps[1:]):
        tags.add("second_invoice")
    if sum(1 for s in steps if s["do"] == "scan") >= 2:
        tags.add("second_scan")
    if any(s.get("stop") for s in steps):
        tags.add("crash_resume")
    if item["restart"]:
        tags.add("restart")
    return [t for t in TAGS if t in tags]


def build_items() -> list[dict]:
    items = []
    for n, spec in enumerate(CASES, start=1):
        expected = {"invoices": invoices_of(all_orders(spec)), "results": spec["results"], "notes": spec["notes"],
                    "failures": spec["failures"], "model_calls": 0}  # fmt: skip
        item = {"id": f"SR-{n:03d}", "dataset_version": DATASET_VERSION, "initial_state": INITIAL_STATE,
                "orders": spec["orders"], "steps": spec["steps"], "restart": spec["restart"],
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
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("SR-*.json"))]


# ---------------------------------------------------------------------------------------------------------------
# The runner: product path on a temporary database.

ACTIVE_CALLS: list[int] = []  # model client calls of the scenario being run (any call is a critical error)


def _serve(conn, tmp: Path, o: dict) -> None:
    order_id = db.insert_order(conn, o["customer"], "web_form", "received", [tuple(x) for x in o["lines"]])
    for sku, quantity in o["lines"]:
        conn.execute("UPDATE stock SET on_hand = on_hand + ? WHERE sku = ?", (quantity, sku))
    conn.commit()
    receipts.serve(conn, tmp / "notes")
    receipts.issue(conn)
    assert conn.execute("SELECT 1 FROM customer_delivery_notes WHERE order_id = ?", (order_id,)).fetchone()


def _command(paths: dict, *words: str, new_process: bool = False) -> tuple[int, str, str]:
    argv = ["--db", str(paths["db"]), "sales", *words]
    if new_process:
        done = subprocess.run(
            [sys.executable, "-m", "purchase_cycle.cli", *argv], capture_output=True, text=True, timeout=120
        )
        return done.returncode, done.stdout, done.stderr
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def parse_show(output: str) -> dict | None:
    """The printed result, difference rows `[sku, incident, amount]` and documents `[kind, total]` of `sales show`."""
    head = re.search(r"^result: (\w+)", output, re.M)
    if head is None:
        return None
    diffs = [
        [None if sku == "-" else sku, incident, float(amount)]
        for incident, sku, amount in re.findall(
            r"^\s+difference \w+\s+incident=(\w+)\s+sku=(\S+).*?amount=(-?[\d.]+)", output, re.M
        )
    ]
    docs = [
        [kind, float(total)]
        for _, kind, total in re.findall(r"^document (\S+)\s+(\w+)\s+total=(-?[\d.]+)", output, re.M)
    ]
    docs = [[kind, total] for kind, total in docs]
    return {"result": head.group(1), "diffs": diffs, "docs": docs}


def parse_list(output: str) -> dict | None:
    head = re.search(r"^invoices: (\d+)", output, re.M)
    if head is None:
        return None
    return {"count": int(head.group(1)), "results": re.findall(r"^\s+(SINV-\d{4}).*?result=(\w+)", output, re.M)}


def _run_steps(item: dict, paths: dict, tmp: Path) -> None:
    new = item["restart"]
    conn = db.connect(paths["db"])
    try:
        db.seed(conn)
        for o in item["orders"]:
            _serve(conn, tmp, o)
    finally:
        conn.close()
    inv = ["--invoices-dir", str(paths["invoices"])]
    for step in item["steps"]:
        if step["do"] == "invoice":
            _command(paths, "invoice", *inv, new_process=new)
        elif step["do"] == "serve":
            conn = db.connect(paths["db"])
            try:
                _serve(conn, tmp, step["order"])
            finally:
                conn.close()
        else:
            for name, file in step["files"].items():
                (paths["inbox"] / f"{name}.txt").write_text(file["text"], encoding="utf-8", newline="\n")
            if step["stop"]:
                saver = sqlite_checkpointer(paths["checkpoints"])
                try:
                    sales.run_sales(
                        paths["db"], paths["invoices"], paths["inbox"], saver, interrupt_after=[step["stop"]]
                    )
                finally:
                    saver.conn.close()
            else:
                _command(paths, "scan", str(paths["inbox"]), *inv, "--checkpoints", str(paths["checkpoints"]),
                         new_process=new)  # fmt: skip


def _measure(item: dict, paths: dict) -> dict:
    """Everything the grader reads: the tables, the files, the failures and the printed list and show."""
    conn = db.connect(paths["db"])
    try:
        invoices, dn_lines = [], {}
        for row in conn.execute("SELECT * FROM customer_invoices ORDER BY id"):
            lines = [
                [r["sku"], r["quantity"], r["unit_price"], r["line_total"]]
                for r in conn.execute(
                    "SELECT * FROM customer_invoice_lines WHERE invoice_id = ? ORDER BY line_no", (row["id"],)
                )
            ]
            invoices.append({"number": row["number"], "note": row["delivery_note_id"], "net_total": row["net_total"],
                             "lines": lines})  # fmt: skip
            dn_lines[row["delivery_note_id"]] = {
                r["sku"]: r["quantity"]
                for r in conn.execute(
                    "SELECT sku, quantity FROM customer_delivery_note_lines WHERE note_id = ?",
                    (row["delivery_note_id"],),
                )
            }
        notes = {}
        for path in sorted(paths["inbox"].glob("*.txt")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            row = conn.execute("SELECT * FROM signed_delivery_notes WHERE content_hash = ?", (digest,)).fetchone()
            if row is not None:
                notes[path.stem] = {"status": row["status"], "note": row["delivery_note_id"], "reason": row["reason"]}
            elif db.get_failure(conn, f"signed-note-{digest[:12]}"):
                notes[path.stem] = {"status": "needs_review", "note": None, "reason": None}
        recs = {}
        for row in conn.execute(
            "SELECT r.*, i.number FROM sales_reconciliations r JOIN customer_invoices i ON i.id = r.invoice_id ORDER BY r.id"
        ):
            diffs = [
                [r["sku"], r["incident"], round(r["amount"], 2)]
                for r in conn.execute(
                    "SELECT * FROM sales_discrepancies WHERE reconciliation_id = ? ORDER BY id", (row["id"],)
                )
            ]
            docs = []
            for doc in conn.execute(
                "SELECT * FROM customer_corrections WHERE invoice_id = ? ORDER BY id", (row["invoice_id"],)
            ):
                lines = [
                    [r["sku"], r["quantity"]]
                    for r in conn.execute(
                        "SELECT * FROM customer_correction_lines WHERE correction_id = ? ORDER BY line_no", (doc["id"],)
                    )
                ]
                line_sum = conn.execute(
                    "SELECT ROUND(SUM(line_total), 2) FROM customer_correction_lines WHERE correction_id = ?",
                    (doc["id"],),
                ).fetchone()[0]
                docs.append([doc["kind"], doc["total"], lines, line_sum])
            recs.setdefault(row["number"], []).append({"result": row["result"], "diffs": diffs, "docs": docs})
        failures = conn.execute("SELECT COUNT(*) FROM failures WHERE channel = 'sales'").fetchone()[0]
    finally:
        conn.close()
    new = item["restart"]
    code, out, _ = _command(paths, "list", new_process=new)
    listed = parse_list(out) if code == 0 else None
    shown = {}
    for number in recs:
        code, out, _ = _command(paths, "show", number, new_process=new)
        shown[number] = parse_show(out) if code == 0 else None
    files = sorted(p.name for p in paths["invoices"].glob("*")) if paths["invoices"].exists() else []
    return {"invoices": invoices, "dn_lines": dn_lines, "notes": notes, "recs": recs, "shown": shown, "listed": listed,
            "failures": failures, "files": files, "calls": sum(ACTIVE_CALLS)}  # fmt: skip


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    paths = {"db": folder / "case.db", "invoices": folder / "invoices", "inbox": folder / "inbox",
             "checkpoints": folder / "cp.sqlite"}  # fmt: skip
    paths["inbox"].mkdir(parents=True)
    ACTIVE_CALLS.clear()
    _run_steps(item, paths, folder)
    final = _measure(item, paths)
    expected = item["expected"]
    return {"id": item["id"], "tags": item["tags"], "expected": expected, "got": _got(final),
            "correct": _correct(expected, final), "critical": _errors(item, final)}  # fmt: skip


def _doc(doc) -> list:
    return [doc[0], round(doc[1], 2), doc[2]]


def _got(final: dict) -> dict:
    return {
        "invoices": {
            i["number"]: {"note": i["note"], "lines": i["lines"], "net_total": i["net_total"]}
            for i in final["invoices"]
        },
        "results": {
            n: {"result": r[0]["result"], "diffs": r[0]["diffs"], "docs": [_doc(d)[:2] for d in r[0]["docs"]]}
            for n, r in final["recs"].items()
        },
        "notes": {n: [d["status"], d["note"]] for n, d in final["notes"].items()},
        "failures": final["failures"],
        "model_calls": final["calls"],
    }


def _norm(rows) -> list:
    return sorted(([*r] for r in rows), key=repr)


def _correct(expected: dict, final: dict) -> bool:
    """The scenario passes when invoices, notes, results, differences and documents match in the database and the print."""
    if final["calls"] or final["failures"] != expected["failures"]:
        return False
    if {i["number"]: i for i in final["invoices"]}.keys() != expected["invoices"].keys():
        return False
    for invoice in final["invoices"]:
        goal = expected["invoices"][invoice["number"]]
        if (invoice["note"], invoice["lines"], invoice["net_total"]) != (
            goal["note"],
            goal["lines"],
            goal["net_total"],
        ):
            return False
    if len(final["files"]) != len(expected["invoices"]):
        return False
    if set(final["notes"]) != set(expected["notes"]):
        return False
    for name, (status, detail) in expected["notes"].items():
        got = final["notes"][name]
        if got["status"] != status:
            return False
        if status == "linked" and got["note"] != detail:
            return False
        if status == "unlinked" and NOTE_TEXT[detail] not in (got["reason"] or ""):
            return False
    if set(final["recs"]) != set(expected["results"]) or any(len(r) != 1 for r in final["recs"].values()):
        return False
    for number, goal in expected["results"].items():
        got = final["recs"][number][0]
        shown = final["shown"].get(number)
        docs = [[d[0], round(d[1], 2), d[2]] for d in got["docs"]]
        want_docs = [[k, t, [list(x) for x in lines]] for k, t, lines in goal["docs"]]
        if got["result"] != goal["result"] or _norm(got["diffs"]) != _norm(goal["diffs"]) or docs != want_docs:
            return False
        if shown is None or shown["result"] != goal["result"] or _norm(shown["diffs"]) != _norm(goal["diffs"]):
            return False
        if shown["docs"] != [[k, t] for k, t, _ in goal["docs"]]:
            return False
    listed = final["listed"]
    return listed is not None and listed == {
        "count": len(expected["invoices"]),
        "results": [
            (n, expected["results"][n]["result"] if n in expected["results"] else "not")
            for n in sorted(expected["invoices"])
        ],
    }


def compare_rows(expected, actual) -> tuple[int, int]:
    """(missed, invented) between two lists of `[sku, incident, amount]`; the amounts are not compared."""
    want = Counter((r[0], r[1]) for r in expected)
    have = Counter((r[0], r[1]) for r in actual)
    return sum((want - have).values()), sum((have - want).values())


def amount_errors(expected, actual) -> int:
    """Rows present in both lists by (SKU, incident) whose amount differs from the expected one."""
    pool = Counter((r[0], r[1], round(r[2], 2)) for r in expected)
    seen = Counter((r[0], r[1]) for r in expected)
    wrong = 0
    for sku, incident, amount in ([r[0], r[1], round(r[2], 2)] for r in actual):
        if pool[(sku, incident, amount)] > 0:
            pool[(sku, incident, amount)] -= 1
        elif seen[(sku, incident)] > 0:
            wrong += 1
    return wrong


def _errors(item: dict, final: dict) -> dict[str, int]:
    """The critical errors of one scenario, from the stored rows and from the printed ones (the larger count of each)."""
    expected = item["expected"]
    errors = dict.fromkeys(CRITICAL_KINDS, 0)
    per_note = Counter(i["note"] for i in final["invoices"])
    errors["duplicate_invoice"] = sum(n - 1 for n in per_note.values() if n > 1)
    for invoice in final["invoices"]:
        delivered = final["dn_lines"][invoice["note"]]
        oracle = sorted([sku, qty, PRICES[sku], round(qty * PRICES[sku], 2)] for sku, qty in delivered.items())
        total = round(sum(x[3] for x in oracle), 2)
        errors["invoice_amount_wrong"] += int(sorted(invoice["lines"]) != oracle or invoice["net_total"] != total)
    for name, (status, detail) in {**expected["notes"]}.items():
        got = final["notes"].get(name)
        if got is None:
            continue
        wrong = (status == "linked" and (got["status"] != "linked" or got["note"] != detail)) or (
            status != "linked" and got["status"] == "linked"
        )
        errors["note_linked_wrong"] += int(wrong)
    for number, goal in expected["results"].items():
        stored = final["recs"].get(number, [{"diffs": [], "docs": []}])[0]
        shown = final["shown"].get(number) or {"diffs": [], "docs": []}
        rows = [stored["diffs"], shown["diffs"]]
        counts = [compare_rows(goal["diffs"], r) for r in rows]
        errors["incident_missed"] += max(c[0] for c in counts)
        errors["incident_invented"] += max(c[1] for c in counts)
        errors["difference_amount_wrong"] += max(amount_errors(goal["diffs"], r) for r in rows)
        credit = round(-sum(d[2] for d in stored["diffs"] if d[2] > 0), 2)
        corrective = round(-sum(d[2] for d in stored["diffs"] if d[2] < 0), 2)
        for kind, total, _lines, line_sum in stored["docs"]:
            due = credit if kind == "credit_note" else corrective
            errors["correction_total_wrong"] += int(abs(total - due) > 0.001 or abs(total - (line_sum or 0)) > 0.001)
        for kind, total in shown["docs"]:
            due = credit if kind == "credit_note" else corrective
            errors["correction_total_wrong"] += int(abs(total - due) > 0.001)
    errors["model_call"] = final["calls"]
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
    """Exit 1 when any scenario holds a critical error, naming each one."""
    if not sum(errors.values()):
        return 0
    for r in results:
        found = {kind: c for kind, c in r["critical"].items() if c}
        if found:
            print(f"  {r['id']}  critical errors {found}")
    print(f"GATE FAILED: critical_errors {sum(errors.values())} above threshold 0")
    return 1


def _count_calls(monkeypatch_target=llm.ModelClient):
    """Replace the model client entry points by counters for the whole run; returns the restore function."""
    saved = (monkeypatch_target.__init__, monkeypatch_target._call_model)

    def counted(*args, **kwargs):
        ACTIVE_CALLS.append(1)
        raise llm.MissingRecording("sales_reconciliation makes no model call")

    monkeypatch_target.__init__ = counted
    monkeypatch_target._call_model = counted

    def restore():
        monkeypatch_target.__init__, monkeypatch_target._call_model = saved

    return restore


def evaluate(
    mode: str, split: str, *, dataset_dir: Path = DATASET_DIR, set_baseline: bool = False, workers: int = 1
) -> int:
    """Run every scenario and print the report; exit 1 below 100% success or with a critical error."""
    items = load_dataset(dataset_dir)
    restore = _count_calls()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            results = [run_item(item, Path(tmp)) for item in items]
    finally:
        restore()
    s = _rate(results)
    failed = zero_event_note(_rate([{"correct": not r["correct"]} for r in results]))
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    errors = critical_totals(results)
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: scripted signed notes, no model call, no split, no baseline)")  # fmt: skip
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
            print(f"  {tag:<28} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        print(f"  {r['id']}  expected {r['expected']}  got {r['got']}")
    code = 0
    if failures:
        print(f"GATE FAILED: scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        code = 1
    return max(code, critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} sales reconciliation scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
