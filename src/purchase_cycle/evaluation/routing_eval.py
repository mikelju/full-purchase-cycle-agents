"""Deterministic `channel_routing` evaluation (phase 05, D7, C13, C15).

Each hand-built item is one inbox file plus the database state it meets: pending clarifications, stored order
sources, and what the paused threads' checkpoints hold (the message id each thread started from and the answers
it applied). The real `router.route` routes the file with no model call; an item is right when its route, the
thread it names and its rejection or duplicate reason are the expected ones. The gate is 100%.

Build the versioned dataset with `uv run python -m purchase_cycle.evaluation.routing_eval`.
"""

import json
import tempfile
from email.message import EmailMessage
from pathlib import Path

from purchase_cycle import db, router
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.evaluation.stats import target_cells, wilson_interval, zero_event_note
from purchase_cycle.whatsapp_order import phone_digits

SUITE = "channel_routing"
DATASET_VERSION = "1.0"
DATASET_PATH = EVALS_DIR / "datasets" / "channel_routing" / "dataset.jsonl"
TARGET = 1.0  # deterministic: every item must reach its route
UNIT = "item"
ROUTES = (
    router.WEB_FORM,
    router.EMAIL,
    router.WHATSAPP_NEW,
    router.WHATSAPP_ANSWER,
    router.DUPLICATE,
    router.REJECTED,
)


# ---------------------------------------------------------------------------------------------------------------------
# Dataset


def build_items() -> list[dict]:
    """The hand-built items, in a fixed order; every label follows from the envelope rules of the router."""
    a, b, c = CUSTOMERS[1], CUSTOMERS[2], CUSTOMERS[3]
    pa, pb = phone_digits(a.phone), phone_digits(b.phone)
    items: list[dict] = []

    def add(category, file, content, route, thread_id=None, reason="", **setup):
        n = len(items) + 1
        items.append(
            {
                "id": f"CR-{n:03d}",
                "dataset_version": DATASET_VERSION,
                "category": category,
                "file": file,
                **content,
                "setup": {
                    "pending": setup.get("pending", []),
                    "stored": setup.get("stored", []),
                    "paused_sources": setup.get("paused_sources", {}),
                    "applied_answers": setup.get("applied_answers", {}),
                },
                "expected": {"route": route, "thread_id": thread_id, "reason": reason},
            }
        )

    def wa(message_id, sender=pa, body="hi, 40 boxes of nitrile gloves M please", kind="text", **extra):
        out = {"message_id": message_id, "from": sender, "timestamp": "1760000000", "type": kind}
        if kind == "text":
            out["text"] = {"body": body}
        return {"json": {**out, **extra}}

    def form(submission_id, customer=a.code, lines=None):
        lines = [{"product": "GLV-NIT-M", "quantity": 40}] if lines is None else lines
        return {"json": {"submission_id": submission_id, "customer_code": customer, "lines": lines}}

    def eml(sender=a.email, message_id="", body="Please send 40 boxes of nitrile gloves M", html=None):
        return {"email": {"from": sender, "message_id": message_id, "body": body, "html": html}}

    def pending(thread, channel, customer, when):
        return {"thread_id": thread, "channel": channel, "customer": customer.code, "updated_at": when}

    def stored(channel, message_id, customer, thread):
        return {"channel": channel, "message_id": message_id, "customer": customer.code, "thread_id": thread}

    t1, t2 = "2026-10-01 10:00:00", "2026-10-02 10:00:00"
    wa_a = pending("whatsapp-run1-WA-A", "whatsapp", a, t1)
    email_a = pending("email-run1-MSG-A", "email", a, t2)

    # Web form: any JSON object with `submission_id`, whatever its content.
    add("web_form", "WF-001.json", form("WF-001"), router.WEB_FORM)
    add(
        "web_form",
        "WF-002.json",
        form("WF-002", lines=[{"product": "alcohol 70 250ml", "quantity": 12}]),
        router.WEB_FORM,
    )
    add("web_form", "WF-003.json", form("WF-003", customer="XXX-999"), router.WEB_FORM)
    add("web_form", "WF-004.json", form("WF-004", lines=[]), router.WEB_FORM)
    add("web_form", "WF-005.JSON", form("WF-005"), router.WEB_FORM)
    add("web_form", "WF-006.json", {"bom": True, **form("WF-006")}, router.WEB_FORM)
    add("web_form", "WF-007.json", {"json": {**form("WF-007")["json"], "message_id": "x", "from": pa}}, router.WEB_FORM)

    # Email: any `.eml` file; the email graph rejects what it cannot read.
    add("email", "MSG-001.eml", eml(message_id="<CR-MSG-001@example.com>"), router.EMAIL)
    add("email", "MSG-002.eml", eml(sender="someone@unknown.example"), router.EMAIL)
    add("email", "MSG-003.eml", eml(), router.EMAIL)
    add("email", "MSG-004.EML", eml(message_id="<CR-MSG-004@example.com>"), router.EMAIL)
    add("email", "MSG-005.eml", eml(message_id="<CR-MSG-005@example.com>"), router.EMAIL, pending=[wa_a])
    add("email", "MSG-006.eml", eml(body=None, html="<p>40 boxes of nitrile gloves M</p>"), router.EMAIL)
    add("email", "MSG-007.eml", {"text": "this is not a real email"}, router.EMAIL)

    # WhatsApp new order: no pending WhatsApp thread of the sender, or a message the WhatsApp graph rejects.
    add("whatsapp_new", "WA-001.json", wa("wamid.CR001"), router.WHATSAPP_NEW)
    add("whatsapp_new", "WA-002.json", wa("wamid.CR002", kind="image"), router.WHATSAPP_NEW, pending=[wa_a])
    add("whatsapp_new", "WA-003.json", wa("wamid.CR003", sender="34999999999"), router.WHATSAPP_NEW, pending=[wa_a])
    add("whatsapp_new", "WA-004.json", wa("wamid.CR004"), router.WHATSAPP_NEW, pending=[wa_a, email_a])
    add(
        "whatsapp_new",
        "WA-005.json",
        wa("wamid.CR005", sender=pb),
        router.WHATSAPP_NEW,
        pending=[wa_a],
    )
    add("whatsapp_new", "WA-006.json", wa("wamid.CR006", text="no body"), router.WHATSAPP_NEW, pending=[wa_a])
    add("whatsapp_new", "WA-007.json", {"bom": True, **wa("wamid.CR007")}, router.WHATSAPP_NEW)
    add(
        "whatsapp_new",
        "WA-008.json",
        wa("wamid.CR008"),
        router.WHATSAPP_NEW,
        stored=[stored("whatsapp", "wamid.OTHER", a, "whatsapp-run0-WA-OTHER")],
    )

    # WhatsApp answer: a text from a known customer whose most recent pending thread is a WhatsApp one.
    add(
        "whatsapp_answer",
        "WA-101.json",
        wa("wamid.CR101", body="remove the bed"),
        router.WHATSAPP_ANSWER,
        "whatsapp-run1-WA-A",
        pending=[wa_a],
    )
    add(
        "whatsapp_answer",
        "WA-102.json",
        wa("wamid.CR102", body="yes, the M size"),
        router.WHATSAPP_ANSWER,
        "whatsapp-run1-WA-B",
        pending=[wa_a, pending("whatsapp-run1-WA-B", "whatsapp", a, t2)],
    )
    add(
        "whatsapp_answer",
        "WA-103.json",
        wa("wamid.CR103", body="ok"),
        router.WHATSAPP_ANSWER,
        "whatsapp-run1-WA-C",
        pending=[pending("email-run1-MSG-B", "email", a, t1), pending("whatsapp-run1-WA-C", "whatsapp", a, t2)],
    )
    add(
        "whatsapp_answer",
        "WA-104.json",
        wa("wamid.CR104", sender=f"+{pa[:2]} {pa[2:5]} {pa[5:8]} {pa[8:]}", body="drop it"),
        router.WHATSAPP_ANSWER,
        "whatsapp-run1-WA-A",
        pending=[wa_a],
    )
    add(
        "whatsapp_answer",
        "WA-105.json",
        wa("wamid.CR105", body="the large ones"),
        router.WHATSAPP_ANSWER,
        "whatsapp-run1-WA-A",
        pending=[wa_a],
        applied_answers={"whatsapp-run1-WA-A": ["wamid.EARLIER"]},
        paused_sources={"whatsapp-run1-WA-A": "wamid.START"},
    )
    add(
        "whatsapp_answer",
        "WA-106.json",
        wa("wamid.CR106", sender=pb, body="yes"),
        router.WHATSAPP_ANSWER,
        "whatsapp-run1-WA-D",
        pending=[wa_a, pending("whatsapp-run1-WA-D", "whatsapp", b, t1)],
    )

    # Duplicate: a re-delivered message already stored, the start of a paused thread, or an applied answer.
    add(
        "duplicate",
        "MSG-201.eml",
        eml(message_id="<CR-MSG-201@example.com>"),
        router.DUPLICATE,
        "email-run0-MSG-201",
        "already stored as order",
        stored=[stored("email", "<CR-MSG-201@example.com>", a, "email-run0-MSG-201")],
    )
    add(
        "duplicate",
        "WA-201.json",
        wa("wamid.CR201"),
        router.DUPLICATE,
        "whatsapp-run0-WA-201",
        "already stored as order",
        stored=[stored("whatsapp", "wamid.CR201", a, "whatsapp-run0-WA-201")],
    )
    add(
        "duplicate",
        "WA-202.json",
        wa("wamid.CR202"),
        router.DUPLICATE,
        "whatsapp-run0-WA-202",
        "already stored as order",
        pending=[wa_a],
        stored=[stored("whatsapp", "wamid.CR202", a, "whatsapp-run0-WA-202")],
    )
    add(
        "duplicate",
        "MSG-202.eml",
        eml(message_id="<CR-MSG-202@example.com>"),
        router.DUPLICATE,
        "email-run1-MSG-A",
        pending=[email_a],
        paused_sources={"email-run1-MSG-A": "<CR-MSG-202@example.com>"},
    )
    add(
        "duplicate",
        "WA-203.json",
        wa("wamid.CR203"),
        router.DUPLICATE,
        "whatsapp-run1-WA-A",
        pending=[wa_a],
        paused_sources={"whatsapp-run1-WA-A": "wamid.CR203"},
    )
    add(
        "duplicate",
        "WA-204.json",
        wa("wamid.CR204", body="remove the bed"),
        router.DUPLICATE,
        "whatsapp-run1-WA-A",
        "already applied as an answer",
        pending=[wa_a],
        applied_answers={"whatsapp-run1-WA-A": ["wamid.CR204"]},
    )
    add(
        "duplicate",
        "WA-205.json",
        wa("wamid.CR205", sender=f"+{pa}"),
        router.DUPLICATE,
        "whatsapp-run1-WA-A",
        pending=[wa_a, email_a],
        paused_sources={"whatsapp-run1-WA-A": "wamid.CR205"},
    )

    # Rejected: no channel fits the file, or its message id is stored for another customer.
    add("rejected", "notes.txt", {"text": "40 boxes of gloves"}, router.REJECTED, reason="'.txt' fits no channel")
    add("rejected", "order.pdf", {"text": "%PDF-1.4"}, router.REJECTED, reason="'.pdf' fits no channel")
    add("rejected", "README", {"text": "40 boxes"}, router.REJECTED, reason="'README' fits no channel")
    add("rejected", "broken.json", {"text": "{not json"}, router.REJECTED, reason="is not valid JSON")
    add("rejected", "empty.json", {"text": ""}, router.REJECTED, reason="is not valid JSON")
    add(
        "rejected",
        "list.json",
        {"json": [1, 2]},
        router.REJECTED,
        reason="neither a web form submission nor a WhatsApp message",
    )
    add(
        "rejected",
        "other.json",
        {"json": {"hello": "world"}},
        router.REJECTED,
        reason="neither a web form submission nor a WhatsApp message",
    )
    add(
        "rejected",
        "string.json",
        {"json": "40 boxes of gloves"},
        router.REJECTED,
        reason="neither a web form submission nor a WhatsApp message",
    )
    add(
        "rejected",
        "no-sender.json",
        {"json": {"message_id": "wamid.X", "text": {"body": "hi"}}},
        router.REJECTED,
        reason="neither a web form submission nor a WhatsApp message",
    )
    add("rejected", "latin1.json", {"hex": "7b2261223a2022e9227d"}, router.REJECTED, reason="cannot be read")
    add(
        "rejected",
        "MSG-301.eml",
        eml(sender=b.email, message_id="<CR-MSG-301@example.com>"),
        router.REJECTED,
        reason="already stored for another customer",
        stored=[stored("email", "<CR-MSG-301@example.com>", a, "email-run0-MSG-301")],
    )
    add(
        "rejected",
        "WA-301.json",
        wa("wamid.CR301", sender=pb),
        router.REJECTED,
        reason="already stored for another customer",
        stored=[stored("whatsapp", "wamid.CR301", c, "whatsapp-run0-WA-301")],
    )
    return items


def load_dataset(path: Path | None = None) -> list[dict]:
    return read_jsonl(path or DATASET_PATH)


def _write_item(folder: Path, item: dict) -> Path:
    path = folder / item["file"]
    if "email" in item:
        e = item["email"]
        message = EmailMessage()
        message["From"] = e["from"]
        message["To"] = "orders@example.com"
        message["Subject"] = "Order"
        if e["message_id"]:
            message["Message-ID"] = e["message_id"]
        if e["body"] is not None:
            message.set_content(e["body"])
        if e["html"] is not None:
            message.set_content(e["html"], subtype="html")
        path.write_bytes(bytes(message))
    elif "hex" in item:
        path.write_bytes(bytes.fromhex(item["hex"]))
    elif "json" in item:
        path.write_text(("﻿" if item.get("bom") else "") + json.dumps(item["json"]), encoding="utf-8")
    else:
        path.write_text(item["text"], encoding="utf-8")
    return path


def _prepare(db_path: Path, setup: dict) -> None:
    conn = db.connect(db_path)
    try:
        db.seed(conn)
        for p in setup["pending"]:
            db.save_clarification(conn, p["thread_id"], p["channel"], p["customer"], "Which gloves?", 1)
            with conn:
                conn.execute(
                    "UPDATE clarifications SET updated_at = ? WHERE thread_id = ?", (p["updated_at"], p["thread_id"])
                )
        for s in setup["stored"]:
            db.insert_order(
                conn, s["customer"], s["channel"], "new", [("GLV-NIT-M", 1)], source=(s["message_id"], s["thread_id"])
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------------
# Run, grade and report


def grade(item: dict, routed: router.Route) -> dict:
    """Right when the route, the named thread and the reason (a substring, or none expected) are the expected ones."""
    expected = item["expected"]
    reason_ok = expected["reason"] in (routed.reason or "") if expected["reason"] else routed.reason is None
    correct = routed.kind == expected["route"] and routed.thread_id == expected["thread_id"] and reason_ok
    return {"route": routed.kind, "thread_id": routed.thread_id, "reason": routed.reason, "correct": correct}


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    (folder / "inbox").mkdir(parents=True)
    db_path = folder / "business.db"
    _prepare(db_path, item["setup"])
    path = _write_item(folder / "inbox", item)
    setup = item["setup"]
    conn = db.connect(db_path)
    try:
        routed = router.route(
            path,
            conn,
            paused_source=lambda channel, thread: setup["paused_sources"].get(thread),
            applied_answers=lambda channel, thread: setup["applied_answers"].get(thread, []),
        )
    finally:
        conn.close()
    return {"id": item["id"], "category": item["category"], "expected": item["expected"], **grade(item, routed)}


def _rate(rows: list[dict]) -> dict:
    hits, n = sum(r["correct"] for r in rows), len(rows)
    low, high = wilson_interval(hits, n)
    return {"value": hits / n if n else 0.0, "low": low, "high": high, "n": n, "hits": hits}


def evaluate(
    mode: str, split: str, *, dataset_path: Path = DATASET_PATH, set_baseline: bool = False, workers: int = 1
) -> int:
    """Route every item and print the report; exit 1 when an item misses its route. Deterministic: no split or baseline."""
    items = load_dataset(dataset_path)
    with tempfile.TemporaryDirectory() as tmp:
        results = [run_item(item, Path(tmp)) for item in items]
    s = _rate(results)
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    print(f"{SUITE}  mode={mode}  items={len(results)}  (deterministic: no model call, no split, no baseline)")
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    print(
        f"{'route_accuracy':<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNIT:<16}{target:<9}{met:<12}"
        f"{_pct(TARGET):<11}{gate}" + zero_event_note(s)
    )
    print("per route:")
    for route in ROUTES:
        rows = [r for r in results if r["expected"]["route"] == route]
        if rows:
            g = _rate(rows)
            print(f"  {route:<16} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        e = r["expected"]
        print(
            f"  {r['id']}  expected {e['route']} thread={e['thread_id']} reason~{e['reason']!r}  "
            f"got {r['route']} thread={r['thread_id']} reason={r['reason']!r}"
        )
    if failures:
        print(f"GATE FAILED: route_accuracy {_pct(s['value'])} below threshold {_pct(TARGET)}")
        return 1
    return 0


def main() -> None:
    items = build_items()
    write_jsonl(DATASET_PATH, items)
    print(f"Built {len(items)} routing items -> {DATASET_PATH}")


if __name__ == "__main__":
    main()
