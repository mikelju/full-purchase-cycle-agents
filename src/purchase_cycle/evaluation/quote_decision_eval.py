"""Deterministic `quote_decision` evaluation (phase 07, D7, C14).

Each scenario is one versioned JSON file: a quote request, the supplier replies with the hand-written model answer
of each one, and a script of `quotes decide`, `quotes approve`, `quotes reject` and clock steps run through the CLI
functions on a temporary database. The model answers are stored with the scenario and replayed from a temporary
recordings file, so no key and no network are needed. Every expectation is written by hand from the D2 comparison
rule: full offers first, then the lowest total, the shortest lead time and the lower supplier code.

The runner seeds a temporary database per scenario and runs each step through `cli.main` (a `new_process` step
through `python -m purchase_cycle.cli`). After the first decide the recordings file is deleted, so any later model
call fails on the missing recording and is counted. The grader reads the database, the supplier outbox, the
`failures` table and the printed recommendation. The critical-error counter checks the final state against an
oracle computed apart from `quote_decision`. The gate is 100% success and 0 critical errors.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.quote_decision_eval`.
"""

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from purchase_cycle import cli, db, llm, quote_decision
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note

SUITE = "quote_decision"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "quote_decision"
OPS = ("decide", "approve", "reject", "advance")
TAGS = (
    "single_full", "partial_vs_full", "tie_lead", "tie_code", "partial_only", "no_usable_offer", "approve",
    "choose_other", "refused_choice", "reject", "approve_twice", "restart", "no_offers_request",
)  # fmt: skip
# C14 minimums: scenarios in all and per coverage tag.
MINIMUMS = {"scenarios": 24, **dict.fromkeys(TAGS, 3)}
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "multiple_orders",
    "order_without_approval",
    "order_from_unusable_offer",
    "order_differs_from_offer",
    "recommendation_differs_from_rule",
    "status_order_mismatch",
    "model_call_on_rerun",
)
INITIAL_STATE = {"seed": "db.seed: catalog, stock, customers, suppliers and the clock at 2026-10-12 09:00"}
START = datetime(2026, 10, 12, 9, 0, 0)
SKU, QUANTITY = "GLV-NIT-XS", 12
SUPPLIER_CODES = ("SUP-001", "SUP-002", "SUP-003")
REF = "QR-0001"
# The rule that decided a recommendation and the words its printed reason holds.
RULE_TEXT = {
    "only_usable_offer": "it is the only usable offer",
    "full_quantity": "it is the only offer that covers the full quantity",
    "lowest_total": "it has the lowest total",
    "shortest_lead_time": "totals are equal and it has the shortest lead time",
    "supplier_code": "total and lead time are equal and it has the lower supplier code",
    "partial_only": "no usable offer covers the full quantity",
    "none": "No usable offer",
}
# The coverage tag a rule gives; `lowest_total` has none because its cases are full offers, not a listed case.
RULE_TAG = {
    "only_usable_offer": "single_full", "full_quantity": "partial_vs_full", "shortest_lead_time": "tie_lead",
    "supplier_code": "tie_code", "partial_only": "partial_only", "none": "no_usable_offer",
}  # fmt: skip
UNUSABLE_STATUSES = ("rejected", "no_offers", "ready_for_decision", "awaiting_approval")
NO_RECORDING = "No recording for"


# Builders of the hand-written model answers, the reply texts, the steps and the expected orders.


def offer(quantity=QUANTITY, price=4.0, lead=5, working=True, currency="EUR", vat=None) -> dict:
    """The model answer for an offer; `vat` is the rate of a price that includes VAT."""
    text = f"{price:.4f}".rstrip("0").rstrip(".")
    return {
        "offers": True, "quantity": quantity, "unit_price": price, "currency": currency, "vat_included": vat is not None,
        "vat_rate": vat, "lead_time_days": lead, "working_days": working, "price_text": f"{text} {currency} per box",
    }  # fmt: skip


DECLINED = {
    "offers": False, "quantity": None, "unit_price": None, "currency": None, "vat_included": False, "vat_rate": None,
    "lead_time_days": None, "working_days": None, "price_text": None,
}  # fmt: skip
NO_PRICE = {**offer(), "unit_price": None, "price_text": None}
NO_LEAD = {**offer(), "lead_time_days": None, "working_days": None}
VAT_NO_RATE = {**offer(price=4.84), "vat_included": True}
BROKEN = {**offer(price=4.3), "quantity": "twelve"}


def body(answer: dict) -> str:
    """A short reply text that says what the answer holds."""
    if not answer["offers"]:
        return "Thank you for the request. We cannot supply this item at the moment."
    if not isinstance(answer["quantity"], int):
        return "Sure, twelve for about four euros."
    if answer["unit_price"] is None:
        return f"We can send {answer['quantity']} boxes. Please call us for the price."
    if answer["lead_time_days"] is None:
        return f"We offer {answer['quantity']} boxes at {answer['price_text']}. Delivery date to be confirmed."
    kind = "working" if answer["working_days"] else "calendar"
    vat = f" with {answer['vat_rate']}% VAT included" if answer["vat_included"] else ""
    return (
        f"We offer {answer['quantity']} boxes at {answer['price_text']}{vat}, "
        f"delivered in {answer['lead_time_days']} {kind} days."
    )


def decide(**flags) -> dict:
    return {"op": "decide", "exit": 0, **flags}


def approve(supplier=None, exit=0, **flags) -> dict:
    return {"op": "approve", **({"supplier": supplier} if supplier else {}), "exit": exit, **flags}


def reject(exit=0, **flags) -> dict:
    return {"op": "reject", "exit": exit, **flags}


def advance(days: int) -> dict:
    return {"op": "advance", "days": days, "exit": 0}


def po(supplier, quantity, price, total, delivery, number="PO-0001") -> dict:
    return {"number": number, "supplier": supplier, "quantity": quantity, "unit_price": price, "currency": "EUR",
            "total": total, "expected_delivery": delivery}  # fmt: skip


D = decide
OPEN, ORDERED, REJECTED, NONE = "awaiting_approval", "ordered", "rejected", "no_offers"

# (replies by supplier, steps, expected status, recommended supplier or None, rule or None, expected orders,
# expected failures), in a fixed order; the outbox follows from the orders. The clock starts on Monday 2026-10-12.
SCENARIOS = [
    # A single full offer, the others declined or silent.
    ({"SUP-001": offer(price=4.1)}, [D(), approve()], ORDERED, "SUP-001", "only_usable_offer",
     [po("SUP-001", 12, 4.1, 49.2, "2026-10-19")], 0),
    ({"SUP-002": offer(price=3.5, lead=3)}, [D(), approve()], ORDERED, "SUP-002", "only_usable_offer",
     [po("SUP-002", 12, 3.5, 42.0, "2026-10-15")], 0),
    ({"SUP-001": DECLINED, "SUP-003": offer(price=5.0, lead=10, working=False)}, [D(), approve()], ORDERED,
     "SUP-003", "only_usable_offer", [po("SUP-003", 12, 5.0, 60.0, "2026-10-22")], 0),
    # A cheaper partial offer against a full one: the full offer wins.
    ({"SUP-001": offer(price=4.5), "SUP-002": offer(quantity=8, price=3.0, lead=2)}, [D(), approve()], ORDERED,
     "SUP-001", "full_quantity", [po("SUP-001", 12, 4.5, 54.0, "2026-10-19")], 0),
    ({"SUP-001": offer(quantity=6, price=2.0), "SUP-003": offer(price=5.5, lead=7)}, [D(), approve()], ORDERED,
     "SUP-003", "full_quantity", [po("SUP-003", 12, 5.5, 66.0, "2026-10-21")], 0),
    ({"SUP-001": offer(price=4.0, lead=4), "SUP-002": offer(quantity=10, price=1.0), "SUP-003": offer(price=4.2, lead=3)},
     [D(), approve()], ORDERED, "SUP-001", "lowest_total", [po("SUP-001", 12, 4.0, 48.0, "2026-10-16")], 0),
    # A price tie decided by the shorter lead time in calendar days.
    ({"SUP-001": offer(price=4.0), "SUP-002": offer(price=4.0, lead=3)}, [D(), approve()], ORDERED, "SUP-002",
     "shortest_lead_time", [po("SUP-002", 12, 4.0, 48.0, "2026-10-15")], 0),
    ({"SUP-001": offer(price=3.5, lead=2), "SUP-002": offer(price=4.0),
      "SUP-003": offer(price=4.235, lead=4, working=False, vat=21)},
     [D(), approve()], ORDERED, "SUP-001", "shortest_lead_time", [po("SUP-001", 12, 3.5, 42.0, "2026-10-14")], 0),
    ({"SUP-002": offer(price=4.5, lead=2), "SUP-003": offer(price=4.5, lead=1, working=False)}, [D(), approve()],
     ORDERED, "SUP-003", "shortest_lead_time", [po("SUP-003", 12, 4.5, 54.0, "2026-10-13")], 0),
    # A price and lead time tie decided by the lower supplier code.
    ({"SUP-002": offer(price=4.0, lead=3), "SUP-003": offer(price=4.0, lead=3)}, [D(), approve()], ORDERED,
     "SUP-002", "supplier_code", [po("SUP-002", 12, 4.0, 48.0, "2026-10-15")], 0),
    ({"SUP-001": offer(price=3.0), "SUP-002": offer(price=3.0), "SUP-003": offer(price=3.0)}, [D(), approve()],
     ORDERED, "SUP-001", "supplier_code", [po("SUP-001", 12, 3.0, 36.0, "2026-10-19")], 0),
    ({"SUP-001": offer(price=4.0, lead=7, working=False), "SUP-003": offer(price=4.0)}, [D(), approve()], ORDERED,
     "SUP-001", "supplier_code", [po("SUP-001", 12, 4.0, 48.0, "2026-10-19")], 0),
    # Only partial offers: the lowest total of the offered quantities wins.
    ({"SUP-001": offer(quantity=8, price=4.0, lead=3), "SUP-002": offer(quantity=6, price=3.0, lead=2)},
     [D(), approve()], ORDERED, "SUP-002", "partial_only", [po("SUP-002", 6, 3.0, 18.0, "2026-10-14")], 0),
    ({"SUP-003": offer(quantity=9, price=5.0)}, [D(), approve()], ORDERED, "SUP-003", "partial_only",
     [po("SUP-003", 9, 5.0, 45.0, "2026-10-19")], 0),
    ({"SUP-001": offer(quantity=6, price=4.0, lead=4), "SUP-002": offer(quantity=6, price=4.0, lead=2)},
     [D(), approve()], ORDERED, "SUP-002", "partial_only", [po("SUP-002", 6, 4.0, 24.0, "2026-10-14")], 0),
    # No usable offer: only reject is accepted.
    ({"SUP-001": DECLINED, "SUP-002": DECLINED}, [D(), approve(exit=1), reject()], REJECTED, None, "none", [], 0),
    ({"SUP-001": offer(price=4.0, currency="USD"), "SUP-002": NO_PRICE}, [D(), approve(exit=1)], OPEN, None, "none",
     [], 0),
    ({"SUP-001": NO_LEAD, "SUP-002": VAT_NO_RATE},
     [D(), approve(exit=1), approve("SUP-001", exit=1), reject()], REJECTED, None, "none", [], 0),
    # Choose another offer than the recommended one.
    ({"SUP-001": offer(price=4.5), "SUP-002": offer(quantity=8, price=3.0, lead=2)}, [D(), approve("SUP-002")],
     ORDERED, "SUP-001", "full_quantity", [po("SUP-002", 8, 3.0, 24.0, "2026-10-14")], 0),
    ({"SUP-001": offer(price=4.0), "SUP-002": offer(price=4.0, lead=3)}, [D(), approve("SUP-001")], ORDERED,
     "SUP-002", "shortest_lead_time", [po("SUP-001", 12, 4.0, 48.0, "2026-10-19")], 0),
    # The clock moves three days before the approval, so the order is issued and due later than the decision.
    ({"SUP-001": offer(price=4.0), "SUP-002": offer(price=4.2), "SUP-003": offer(price=4.4)},
     [D(), advance(3), approve("SUP-003")], ORDERED, "SUP-001", "lowest_total",
     [po("SUP-003", 12, 4.4, 52.8, "2026-10-22")], 0),
    # A refused choice changes nothing.
    ({"SUP-001": offer(price=4.1), "SUP-002": DECLINED},
     [D(), approve("SUP-002", exit=1), approve("SUP-003", exit=1), approve("SUP-001")], ORDERED, "SUP-001",
     "only_usable_offer", [po("SUP-001", 12, 4.1, 49.2, "2026-10-19")], 0),
    ({"SUP-001": offer(price=4.1), "SUP-002": offer(price=4.3)}, [D(), approve("SUP-009", exit=1), reject()],
     REJECTED, "SUP-001", "lowest_total", [], 0),
    ({"SUP-001": offer(price=4.0, currency="USD"), "SUP-002": offer(price=4.3)},
     [D(), approve("SUP-001", exit=1)], OPEN, "SUP-002", "only_usable_offer", [], 0),
    # Reject, and approve twice.
    ({"SUP-001": offer(price=4.1), "SUP-002": offer(price=4.3)}, [D(), reject()], REJECTED, "SUP-001", "lowest_total",
     [], 0),
    ({"SUP-002": offer(price=4.1)}, [D(), reject(), approve(exit=1), reject(exit=1)], REJECTED, "SUP-002",
     "only_usable_offer", [], 0),
    ({"SUP-001": offer(quantity=8, price=4.0), "SUP-003": offer(quantity=5, price=3.0)}, [D(), reject()], REJECTED,
     "SUP-003", "partial_only", [], 0),
    ({"SUP-001": offer(price=4.1)}, [D(), approve(), approve(exit=1)], ORDERED, "SUP-001", "only_usable_offer",
     [po("SUP-001", 12, 4.1, 49.2, "2026-10-19")], 0),
    ({"SUP-001": offer(price=4.5), "SUP-002": offer(price=4.0)},
     [D(), approve("SUP-002"), approve("SUP-002", exit=1), reject(exit=1)], ORDERED, "SUP-002", "lowest_total",
     [po("SUP-002", 12, 4.0, 48.0, "2026-10-19")], 0),
    ({"SUP-003": offer(price=4.2, lead=3)}, [D(), approve(), approve(exit=1, new_process=True)], ORDERED,
     "SUP-003", "only_usable_offer", [po("SUP-003", 12, 4.2, 50.4, "2026-10-15")], 0),
    # A process restart between decide and approve: the thread resumes from the SQLite checkpoint.
    ({"SUP-001": offer(price=4.1), "SUP-002": offer(price=4.3)}, [D(new_process=True), approve(new_process=True)],
     ORDERED, "SUP-001", "lowest_total", [po("SUP-001", 12, 4.1, 49.2, "2026-10-19")], 0),
    ({"SUP-001": offer(price=4.5), "SUP-002": offer(quantity=8, price=3.0, lead=2)},
     [D(), approve("SUP-002", new_process=True)], ORDERED, "SUP-001", "full_quantity",
     [po("SUP-002", 8, 3.0, 24.0, "2026-10-14")], 0),
    ({"SUP-002": offer(price=4.0, lead=3), "SUP-003": offer(price=4.0, lead=3)},
     [D(new_process=True), reject(new_process=True)], REJECTED, "SUP-002", "supplier_code", [], 0),
    # A reply that fails the schema is parked for review and the others go on.
    ({"SUP-001": BROKEN, "SUP-002": offer(price=4.3)}, [D(), approve()], ORDERED, "SUP-002", "only_usable_offer",
     [po("SUP-002", 12, 4.3, 51.6, "2026-10-19")], 1),
    # A request with no offers waits for a person: no recommendation and nothing to approve or reject.
    ({}, [D(), approve(exit=1), reject(exit=1)], NONE, None, None, [], 0),
    ({}, [D(), D(), approve("SUP-001", exit=1)], NONE, None, None, [], 0),
    ({}, [D(new_process=True), reject(exit=1)], NONE, None, None, [], 0),
]  # fmt: skip


def derive_tags(item: dict) -> list[str]:
    """The coverage tags follow from the steps and the rule: the files hold what the scenario really does."""
    steps, expected = item["steps"], item["expected"]
    tags = set()
    if expected["rule"] in RULE_TAG:
        tags.add(RULE_TAG[expected["rule"]])
    if item["request"]["status"] == "no_offers":
        tags.add("no_offers_request")
    if any(s["op"] == "approve" and "supplier" not in s and s["exit"] == 0 for s in steps):
        tags.add("approve")
    if any(s["op"] == "approve" and "supplier" in s and s["exit"] == 0 for s in steps):
        tags.add("choose_other")
    if any(s["op"] == "approve" and "supplier" in s and s["exit"] != 0 for s in steps):
        tags.add("refused_choice")
    if any(s["op"] == "reject" and s["exit"] == 0 for s in steps):
        tags.add("reject")
    done = [n for n, s in enumerate(steps) if s["op"] == "approve" and s["exit"] == 0]
    if done and any(s["op"] == "approve" for s in steps[done[0] + 1 :]):
        tags.add("approve_twice")
    if any(s.get("new_process") for s in steps):
        tags.add("restart")
    return [t for t in TAGS if t in tags]


def _outbox(orders: list[dict]) -> list[str]:
    return sorted(f"{o['number']}-{o['supplier']}.eml" for o in orders)


def build_items() -> list[dict]:
    items = []
    for n, (replies, steps, status, recommended, rule, orders, failures) in enumerate(SCENARIOS, start=1):
        item = {
            "id": f"QD-{n:03d}",
            "dataset_version": DATASET_VERSION,
            "initial_state": INITIAL_STATE,
            "request": {"sku": SKU, "quantity": QUANTITY, "status": "no_offers" if status == NONE else "ready_for_decision"},
            "replies": [{"supplier": s, "body": body(a), "answer": a} for s, a in replies.items()],
            "steps": steps,
            "expected": {
                "status": status,
                "recommended": recommended,
                "rule": rule,
                "orders": orders,
                "outbox": _outbox(orders),
                "failures": failures,
            },
        }  # fmt: skip
        item["tags"] = derive_tags(item)
        items.append(item)
    return items


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("QD-*.json"))]


# The oracle: the usable offers, the winner and the order fields recomputed from the stored answers, apart from
# `quote_decision`.


def _add_days(moment: datetime, days: int, working: bool) -> datetime:
    if not working:
        return moment + timedelta(days=days)
    while days > 0:
        moment += timedelta(days=1)
        days -= moment.weekday() < 5
    return moment


def _oracle_offers(item: dict) -> dict[str, dict]:
    """The usable offers by supplier with the net price, the quantity that would be ordered and the lead time."""
    wanted, found = item["request"]["quantity"], {}
    for reply in item["replies"]:
        a = reply["answer"]
        if not (a["offers"] and isinstance(a["quantity"], int) and a["quantity"] > 0):
            continue
        if not a["unit_price"] or a["unit_price"] <= 0 or a["currency"] != "EUR" or a["lead_time_days"] is None:
            continue
        net = a["unit_price"]
        if a["vat_included"]:
            if a["vat_rate"] is None:
                continue
            net = net / (1 + a["vat_rate"] / 100)
        net = round(net, 4)
        quantity = min(a["quantity"], wanted)
        delivery = _add_days(START, a["lead_time_days"], bool(a["working_days"]))
        found[reply["supplier"]] = {
            "full": a["quantity"] >= wanted, "quantity": quantity, "net": net, "total": round(net * quantity, 2),
            "lead": (delivery - START).days, "lead_time_days": a["lead_time_days"], "working": bool(a["working_days"]),
        }  # fmt: skip
    return found


def oracle_winner(item: dict) -> str | None:
    found = _oracle_offers(item)
    ranked = sorted(found, key=lambda s: (not found[s]["full"], found[s]["total"], found[s]["lead"], s))
    return ranked[0] if ranked else None


# Runner, grader and critical errors.


def _recordings(item: dict, folder: Path, conn) -> Path:
    """Hand-written recordings: the stored answer of every reply, keyed on the exact prompt the module builds."""
    request = quote_decision.request_row(conn, 1)
    path = folder / "offers.jsonl"
    client = llm.ModelClient("replay", [], recordings_path=path, task=llm.OFFER_EXTRACTION)
    rows = []
    for reply in item["replies"]:
        text = quote_decision.model_text(request, reply["body"])
        key = llm.recording_key(client.system_prompt, text, llm.OFFER_EXTRACTION)
        rows.append({"key": key, "case_id": item["id"], "sentence": text, "answer": reply["answer"]})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    return path


def _setup(item: dict, folder: Path) -> dict:
    paths = {
        "db": folder / "business.db", "checkpoints": folder / "checkpoints.sqlite", "outbox": folder / "supplier_outbox",
    }  # fmt: skip
    conn = db.connect(paths["db"])
    try:
        db.seed(conn)
        request = item["request"]
        conn.execute(
            "INSERT INTO quote_requests (id, sku, quantity, thread_id, status, created_at, reminder_at, deadline) "
            "VALUES (1, ?, ?, 'quote-QR-0001', ?, '2026-10-12 09:00:00', '2026-10-15 09:00:00', '2026-10-17 09:00:00')",
            (request["sku"], request["quantity"], request["status"]),
        )
        replied = {r["supplier"]: r for r in item["replies"]}
        for code in SUPPLIER_CODES:
            path = None
            if code in replied:
                path = folder / f"{REF}-{code}.eml"
                path.write_text(
                    f"From: ventas@{code.lower()}.example.com\nTo: purchasing@distributor.example.com\n"
                    f"Subject: Re: Quote request {REF} - {request['sku']}\n\n{replied[code]['body']}\n",
                    encoding="utf-8",
                )
            conn.execute(
                "INSERT INTO quote_request_suppliers (request_id, supplier_code, sent_at, replied_at, reply_file) "
                "VALUES (1, ?, 't', ?, ?)",
                (code, "t" if path else None, str(path) if path else None),
            )
        conn.commit()
        paths["recordings"] = _recordings(item, folder, conn)
    finally:
        conn.close()
    return paths


def _argv(step: dict, paths: dict) -> list[str]:
    common = ["--db", str(paths["db"])]
    checkpoints = ["--checkpoints", str(paths["checkpoints"])]
    if step["op"] == "advance":
        return [*common, "clock", "advance", "--days", str(step["days"])]
    if step["op"] == "decide":
        return [*common, "quotes", "decide", "--recordings", str(paths["recordings"]), *checkpoints]
    if step["op"] == "approve":
        supplier = ["--supplier", step["supplier"]] if "supplier" in step else []
        return [*common, "quotes", "approve", REF, *supplier, *checkpoints, "--outbox", str(paths["outbox"])]
    return [*common, "quotes", "reject", REF, *checkpoints]


def _run_step(step: dict, paths: dict) -> tuple[int, str, str]:
    """The exit code, the printed output and the error output of one step."""
    argv = _argv(step, paths)
    if step.get("new_process"):
        run = [sys.executable, "-m", "purchase_cycle.cli", *argv]
        done = subprocess.run(run, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout, done.stderr
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def _printed(output: str) -> dict | None:
    """The recommendation `quotes decide` printed for the request, or None when it printed none."""
    found = re.search(r"recommended: (\S+)\n\s+reason: (.*)", output)
    if not found:
        return None
    return {"supplier": None if found.group(1) == "none" else found.group(1), "reason": found.group(2)}


def _measure(paths: dict) -> dict:
    """The request status, the purchase orders, the offers, the `failures` count and the outbox names."""
    conn = db.connect(paths["db"])
    try:
        status = conn.execute("SELECT status FROM quote_requests WHERE id = 1").fetchone()[0]
        orders = [
            {"number": f"PO-{r['id']:04d}", "supplier": r["supplier_code"], "quantity": r["quantity"],
             "unit_price": r["unit_price"], "currency": r["currency"], "total": r["total"],
             "expected_delivery": r["expected_delivery"], "issued_at": r["issued_at"]}
            for r in conn.execute("SELECT * FROM purchase_orders ORDER BY id")
        ]  # fmt: skip
        offers = {r["supplier_code"]: dict(r) for r in conn.execute("SELECT * FROM quote_offers")}
        failures = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    finally:
        conn.close()
    outbox = sorted(p.name for p in paths["outbox"].glob("*.eml")) if paths["outbox"].is_dir() else []
    return {"status": status, "orders": orders, "offers": offers, "failures": failures, "outbox": outbox}


def _errors(item: dict, final: dict, log: list[dict]) -> dict[str, int]:
    """Critical errors read from the final state, the printed recommendation and the step log."""
    counts = dict.fromkeys(CRITICAL_KINDS, 0)
    orders, status = final["orders"], final["status"]
    counts["multiple_orders"] = max(0, len(orders) - 1) + max(0, len(final["outbox"]) - len(orders))
    approved = any(s["op"] == "approve" and s["code"] == 0 for s in log)
    if orders and (status in UNUSABLE_STATUSES or not approved):
        counts["order_without_approval"] = 1
    printed = next((s["printed"] for s in log if s["printed"]), None)
    chosen = next((s["supplier"] for s in log if s["op"] == "approve" and s["code"] == 0 and s["supplier"]), None)
    chosen = chosen or (printed or {}).get("supplier")
    for order in orders:
        if not final["offers"].get(order["supplier"], {}).get("usable"):
            counts["order_from_unusable_offer"] += 1
        want = _oracle_offers(item).get(order["supplier"])
        issued = datetime.strptime(order["issued_at"], "%Y-%m-%d %H:%M:%S")
        due = _add_days(issued, want["lead_time_days"], want["working"]).strftime("%Y-%m-%d") if want else None
        if (
            want is None or order["supplier"] != chosen or order["quantity"] != want["quantity"]
            or abs(order["unit_price"] - want["net"]) > 1e-9 or order["expected_delivery"] != due
        ):  # fmt: skip
            counts["order_differs_from_offer"] += 1
    if printed is not None and printed["supplier"] != oracle_winner(item):
        counts["recommendation_differs_from_rule"] = 1
    counts["status_order_mismatch"] = int((status == "ordered") != bool(orders))
    counts["model_call_on_rerun"] = sum(NO_RECORDING in s["err"] for s in log)
    return counts


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    folder.mkdir(parents=True)
    paths = _setup(item, folder)
    log, decided = [], False
    for step in item["steps"]:
        code, out, err = _run_step(step, paths)
        log.append({"op": step["op"], "supplier": step.get("supplier"), "code": code, "err": err,
                    "printed": _printed(out) if step["op"] == "decide" else None})  # fmt: skip
        if step["op"] == "decide" and code == 0 and not decided:
            # Every later step must run with no model: a call now fails on the missing recording and is counted.
            decided = True
            paths["recordings"].unlink()
    final = _measure(paths)
    printed = next((s["printed"] for s in log if s["printed"]), None)
    expected = item["expected"]
    reason_ok = expected["rule"] is None or (printed is not None and RULE_TEXT[expected["rule"]] in printed["reason"])
    shown = [{k: v for k, v in o.items() if k != "issued_at"} for o in final["orders"]]
    measured = {
        "status": final["status"], "recommended": (printed or {}).get("supplier"), "orders": shown,
        "outbox": final["outbox"], "failures": final["failures"], "exits": [s["code"] for s in log],
    }  # fmt: skip
    keys = ("status", "recommended", "orders", "outbox", "failures")
    correct = (
        {k: measured[k] for k in keys} == {k: expected[k] for k in keys}
        and measured["exits"] == [s["exit"] for s in item["steps"]]
        and reason_ok
    )
    return {"id": item["id"], "tags": item["tags"], "expected": expected, **measured, "reason_ok": reason_ok,
            "correct": correct, "critical": _errors(item, final, log)}  # fmt: skip


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
    # At 100% success the zero-event rate is the failed scenarios, so its Wilson upper bound follows the row.
    failed = zero_event_note(_rate([{"correct": not r["correct"]} for r in results]))
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    errors = critical_totals(results)
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: hand-written recordings, no model call, no split, no baseline)")  # fmt: skip
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
            print(f"  {tag:<18} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        got = {k: r[k] for k in ("status", "recommended", "orders", "outbox", "failures", "exits", "reason_ok")}
        print(f"  {r['id']}  expected {r['expected']}  got {got}")
    code = 0
    if failures:
        print(f"GATE FAILED: scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        code = 1
    return max(code, critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} quote decision scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
