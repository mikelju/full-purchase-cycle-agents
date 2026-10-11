"""Deterministic `quote_wait` evaluation (phase 06, D7, C11).

Each scenario is one versioned JSON file: a scripted timeline of orders, scans, clock advances, supplier replies
and ticks run through the CLI functions on a temporary database, with the expected final state of every quote
request, the `failures` rows and the supplier outbox. Every expectation is written by hand from the D5 schedule:
a reminder at the first tick on or after day 3 to each supplier that has not replied, a close at the first tick
on or after day 5, and an early close when every supplier replied.

Build the versioned timeline files with `uv run python -m purchase_cycle.evaluation.quote_wait_eval`.
"""

import json
from pathlib import Path

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.suppliers import SUPPLIER_FAMILIES

SUITE = "quote_wait"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "quote_wait"
OPS = ("order", "scan", "advance", "receive", "tick")
TAGS = ("all_replied", "some_replied", "none_replied", "late_reply", "duplicate_reply", "rescan", "repeated_tick",
        "restart")  # fmt: skip
# C11 minimums: timelines in all and per coverage tag.
MINIMUMS = {"timelines": 20, **dict.fromkeys(TAGS, 3)}
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


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} quote wait timelines -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
