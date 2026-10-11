"""Deterministic `quote_wait` evaluation (phase 06, D7, C11).

Each scenario is one versioned JSON file: a scripted timeline of orders, scans, clock advances, supplier replies
and ticks run through the CLI functions on a temporary database, with the expected final state of every quote
request, the `failures` rows and the supplier outbox. Every expectation is written by hand from the D5 schedule:
a reminder at the first tick on or after day 3 to each supplier that has not replied, a close at the first tick
on or after day 5, and an early close when every supplier replied.

The runner seeds a temporary database per timeline and runs each step through `cli.main` (a `new_process` step
through `python -m purchase_cycle.cli`). The grader compares the final requests, the `needs_review` failures rows
of step `quote_wait` and the outbox file names with the expectation; a snapshot before and after each step feeds
the critical-error counter. The gate is 100% success and 0 critical errors.

Build the versioned timeline files with `uv run python -m purchase_cycle.evaluation.quote_wait_eval`.
"""

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from purchase_cycle import cli, db, quotes, sim_clock
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.suppliers import SUPPLIER_FAMILIES, SUPPLIERS

SUITE = "quote_wait"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "quote_wait"
OPS = ("order", "scan", "advance", "receive", "tick")
TAGS = ("all_replied", "some_replied", "none_replied", "late_reply", "duplicate_reply", "rescan", "repeated_tick",
        "restart")  # fmt: skip
# C11 minimums: timelines in all and per coverage tag.
MINIMUMS = {"timelines": 20, **dict.fromkeys(TAGS, 3)}
TARGET = 1.0  # deterministic: every timeline must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "second_open_request",
    "request_without_shortfall",
    "wrong_quantity",
    "reminder_after_reply",
    "repeated_reminder",
    "email_after_close",
    "waiting_after_deadline",
    "lost_or_duplicated_thread",
)
OUTBOX_NAME = re.compile(r"(QR-\d+)-(request|reminder)-(.+)\.eml")
INITIAL_STATE = {"seed": "db.seed: catalog, stock, customers, suppliers and the clock at 2026-10-12 09:00"}

# Seeded stock is (i * 37) % 250 over the catalog order; each order below asks stock plus the shortfall.
A, B, C, D = "GLV-NIT-XS", "GLV-LTX-L", "GLV-VIN-L", "GLV-SURG-NEO-6.5"  # stock 0, 18, 27 and 36
STOCK = {p.sku: (i * 37) % 250 for i, p in enumerate(PRODUCTS)}
FAMILY = {p.sku: p.family for p in PRODUCTS}
S1, S2, S3 = "SUP-001", "SUP-002", "SUP-003"


def _suppliers(sku: str) -> list[str]:
    return sorted(code for code, family in SUPPLIER_FAMILIES if family == FAMILY[sku])


def order(*shorts: tuple[str, int]) -> dict:
    """One `received` order leaving each SKU short by the given units (or more short, on top of earlier orders)."""
    return {"op": "order", "lines": [[sku, STOCK[sku] + units] for sku, units in shorts]}


def extra(*lines: tuple[str, int]) -> dict:
    """One more `received` order with the given quantities."""
    return {"op": "order", "lines": [list(line) for line in lines]}


def scan(**flags) -> dict:
    return {"op": "scan", **flags}


def tick(**flags) -> dict:
    return {"op": "tick", **flags}


def advance(days: int = 0, hours: int = 0, **flags) -> dict:
    return {"op": "advance", **({"days": days} if days else {"hours": hours}), **flags}


def receive(*replies: tuple[str, str], **flags) -> dict:
    """One folder of reply files, one per (reference, supplier code), read by `quotes receive`."""
    return {"op": "receive", "replies": [{"reference": r, "supplier": s} for r, s in replies], **flags}


def request(ref: str, sku: str, quantity: int, status: str, replied=(), reminded=()) -> dict:
    return {"reference": ref, "sku": sku, "quantity": quantity, "status": status, "replied": sorted(replied),
            "reminded": sorted(reminded)}  # fmt: skip


READY, NONE, WAITING = "ready_for_decision", "no_offers", "waiting"

# (tags, steps, expected requests), in a fixed order; the outbox and `failures` follow from the requests.
TIMELINES = [
    # All suppliers reply before the deadline: the request closes at once.
    (["all_replied"],
     [order((A, 10)), scan(), advance(1), receive(("QR-0001", S1), ("QR-0001", S2), ("QR-0001", S3))],
     [request("QR-0001", A, 10, READY, [S1, S2, S3])]),
    (["all_replied"],
     [order((B, 5)), scan(), advance(1), receive(("QR-0001", S2)), advance(1), receive(("QR-0001", S3)),
      advance(2), tick()],
     [request("QR-0001", B, 5, READY, [S2, S3])]),
    (["all_replied"],
     [order((C, 7)), scan(), advance(3), tick(), advance(1), receive(("QR-0001", S1), ("QR-0001", S3))],
     [request("QR-0001", C, 7, READY, [S1, S3], [S1, S3])]),
    # Some suppliers reply: the deadline tick closes the request ready for the decision.
    (["some_replied"],
     [order((A, 10)), scan(), advance(1), receive(("QR-0001", S2)), advance(2), tick(), advance(2), tick()],
     [request("QR-0001", A, 10, READY, [S2], [S1, S3])]),
    (["some_replied"],
     [order((B, 5)), scan(), advance(4), receive(("QR-0001", S3)), advance(1), tick()],
     [request("QR-0001", B, 5, READY, [S3])]),
    (["some_replied"],  # S3 does not sell surgical gloves, so its reply is rejected
     [order((D, 4)), scan(), advance(1), receive(("QR-0001", S3), ("QR-0001", S1)), advance(2), tick(),
      advance(2), tick()],
     [request("QR-0001", D, 4, READY, [S1], [S2])]),
    # No supplier replies: `no_offers` with one `failures` row.
    (["none_replied"],
     [order((A, 10)), scan(), advance(3), tick(), advance(2), tick()],
     [request("QR-0001", A, 10, NONE, [], [S1, S2, S3])]),
    (["none_replied"],  # the first tick is past the deadline: it closes the request with no reminder
     [order((C, 7)), scan(), advance(5), tick()],
     [request("QR-0001", C, 7, NONE)]),
    (["none_replied"],
     [order((B, 5), (D, 4)), scan(), advance(3), tick(), advance(3), tick()],
     [request("QR-0001", B, 5, NONE, [], [S2, S3]), request("QR-0002", D, 4, NONE, [], [S1, S2])]),
    # A reply after the closing tick is rejected and changes nothing.
    (["none_replied", "late_reply"],
     [order((A, 10)), scan(), advance(5), tick(), advance(1), receive(("QR-0001", S1))],
     [request("QR-0001", A, 10, NONE)]),
    (["some_replied", "late_reply"],
     [order((B, 5)), scan(), advance(1), receive(("QR-0001", S2)), advance(4), tick(), advance(hours=1),
      receive(("QR-0001", S3)), advance(1), tick()],
     [request("QR-0001", B, 5, READY, [S2])]),
    (["none_replied", "late_reply"],
     [order((C, 7)), scan(), advance(3), tick(), advance(2), tick(), receive(("QR-0001", S1), ("QR-0001", S3))],
     [request("QR-0001", C, 7, NONE, [], [S1, S3])]),
    # The same reply twice: the second one changes nothing.
    (["some_replied", "duplicate_reply"],
     [order((A, 10)), scan(), advance(1), receive(("QR-0001", S1)), receive(("QR-0001", S1)), advance(2), tick(),
      advance(2), tick()],
     [request("QR-0001", A, 10, READY, [S1], [S2, S3])]),
    (["all_replied", "duplicate_reply"],  # two copies of the same reply in one folder
     [order((B, 5)), scan(), advance(1), receive(("QR-0001", S2), ("QR-0001", S2)), advance(1),
      receive(("QR-0001", S3))],
     [request("QR-0001", B, 5, READY, [S2, S3])]),
    (["some_replied", "duplicate_reply"],
     [order((D, 4)), scan(), advance(3), tick(), advance(1), receive(("QR-0001", S2)), advance(hours=1),
      receive(("QR-0001", S2)), advance(hours=23), tick()],
     [request("QR-0001", D, 4, READY, [S2], [S1, S2])]),
    # A re-run scan starts nothing while the request waits; a closed request no longer blocks a new one.
    (["none_replied", "rescan"],
     [order((A, 10)), scan(), scan(), advance(1), scan(), advance(4), tick()],
     [request("QR-0001", A, 10, NONE)]),
    (["none_replied", "rescan"],  # the new B demand waits behind the open B request; C starts its own
     [order((B, 5)), scan(), extra((C, STOCK[C] + 7), (B, 3)), scan(), advance(5), tick()],
     [request("QR-0001", B, 5, NONE), request("QR-0002", C, 7, NONE)]),
    (["all_replied", "rescan"],
     [order((D, 4)), scan(), advance(1), receive(("QR-0001", S1), ("QR-0001", S2)), scan(), scan()],
     [request("QR-0001", D, 4, READY, [S1, S2]), request("QR-0002", D, 4, WAITING)]),
    # A tick repeated at the same time writes nothing new.
    (["none_replied", "repeated_tick"],
     [order((A, 10)), scan(), advance(3), tick(), tick(), advance(1), tick(), advance(1), tick(), tick()],
     [request("QR-0001", A, 10, NONE, [], [S1, S2, S3])]),
    (["some_replied", "repeated_tick"],
     [order((C, 7)), scan(), tick(), advance(2), tick(), tick(), advance(1), tick(), receive(("QR-0001", S1)),
      tick(), advance(2), tick()],
     [request("QR-0001", C, 7, READY, [S1], [S1, S3])]),
    (["some_replied", "repeated_tick"],
     [order((B, 5)), scan(), advance(2), receive(("QR-0001", S3)), advance(1), tick(), tick(), advance(2), tick(),
      tick()],
     [request("QR-0001", B, 5, READY, [S3], [S2])]),
    # Steps run in a new process: the thread resumes from the SQLite checkpoint.
    (["some_replied", "restart"],
     [order((A, 10)), scan(new_process=True), advance(3), tick(new_process=True),
      receive(("QR-0001", S2), new_process=True), advance(2), tick(new_process=True)],
     [request("QR-0001", A, 10, READY, [S2], [S1, S2, S3])]),
    (["none_replied", "restart"],
     [order((D, 4)), scan(), advance(5, new_process=True), tick(new_process=True)],
     [request("QR-0001", D, 4, NONE)]),
    (["all_replied", "some_replied", "restart"],
     [order((B, 5), (C, 7)), scan(), advance(1),
      receive(("QR-0001", S2), ("QR-0001", S3), ("QR-0002", S1), new_process=True), advance(4),
      tick(new_process=True)],
     [request("QR-0001", B, 5, READY, [S2, S3]), request("QR-0002", C, 7, READY, [S1])]),
]  # fmt: skip


def _outbox(requests: list[dict]) -> list[str]:
    """One request email per family supplier and one reminder per reminded supplier."""
    names = []
    for r in requests:
        names += [f"{r['reference']}-request-{code}.eml" for code in _suppliers(r["sku"])]
        names += [f"{r['reference']}-reminder-{code}.eml" for code in r["reminded"]]
    return sorted(names)


def build_items() -> list[dict]:
    items = []
    for n, (tags, steps, requests) in enumerate(TIMELINES, start=1):
        items.append(
            {
                "id": f"QW-{n:03d}",
                "dataset_version": DATASET_VERSION,
                "tags": tags,
                "initial_state": INITIAL_STATE,
                "steps": steps,
                "expected": {
                    "requests": requests,
                    "failures": sum(r["status"] == NONE for r in requests),
                    "outbox": _outbox(requests),
                },
            }
        )
    return items


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("QW-*.json"))]


# Runner, grader and critical errors (increment 12).

EMAIL = {s.code: s.email for s in SUPPLIERS}


def _snapshot(paths: dict) -> dict:
    """Clock, outbox names, every request with its replied and reminded suppliers, and the shortfall per SKU.

    The shortfall is computed here from the `received` order lines and `on_hand`, apart from `quotes.shortfalls`.
    """
    outbox = paths["outbox"]
    conn = db.connect(paths["db"])
    try:
        requests = {}
        for r in conn.execute("SELECT * FROM quote_requests ORDER BY id").fetchall():
            suppliers = conn.execute(
                "SELECT supplier_code, reminded_at, replied_at FROM quote_request_suppliers WHERE request_id = ?",
                (r["id"],),
            ).fetchall()
            requests[quotes.reference(r["id"])] = {
                "sku": r["sku"],
                "quantity": r["quantity"],
                "status": r["status"],
                "deadline": r["deadline"],
                "thread_id": r["thread_id"],
                "replied": {s["supplier_code"] for s in suppliers if s["replied_at"] is not None},
                "reminded": {s["supplier_code"]: s["reminded_at"] for s in suppliers},
            }
        demand = conn.execute(
            "SELECT l.sku, SUM(l.quantity) - s.on_hand AS short FROM order_lines l "
            "JOIN orders o ON o.id = l.order_id JOIN stock s ON s.sku = l.sku "
            "WHERE o.status = 'received' GROUP BY l.sku"
        ).fetchall()
        return {
            "now": sim_clock.now(conn).strftime(sim_clock.FORMAT),
            "outbox": {p.name for p in outbox.glob("*.eml")} if outbox.is_dir() else set(),
            "requests": requests,
            "shortfall": {r["sku"]: r["short"] for r in demand if r["short"] > 0},
        }
    finally:
        conn.close()


def _excess_waiting(snapshot: dict) -> int:
    skus = [r["sku"] for r in snapshot["requests"].values() if r["status"] == WAITING]
    return sum(skus.count(sku) - 1 for sku in set(skus))


def _step_errors(before: dict, after: dict, step: dict) -> dict[str, int]:
    """Critical errors one step added, read from the snapshots before and after it."""
    counts = dict.fromkeys(CRITICAL_KINDS, 0)
    counts["second_open_request"] = max(0, _excess_waiting(after) - _excess_waiting(before))
    for ref, r in after["requests"].items():
        old = before["requests"].get(ref)
        if old is None:
            # A closed request no longer blocks demand still `received`, so only the shortfall size is checked.
            shortfall = before["shortfall"].get(r["sku"], 0)
            if shortfall <= 0:
                counts["request_without_shortfall"] += 1
            elif r["quantity"] != shortfall:
                counts["wrong_quantity"] += 1
            continue
        for code, at in r["reminded"].items():
            if old["reminded"].get(code) is not None and at != old["reminded"][code]:
                counts["repeated_reminder"] += 1
        if step["op"] == "tick" and r["status"] == WAITING and r["deadline"] <= after["now"]:
            counts["waiting_after_deadline"] += 1
    for name in sorted(after["outbox"] - before["outbox"]):
        found = OUTBOX_NAME.fullmatch(name)
        old = before["requests"].get(found.group(1)) if found else None
        if old is None:
            continue
        if old["status"] != WAITING:
            counts["email_after_close"] += 1
        elif found.group(2) == "reminder" and found.group(3) in old["replied"]:
            counts["reminder_after_reply"] += 1
    return counts


def _thread_errors(requests: dict, threads: dict) -> dict[str, int]:
    """A request thread missing from the checkpoints or not paused while waiting, and any thread of no request."""
    counts = dict.fromkeys(CRITICAL_KINDS, 0)
    owned = {r["thread_id"] for r in requests.values()}
    lost = [
        r for r in requests.values()
        if r["thread_id"] not in threads or (r["status"] == WAITING and not threads[r["thread_id"]])
    ]  # fmt: skip
    counts["lost_or_duplicated_thread"] = len(lost) + len(set(threads) - owned)
    return counts


def _threads(paths: dict) -> dict[str, tuple]:
    """The pending nodes of every checkpoint thread."""
    saver = sqlite_checkpointer(paths["checkpoints"])
    graph = quotes.build_quote_request_graph(paths["db"], paths["outbox"], checkpointer=saver)
    ids = sorted({c.config["configurable"]["thread_id"] for c in saver.list(None)})
    return {t: graph.get_state({"configurable": {"thread_id": t}}).next for t in ids}


def _argv(step: dict, n: int, paths: dict) -> list[str]:
    common = ["--db", str(paths["db"])]
    checkpoints = ["--checkpoints", str(paths["checkpoints"])]
    if step["op"] == "advance":
        unit = "days" if "days" in step else "hours"
        return [*common, "clock", "advance", f"--{unit}", str(step[unit])]
    if step["op"] == "receive":
        inbox = paths["folder"] / f"inbox-{n:02d}"
        inbox.mkdir()
        for k, reply in enumerate(step["replies"], start=1):
            (inbox / f"reply-{k:02d}.eml").write_text(
                f"From: {EMAIL[reply['supplier']]}\nTo: purchasing@distributor.example.com\n"
                f"Subject: Re: Quote request {reply['reference']}\n\nOur offer is attached.\n",
                encoding="utf-8",
            )
        return [*common, "quotes", "receive", str(inbox), *checkpoints]
    return [*common, "quotes", step["op"], *checkpoints, "--outbox", str(paths["outbox"])]


def _run_step(step: dict, n: int, paths: dict) -> int:
    if step["op"] == "order":
        conn = db.connect(paths["db"])
        try:
            db.insert_order(conn, CUSTOMERS[0].code, "web_form", "received", [tuple(line) for line in step["lines"]])
        finally:
            conn.close()
        return 0
    argv = _argv(step, n, paths)
    if step.get("new_process"):
        run = [sys.executable, "-m", "purchase_cycle.cli", *argv]
        return subprocess.run(run, capture_output=True, text=True, timeout=120).returncode
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cli.main(argv)


def _measure(paths: dict, snapshot: dict) -> dict:
    """The final requests, the parked `quote_wait` failures and the outbox names, shaped like the expectation."""
    conn = db.connect(paths["db"])
    try:
        failures = conn.execute(
            "SELECT COUNT(*) FROM failures WHERE status = 'needs_review' AND step = 'quote_wait'"
        ).fetchone()[0]
    finally:
        conn.close()
    requests = [
        {"reference": ref, "sku": r["sku"], "quantity": r["quantity"], "status": r["status"],
         "replied": sorted(r["replied"]), "reminded": sorted(c for c, at in r["reminded"].items() if at)}
        for ref, r in snapshot["requests"].items()
    ]  # fmt: skip
    return {"requests": requests, "failures": failures, "outbox": sorted(snapshot["outbox"])}


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    folder.mkdir(parents=True)
    paths = {
        "folder": folder,
        "db": folder / "business.db",
        "checkpoints": folder / "checkpoints.sqlite",
        "outbox": folder / "supplier_outbox",
    }
    conn = db.connect(paths["db"])
    try:
        db.seed(conn)
    finally:
        conn.close()
    errors, codes = dict.fromkeys(CRITICAL_KINDS, 0), []
    before = _snapshot(paths)
    for n, step in enumerate(item["steps"], start=1):
        codes.append(_run_step(step, n, paths))
        after = _snapshot(paths)
        for kind, c in _step_errors(before, after, step).items():
            errors[kind] += c
        before = after
    for kind, c in _thread_errors(before["requests"], _threads(paths)).items():
        errors[kind] += c
    measured = _measure(paths, before)
    correct = measured == item["expected"] and not any(codes)
    return {"id": item["id"], "tags": item["tags"], "expected": item["expected"], **measured, "codes": codes,
            "correct": correct, "critical": errors}  # fmt: skip


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
    """Exit 1 when any timeline holds a critical error, naming each one."""
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
    """Run every timeline and print the report; exit 1 below 100% success or with a critical error."""
    items = load_dataset(dataset_dir)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = [run_item(item, Path(tmp)) for item in items]
    s = _rate(results)
    # At 100% success the zero-event rate is the failed scenarios, so its Wilson upper bound follows the row.
    failed = zero_event_note(_rate([{"correct": not r["correct"]} for r in results]))
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    errors = critical_totals(results)
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: scripted timelines, no model call, no split, no baseline)")  # fmt: skip
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
            print(f"  {tag:<16} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        got = {k: r[k] for k in ("requests", "failures", "outbox", "codes")}
        print(f"  {r['id']}  expected {r['expected']}  got {got}")
    code = 0
    if failures:
        print(f"GATE FAILED: scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        code = 1
    return max(code, critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} quote wait timelines -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
