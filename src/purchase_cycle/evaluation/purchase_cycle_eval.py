"""Deterministic `purchase_cycle` evaluation (phase 12, D4, C8, C1).

Each scenario is one versioned JSON file: the orders written to the order inbox, the scripted steps (cycle passes, a
crash after a stage, the simulated clock, supplier replies and documents, signed notes, and the decisions a person
takes with the existing commands) and the expected final state written by hand from the rules of the cycle.

The runner builds two temporary worlds from the same inputs and runs the same steps on both: `cycle run` on one and the
module commands one by one (`route`, `quotes ...`, `reconcile ...`, `receipts run`, `sales ...`) on the other. The grader
reads the shared database (every row), the folders, the `failures` table and the printed output of `cycle run`. No model
is called: the model client is a test double with hand-written answers that counts its calls, and the real client is
replaced by a counter that records any call.
The gate is 100% scenario success and 0 critical errors.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.purchase_cycle_eval`.
"""

import contextlib
import io
import json
import re
import sqlite3
import tempfile
from email.message import EmailMessage
from pathlib import Path

from purchase_cycle import cli, cycle, cycle_cli, db, llm, sales_cli
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.llm import (
    CLAIM_DRAFTING,
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    OFFER_EXTRACTION,
    RESPONSE_DRAFTING,
    SUPPLIER_DOCUMENT_EXTRACTION,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
)

SUITE = "purchase_cycle"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "purchase_cycle"
TAGS = (
    "served_from_stock",
    "short_of_stock",
    "pause_clarification",
    "pause_quote_approval",
    "pause_claim",
    "pause_customer_response",
    "second_pass",
    "crash_resume",
    "restart",
    "rejected",
    "short_delivery",
)
MINIMUMS = {"scenarios": 16, **dict.fromkeys(TAGS, 2)}
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "decision_taken",
    "duplicate",
    "stage_out_of_order",
    "model_call",
    "final_state_differs",
)
INITIAL_STATE = {"seed": "db.seed: catalog, stock, customers and the clock; the stock of the short SKU set to zero"}
SHORT_SKU = "GLV-NIT-XS"
SUPPLIER = "SUP-002"
PHONE = "34600101201"
EMAIL_CUSTOMER = CUSTOMERS[1]
WHATSAPP_TEXT = "hi, can u send 4 boxes of nitrile gloves S thx"
EMAIL_LINE = "3 boxes of nitrile gloves L"
REPLY_TEXT = "We can supply 3 boxes at 4.10 EUR per box, delivery in 5 working days."
INVOICE_FORM = "INVOICE F-1\nPO-0001 gloves XS 3 boxes"
NOTE_FORM = "DELIVERY NOTE A-1\nPO-0001 gloves XS 3 boxes"
SIZES = ", ".join(f"Nitrile examination gloves, powder-free, size {s}" for s in ("XS", "S", "M", "L", "XL"))
QUESTION = f'For "nitrile gloves" (10 boxes), which one do you want: {SIZES}?'
ANSWERS = {  # hand-written model answers by task, keyed by a text inside the model input
    MATCHING.name: {
        "nitrile gloves M": {"sku": "GLV-NIT-M"},
        "nitrile gloves XS": {"sku": SHORT_SKU},
        "nitrile gloves": {"sku": None},
    },
    EMAIL_INTAKE.name: {"": {"is_order": True, "reason": "The email asks for gloves."}},
    EMAIL_EXTRACTION.name: {
        EMAIL_LINE: {"lines": [{"source": "body", "source_text": EMAIL_LINE, "sku": "GLV-NIT-L", "quantity": 3}]}
    },
    WHATSAPP_INTAKE.name: {"": {"is_order": True, "reason": "The message asks for gloves."}},
    WHATSAPP_EXTRACTION.name: {
        "nitrile gloves S": {
            "lines": [
                {"source": "message", "source_text": "4 boxes of nitrile gloves S", "sku": "GLV-NIT-S", "quantity": 4}
            ]
        }
    },
    CLARIFICATION_QUESTION.name: {"": {"question": QUESTION}},
    CLARIFICATION_ANSWER.name: {
        "": {"resolutions": [{"line_id": 1, "action": "set", "sku": "GLV-NIT-L", "quantity": 10}]}
    },
    OFFER_EXTRACTION.name: {
        REPLY_TEXT: {
            "offers": True,
            "quantity": 3,
            "unit_price": 4.1,
            "currency": "EUR",
            "vat_included": False,
            "vat_rate": None,
            "lead_time_days": 5,
            "working_days": True,
            "price_text": "4.10 EUR per box",
        }
    },
    SUPPLIER_DOCUMENT_EXTRACTION.name: {},
    CLAIM_DRAFTING.name: {"": {"body": "Dear supplier, please review the invoice."}},
    RESPONSE_DRAFTING.name: {"": {"body": "Dear customer, we are sorry."}},
}
DECIDED = {  # the statuses that only a person's decision gives, by table
    "quote_requests": ("ordered", "rejected"),
    "claims": ("sent", "rejected"),
    "customer_responses": ("sent", "rejected"),
    "clarifications": ("answered", "closed"),
}
WALL_CLOCK_TABLES = ("orders", "clarifications", "failures")  # created_at and updated_at come from the real clock
OUTPUT_FOLDERS = (
    "outbox",
    "supplier_outbox",
    "customer_outbox",
    "notes",
    "invoices",
)  # the files a pass or a decision writes
STATUS_TABLES = ("orders", "quote_requests", "claims", "customer_responses", "clarifications")
COUNT_TABLES = ("purchase_orders", "customer_invoices", "failures")

# ---------------------------------------------------------------------------------------------------------------
# The scenarios: inputs, steps and the expected final state, written by hand.

WEB_M = {"kind": "web_form", "file": "a-form.json", "product": "nitrile gloves M", "quantity": 2}
WEB_XS = {"kind": "web_form", "file": "b-short.json", "product": "nitrile gloves XS", "quantity": 3}
WEB_AMBIGUOUS = {"kind": "web_form", "file": "c-doubt.json", "product": "nitrile gloves", "quantity": 10}
EMAIL = {"kind": "email", "file": "d-email.eml"}
WHATSAPP = {"kind": "whatsapp", "file": "e-whatsapp.json"}
MIX = [WEB_M, EMAIL, WHATSAPP]
DOUBT_THREAD = "web_form-c0001-c-doubt"  # the thread id of the first pass over the file of WEB_AMBIGUOUS

PASS = {"do": "pass"}
AGAIN = {"do": "pass", "repeat": True}


def crash(stage) -> dict:
    return {"do": "pass", "crash": stage}


def process(step) -> dict:
    """The same step run by a new `python -m purchase_cycle.cli` process (the cycle world only)."""
    return {**step, "process": True}


def reply(request=1) -> dict:
    return {"do": "reply", "request": request}


def wait(days=6) -> dict:
    return {"do": "clock", "days": days}


def decide(command, ref=1, **extra) -> dict:
    return {"do": "decide", "command": command, "ref": ref, **extra}


def docs(n=1, price=4.1, invoice_quantity=3, note_quantity=3) -> dict:
    return {
        "do": "documents",
        "n": n,
        "price": price,
        "invoice_quantity": invoice_quantity,
        "note_quantity": note_quantity,
    }


def signed(note=1, incidents=None) -> dict:
    return {"do": "signed", "note": note, "incidents": incidents}


def want(orders=(), quotes=(), po=0, claims=(), invoices=0, sales=(), responses=(), clarifications=(), pending=()):
    return {
        "orders": list(orders),
        "quote_requests": list(quotes),
        "purchase_orders": po,
        "claims": list(claims),
        "customer_invoices": invoices,
        "sales_results": list(sales),
        "customer_responses": list(responses),
        "clarifications": list(clarifications),
        "failures": 0,
        "pending": [list(p) for p in pending],
    }


CASES = [
    {  # PC-001: one web form order in stock is served in one pass and a second pass changes nothing
        "orders": [WEB_M],
        "steps": [PASS, AGAIN],
        "expected": want(orders=["served"], invoices=1),
    },
    {  # PC-002: a pass stopped after the orders stage resumes with no model call and ends as a whole pass
        "orders": [WEB_M, EMAIL],
        "steps": [crash("orders"), PASS],
        "expected": want(orders=["served", "served"], invoices=2),
    },
]

# ---------------------------------------------------------------------------------------------------------------
# The dataset.


def derive_tags(item: dict) -> list[str]:
    steps = item["steps"]
    tags = []
    if any(s.get("repeat") for s in steps):
        tags.append("second_pass")
    if any(s.get("crash") for s in steps):
        tags.append("crash_resume")
    if any(s.get("process") for s in steps):
        tags.append("restart")
    expected = item["expected"]
    if expected["orders"] and not expected["quote_requests"]:
        tags.append("served_from_stock")
    if expected["quote_requests"]:
        tags.append("short_of_stock")
    if "awaiting_approval" in expected["quote_requests"]:
        tags.append("pause_quote_approval")
    if "pending" in expected["clarifications"]:
        tags.append("pause_clarification")
    if "draft" in expected["claims"]:
        tags.append("pause_claim")
    if "draft" in expected["customer_responses"]:
        tags.append("pause_customer_response")
    if any(s.get("command", "").startswith("reject") for s in steps):
        tags.append("rejected")
    return tags


def coverage(items: list[dict]) -> dict[str, int]:
    return {tag: sum(tag in item["tags"] for item in items) for tag in TAGS}


def build_items() -> list[dict]:
    items = []
    for n, spec in enumerate(CASES, start=1):
        item = {
            "id": f"PC-{n:03d}",
            "dataset_version": DATASET_VERSION,
            "initial_state": INITIAL_STATE,
            "orders": spec["orders"],
            "steps": spec["steps"],
            "expected": spec["expected"],
        }
        item["tags"] = derive_tags(item)
        items.append(item)
    return items


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("PC-*.json"))]


# ---------------------------------------------------------------------------------------------------------------
# The runner: two worlds from the same inputs, `cycle run` on one and the module commands on the other.

ACTIVE_CALLS: list[int] = []  # calls of the real model client (any call is a critical error)


class CountingClient:
    """A test double of the model client: hand-written answers, one shared call list."""

    def __init__(self, task, calls):
        self.task, self.calls_list = task, calls
        self.calls, self.usage = 0, []

    def extract(self, sentence, case_id=None, correction=None):
        self.calls_list.append(self.task.name)
        self.calls += 1
        for key, answer in ANSWERS[self.task.name].items():
            if key in sentence:
                return self.task.answer.model_validate(answer)
        raise AssertionError(f"no hand-written answer for {self.task.name}: {sentence!r}")

    def save_recordings(self):
        return 0


def _write_world(root: Path, orders: list[dict]) -> None:
    for name in ("orders", "replies", "documents", "signed"):
        (root / name).mkdir(parents=True)
    for spec in orders:
        path = root / "orders" / spec["file"]
        if spec["kind"] == "web_form":
            form = {
                "submission_id": f"WF-{spec['file']}",
                "customer_code": "CLI-001",
                "lines": [{"product": spec["product"], "quantity": spec["quantity"]}],
            }
            path.write_text(json.dumps(form), encoding="utf-8")
        elif spec["kind"] == "email":
            message = EmailMessage()
            message["From"], message["To"], message["Subject"] = EMAIL_CUSTOMER.email, "orders@example.com", "Order"
            message.set_content(f"Hello,\nPlease send {EMAIL_LINE}.\nThanks")
            path.write_bytes(bytes(message))
        else:
            body = {"message_id": "wamid.CYCLE1", "from": PHONE, "timestamp": "1760000000", "type": "text"}
            body["text"] = {"body": WHATSAPP_TEXT}
            path.write_text(json.dumps(body), encoding="utf-8")
    conn = db.connect(root / "business.db")
    try:
        db.seed(conn)
        conn.execute("UPDATE stock SET on_hand = 0 WHERE sku = ?", (SHORT_SKU,))
        conn.commit()
    finally:
        conn.close()


def _mask(root: Path, text: str) -> str:
    return text.replace(str(root), "<root>").replace(root.as_posix(), "<root>")


def _snapshot(root: Path) -> dict:
    """Every row of every table and every output file, with the run folder and the wall-clock stamps masked."""
    conn = sqlite3.connect(root / "business.db")
    rows = {}
    try:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            names = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
            stamps = {n for n in names if table in WALL_CLOCK_TABLES and n in ("created_at", "updated_at")}
            rows[table] = [
                [
                    "<wall-clock>" if n in stamps else _mask(root, v) if isinstance(v, str) else v
                    for n, v in zip(names, row, strict=True)
                ]
                for row in conn.execute(f"SELECT * FROM {table}")
            ]
    finally:
        conn.close()
    files = {}
    for folder in OUTPUT_FOLDERS:
        base = root / folder
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.is_file():
                files[f"{folder}/{path.relative_to(base).as_posix()}"] = _mask(root, path.read_text(encoding="utf-8"))
    return {"rows": rows, "files": files}


def _query(root: Path, sql: str) -> list:
    conn = sqlite3.connect(root / "business.db")
    try:
        return [row[0] for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _decided(root: Path) -> int:
    total = 0
    for table, statuses in DECIDED.items():
        listed = ", ".join(f"'{s}'" for s in statuses)
        total += _query(root, f"SELECT COUNT(*) FROM {table} WHERE status IN ({listed})")[0]
    return total


def _folders(root: Path) -> cycle.Folders:
    return cycle.Folders(
        root / "orders",
        root / "replies",
        root / "documents",
        root / "signed",
        root / "outbox",
        root / "supplier_outbox",
        root / "notes",
        root / "invoices",
    )


def _pending(lines: list[str]) -> list[list[str]]:
    found = []
    for line in lines:
        match = re.match(r"^  (\w+) (\S+)$", line)
        if match and match.group(1) not in cycle.STAGES:
            found.append([match.group(1), match.group(2)])
    return found


def _cycle_pass(root: Path, stage, calls: list[str]) -> tuple[int, list[str], list[list[str]]]:
    """One pass of `cycle run` (or a pass stopped after `stage`): exit code, printed stages, pending decisions."""
    if stage:
        result = cycle.run_cycle(
            root / "business.db",
            root / "cp.sqlite",
            _folders(root),
            lambda task, catalog: CountingClient(task, calls),
            [stage],
        )
        return 0, [], [[p["kind"], p["reference"]] for p in result["pending"]]
    argv = ["--db", str(root / "business.db"), "cycle", "run"]
    flags = (
        ("--orders", "orders"),
        ("--replies", "replies"),
        ("--documents", "documents"),
        ("--signed-notes", "signed"),
        ("--outbox", "outbox"),
        ("--supplier-outbox", "supplier_outbox"),
        ("--delivery-notes", "notes"),
        ("--invoices", "invoices"),
    )
    for flag, name in flags:
        argv += [flag, str(root / name)]
    argv += ["--checkpoints", str(root / "cp.sqlite")]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    lines = out.getvalue().splitlines()
    stages = [m.group(1) for line in lines if (m := re.match(r"^  (\w+):", line)) and m.group(1) in cycle.STAGES]
    return code, stages, _pending(lines)


def _reference(root: Path) -> None:
    """The module commands one by one over the same folders, in the order of the cycle."""
    r, cp = root, str(root / "cp.sqlite")
    steps = [
        ["route", str(r / "orders"), "--run-id", "c0001", "--checkpoints", cp, "--outbox", str(r / "outbox")],
        ["quotes", "scan", "--checkpoints", cp, "--outbox", str(r / "supplier_outbox")],
        ["quotes", "receive", str(r / "replies"), "--checkpoints", cp],
        ["quotes", "tick", "--checkpoints", cp, "--outbox", str(r / "supplier_outbox")],
        ["quotes", "decide", "--checkpoints", cp],
        ["reconcile", "scan", str(r / "documents"), "--checkpoints", cp],
        ["reconcile", "claims", "--checkpoints", cp],
        ["receipts", "run", "--checkpoints", cp, "--notes-dir", str(r / "notes")],
        ["sales", "scan", str(r / "signed"), "--checkpoints", cp, "--invoices-dir", str(r / "invoices")],
        ["sales", "responses", "--checkpoints", cp],
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for step in steps:
            if cli.main(["--db", str(r / "business.db"), *step]) != 0:
                raise RuntimeError(f"reference command failed: {step}")


def _factory(calls: list[str]):
    return lambda mode, catalog, recordings=None, task=None: CountingClient(task, calls)


def measure(item: dict, tmp: Path) -> dict:
    """Run the scenario on both worlds and return what the grader needs."""
    cycle_root, ref_root = tmp / "cycle", tmp / "ref"
    for root in (cycle_root, ref_root):
        _write_world(root, item["orders"])
    cycle_calls: list[str] = []
    ref_calls: list[str] = []
    passes, pending, ref_done, previous = [], [], False, None
    for step in item["steps"]:
        if step["do"] != "pass":
            raise ValueError(f"step not supported yet: {step['do']}")
        if step.get("crash"):
            kind = "crash"
        elif step.get("repeat"):
            kind = "repeat"
        else:
            kind = "resume" if previous == "crash" else "pass"
        before, decided_before, calls_before = _snapshot(cycle_root), _decided(cycle_root), len(cycle_calls)
        saved = cycle_cli.ModelClient
        cycle_cli.ModelClient = _factory(cycle_calls)
        try:
            code, stages, pending = _cycle_pass(cycle_root, step.get("crash"), cycle_calls)
        finally:
            cycle_cli.ModelClient = saved
        passes.append(
            {
                "kind": kind,
                "calls": len(cycle_calls) - calls_before,
                "stages": stages,
                "decided": [decided_before, _decided(cycle_root)],
                "changed": before != _snapshot(cycle_root),
                "rc": code,
            }
        )
        if kind in ("pass", "resume") and not ref_done:
            ref_done = True
            patched = (cli.ModelClient, sales_cli.ModelClient)
            cli.ModelClient = sales_cli.ModelClient = _factory(ref_calls)
            try:
                _reference(ref_root)
            finally:
                cli.ModelClient, sales_cli.ModelClient = patched
        previous = kind
    return {
        "passes": passes,
        "violations": len(_query(cycle_root, "PRAGMA foreign_key_check")),
        "cycle": _snapshot(cycle_root),
        "ref": _snapshot(ref_root),
        "calls": len(cycle_calls),
        "ref_calls": len(ref_calls),
        "pending": pending,
        "real_calls": len(ACTIVE_CALLS),
        "statuses": {t: _query(cycle_root, f"SELECT status FROM {t} ORDER BY rowid") for t in STATUS_TABLES},
        "counts": {t: _query(cycle_root, f"SELECT COUNT(*) FROM {t}")[0] for t in COUNT_TABLES},
    }


def _got(measured: dict) -> dict:
    statuses, counts = measured["statuses"], measured["counts"]
    return {
        "orders": statuses["orders"],
        "quote_requests": statuses["quote_requests"],
        "purchase_orders": counts["purchase_orders"],
        "claims": statuses["claims"],
        "customer_invoices": counts["customer_invoices"],
        "sales_results": [],
        "customer_responses": statuses["customer_responses"],
        "clarifications": statuses["clarifications"],
        "failures": counts["failures"],
        "pending": measured["pending"],
    }


def _errors(item: dict, measured: dict) -> dict[str, int]:
    """The critical errors of one scenario, counted from the measured passes and the two final states."""
    errors = dict.fromkeys(CRITICAL_KINDS, 0)
    passes = measured["passes"]
    errors["decision_taken"] = sum(p["decided"][1] > p["decided"][0] for p in passes)
    errors["duplicate"] = sum(p["changed"] for p in passes if p["kind"] == "repeat")
    errors["stage_out_of_order"] = sum(p["stages"] != list(cycle.STAGES) for p in passes if p["stages"]) + int(
        bool(measured["violations"])
    )
    again = [p for p in passes if p["kind"] in ("repeat", "resume")]
    errors["model_call"] = sum(bool(p["calls"]) or p["rc"] != 0 for p in again) + measured["real_calls"]
    errors["final_state_differs"] = int(
        measured["cycle"] != measured["ref"] or measured["calls"] != measured["ref_calls"]
    )
    return errors


def grade(item: dict, measured: dict) -> dict:
    got = _got(measured)
    got = {key: got[key] for key in item["expected"]}
    return {
        "id": item["id"],
        "tags": item["tags"],
        "expected": item["expected"],
        "got": got,
        "correct": got == item["expected"] and not any(p["rc"] for p in measured["passes"]),
        "critical": _errors(item, measured),
    }


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    folder.mkdir(parents=True)
    ACTIVE_CALLS.clear()
    return grade(item, measure(item, folder))


def critical_totals(results: list[dict]) -> dict[str, int]:
    return {kind: sum(r["critical"][kind] for r in results) for kind in CRITICAL_KINDS}


def print_critical(errors: dict[str, int], n: int) -> None:
    total = sum(errors.values())
    target, met = target_cells(total, 0, "<=")
    print(
        f"{'critical_errors':<26}{total:<8}{'-':<16}{n:<6}{UNIT:<16}{'=0':<9}{met:<12}{'0':<11}"
        f"{'PASS' if total == 0 else 'FAIL'}"
    )
    print("  by kind: " + "  ".join(f"{kind}={errors[kind]}" for kind in CRITICAL_KINDS))


def critical_gate(errors: dict[str, int], results: list[dict]) -> int:
    if not sum(errors.values()):
        return 0
    for r in results:
        found = {kind: c for kind, c in r["critical"].items() if c}
        if found:
            print(f"  {r['id']}  critical errors {found}")
    print(f"GATE FAILED: critical_errors {sum(errors.values())} above threshold 0")
    return 1


def _count_calls(target=llm.ModelClient):
    """Replace the real model client entry points by counters for the whole run; returns the restore function."""
    saved = (target.__init__, target._call_model)

    def counted(*args, **kwargs):
        ACTIVE_CALLS.append(1)
        raise llm.MissingRecording("purchase_cycle makes no model call")

    target.__init__ = counted
    target._call_model = counted

    def restore():
        target.__init__, target._call_model = saved

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
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: scripted passes, no model call, no split, no baseline)")  # fmt: skip
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
    print(f"Built {len(items)} purchase cycle scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
