"""Deterministic `failure_recovery` evaluation (phase 05, D7, C13, C15).

Each scripted scenario sends one inbox message (web form, email or WhatsApp) through the real router and
channel graphs with recovery on, in replay over hand-written recordings in a temporary file. A fault-injecting
double around the recorded client raises the scripted errors in order (transient API errors, an authentication
error, a schema-invalid answer) and then answers from the recordings; a crash at a fault injection point is
simulated in-process by raising where `faults.crash_at` would exit the process. Later phases re-deliver the
message, resume the thread from its checkpoint (`resume`) or resume a parked thread (`failures resume`), each
with freshly built graphs, as a new process would. A scenario is right when every phase ends as expected and
the database and outbox hold the expected orders, lines, sources, failures rows and files. The gate is 100%.

Build the versioned dataset with `uv run python -m purchase_cycle.evaluation.recovery_eval`.
"""

import json
import tempfile
from email.message import EmailMessage
from pathlib import Path

import anthropic
import httpx
from pydantic import ValidationError

from purchase_cycle import db, faults, llm, router
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.clarification import ClarificationClients
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.email_order import build_email_order_graph, parse_email
from purchase_cycle.email_order import model_text as email_text
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.evaluation.stats import target_cells, wilson_interval, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import InvalidModelOutput, ModelClient
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph, phone_digits
from purchase_cycle.whatsapp_order import model_text as whatsapp_text

SUITE = "failure_recovery"
DATASET_VERSION = "1.0"
DATASET_PATH = EVALS_DIR / "datasets" / "failure_recovery" / "dataset.jsonl"
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CATEGORIES = (
    "transient_then_success",
    "transient_beyond_budget",
    "not_retried",
    "invalid_then_valid",
    "two_invalid",
    "parked_then_resumed",
    "crash_and_resume",
    "redelivery",
)
# Phase outcomes.
STORED, NO_ORDER, PARKED, ERROR, CRASH, DUPLICATE = "stored", "no_order", "parked", "error", "crash", "duplicate"

CUSTOMER = CUSTOMERS[1]
WA_BODY = "hi! can u send 40 boxes of nitrile gloves M thx"
WA_LINES = [{"source": "message", "source_text": "40 boxes of nitrile gloves M", "sku": "GLV-NIT-M", "quantity": 40}]
EMAIL_BODY = "Hello,\nPlease send 40 boxes of nitrile gloves M.\nThanks"
EMAIL_LINES = [{"source": "body", "source_text": "40 boxes of nitrile gloves M", "sku": "GLV-NIT-M", "quantity": 40}]
FORM_PRODUCT = "alcohol 70 250ml"
INTAKE = {"is_order": True, "reason": "An order."}
INVALID_ANSWER = "not a tool call"  # fails every task schema
STEPS = {
    "web_form": {"match": llm.MATCHING},
    "email": {"intake": llm.EMAIL_INTAKE, "extract": llm.EMAIL_EXTRACTION},
    "whatsapp": {"intake": llm.WHATSAPP_INTAKE, "extract": llm.WHATSAPP_EXTRACTION},
}
_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(cls, code):
    return cls(f"status {code}", response=httpx.Response(code, request=_REQUEST), body=None)


ERRORS = {
    "connection": lambda: anthropic.APIConnectionError(request=_REQUEST),
    "timeout": lambda: anthropic.APITimeoutError(request=_REQUEST),
    "rate_limit": lambda: _status(anthropic.RateLimitError, 429),
    "server": lambda: _status(anthropic.InternalServerError, 500),
    "authentication": lambda: _status(anthropic.AuthenticationError, 401),
}


# ---------------------------------------------------------------------------------------------------------------------
# Dataset


def build_items() -> list[dict]:
    """The scripted scenarios, in a fixed order; every expectation follows from the phase 05 recovery rules."""
    items: list[dict] = []

    def add(category, channel, faults_by_step, phases, expected, crash_at=None):
        n = len(items) + 1
        items.append(
            {
                "id": f"FR-{n:03d}",
                "dataset_version": DATASET_VERSION,
                "category": category,
                "channel": channel,
                "faults": faults_by_step,
                "crash_at": crash_at,
                "phases": phases,
                "expected": {
                    "phases": expected["phases"],
                    "orders": expected.get("orders", 0),
                    "lines": expected.get("lines", 0),
                    "sources": expected.get("sources", 0),
                    "needs_review": expected.get("needs_review", 0),
                    "resolved": expected.get("resolved", 0),
                    "outbox_files": expected.get("outbox_files", 0),
                },
            }
        )

    def stored(channel, phases=(STORED,), **extra):
        # One order with one line and one source; WhatsApp writes one reply file.
        return {"phases": list(phases), "orders": 1, "lines": 1, "sources": 1,
                "outbox_files": 1 if channel == "whatsapp" else 0, **extra}  # fmt: skip

    def parked(channel):
        # Nothing stored, one needs_review row; WhatsApp writes one parked notice.
        return {"phases": [PARKED], "needs_review": 1, "outbox_files": 1 if channel == "whatsapp" else 0}

    # Transient errors within the 3-attempt budget, then success.
    add("transient_then_success", "whatsapp", {"intake": ["connection", "connection"]}, ["deliver"], stored("whatsapp"))
    add("transient_then_success", "whatsapp", {"extract": ["timeout", "rate_limit"]}, ["deliver"], stored("whatsapp"))
    add("transient_then_success", "email", {"intake": ["server"]}, ["deliver"], stored("email"))
    add("transient_then_success", "email", {"extract": ["rate_limit", "server"]}, ["deliver"], stored("email"))
    add("transient_then_success", "web_form", {"match": ["connection", "timeout"]}, ["deliver"], stored("web_form"))

    # Transient errors beyond the budget park the thread with nothing stored.
    add("transient_beyond_budget", "whatsapp", {"extract": ["connection"] * 3}, ["deliver"], parked("whatsapp"))
    add("transient_beyond_budget", "email", {"intake": ["timeout"] * 3}, ["deliver"], parked("email"))
    add("transient_beyond_budget", "web_form", {"match": ["server"] * 3}, ["deliver"], parked("web_form"))

    # An authentication error is not retried or parked: the item fails with nothing stored.
    add("not_retried", "whatsapp", {"intake": ["authentication"]}, ["deliver"], {"phases": [ERROR]})
    add("not_retried", "email", {"extract": ["authentication"]}, ["deliver"], {"phases": [ERROR]})

    # A schema-invalid answer is re-asked once with its error and the valid answer is stored.
    add("invalid_then_valid", "whatsapp", {"extract": ["invalid"]}, ["deliver"], stored("whatsapp"))
    add("invalid_then_valid", "email", {"intake": ["invalid"]}, ["deliver"], stored("email"))
    add("invalid_then_valid", "web_form", {"match": ["invalid"]}, ["deliver"], stored("web_form"))

    # Two invalid answers park the thread.
    add("two_invalid", "whatsapp", {"extract": ["invalid", "invalid"]}, ["deliver"], parked("whatsapp"))
    add("two_invalid", "email", {"extract": ["invalid", "invalid"]}, ["deliver"], parked("email"))
    add("two_invalid", "web_form", {"match": ["invalid", "invalid"]}, ["deliver"], parked("web_form"))

    # A parked thread is resumed with `failures resume` once the model answers again, and its row is resolved.
    add(
        "parked_then_resumed",
        "whatsapp",
        {"intake": ["invalid", "invalid"]},
        ["deliver", "failures_resume"],
        stored("whatsapp", [PARKED, STORED], resolved=1, outbox_files=2),  # parked notice and reply
    )
    add(
        "parked_then_resumed",
        "email",
        {"intake": ["connection"] * 3},
        ["deliver", "failures_resume"],
        stored("email", [PARKED, STORED], resolved=1),
    )
    add(
        "parked_then_resumed",
        "web_form",
        {"match": ["invalid", "invalid"]},
        ["deliver", "failures_resume"],
        stored("web_form", [PARKED, STORED], resolved=1),
    )

    # A crash at each injection point, then `resume`: exactly one order, its line, one source and the reply.
    for channel in ("web_form", "email", "whatsapp"):
        for point in (faults.AFTER_CHANNEL_STEPS, faults.AFTER_STORE_COMMIT, faults.IN_REPLY):
            add("crash_and_resume", channel, {}, ["deliver", "resume"], stored(channel, [CRASH, STORED]), point)

    # A re-delivered message never makes a second order.
    add("redelivery", "whatsapp", {}, ["deliver", "deliver"], stored("whatsapp", [STORED, DUPLICATE]))
    add("redelivery", "email", {}, ["deliver", "deliver"], stored("email", [STORED, DUPLICATE]))
    add("redelivery", "web_form", {}, ["deliver", "deliver"], stored("web_form", [STORED, STORED]))
    add(
        "redelivery",
        "whatsapp",
        {},
        ["deliver", "deliver", "resume"],
        stored("whatsapp", [CRASH, DUPLICATE, STORED]),
        faults.AFTER_STORE_COMMIT,
    )
    add(
        "redelivery",
        "email",
        {},
        ["deliver", "deliver", "resume"],
        stored("email", [CRASH, STORED, STORED]),
        faults.AFTER_CHANNEL_STEPS,
    )
    add(
        "redelivery",
        "whatsapp",
        {"extract": ["invalid", "invalid"]},
        ["deliver", "deliver"],
        stored("whatsapp", [PARKED, STORED], needs_review=1, outbox_files=2),  # parked notice and reply
    )
    return items


def load_dataset(path: Path | None = None) -> list[dict]:
    return read_jsonl(path or DATASET_PATH)


# ---------------------------------------------------------------------------------------------------------------------
# Fault injection


class SimulatedCrash(BaseException):
    """Raised where `faults.crash_at` would end the process; not an Exception, so nothing in the graph handles it."""


class Scripted:
    """Fault-injecting double around the recorded client: it raises the scripted faults in order, then answers."""

    def __init__(self, client: ModelClient, script: list[str]):
        self.client, self.script = client, script

    def __getattr__(self, name):
        return getattr(self.client, name)

    def extract(self, *args, **kwargs):
        if self.script:
            fault = self.script.pop(0)
            if fault == "invalid":
                raise _invalid(self.client.task)
            raise ERRORS[fault]()
        return self.client.extract(*args, **kwargs)


def _invalid(task) -> InvalidModelOutput:
    try:
        task.answer.model_validate(INVALID_ANSWER)
    except ValidationError as error:
        return InvalidModelOutput(error, "scripted fault")
    raise AssertionError(f"{INVALID_ANSWER!r} fits the {task.name} schema")


# ---------------------------------------------------------------------------------------------------------------------
# Run, grade and report


def _write_message(inbox: Path, channel: str, item_id: str) -> tuple[Path, dict]:
    """Write the scenario message and return its path and the model text of each step."""
    if channel == "whatsapp":
        path = inbox / f"{item_id}.json"
        message = {"message_id": f"wamid.{item_id}", "from": phone_digits(CUSTOMER.phone), "timestamp": "1760000000",
                   "type": "text", "text": {"body": WA_BODY}}  # fmt: skip
        path.write_text(json.dumps(message), encoding="utf-8")
        text = whatsapp_text({"body": WA_BODY})
        return path, {"intake": (text, INTAKE), "extract": (text, {"lines": WA_LINES})}
    if channel == "email":
        path = inbox / f"{item_id}.eml"
        message = EmailMessage()
        message["From"] = CUSTOMER.email
        message["To"] = "orders@example.com"
        message["Subject"] = "Order"
        message["Message-ID"] = f"<{item_id}@example.com>"
        message.set_content(EMAIL_BODY)
        path.write_bytes(bytes(message))
        text = email_text(parse_email(path.read_bytes()))
        return path, {"intake": (text, INTAKE), "extract": (text, {"lines": EMAIL_LINES})}
    path = inbox / f"{item_id}.json"
    form = {
        "submission_id": item_id,
        "customer_code": CUSTOMER.code,
        "lines": [{"product": FORM_PRODUCT, "quantity": 12}],
    }
    path.write_text(json.dumps(form), encoding="utf-8")
    return path, {"match": (FORM_PRODUCT, {"sku": "ALC70-250"})}


def _write_recordings(path: Path, catalog: list, channel: str, texts: dict) -> None:
    """The valid answer of each step, for the first ask and for the re-ask after the scripted invalid answer."""
    rows = []
    for step, task in STEPS[channel].items():
        text, answer = texts[step]
        prompt = llm.build_system_prompt(catalog, task)
        for sentence in (text, text + llm.CORRECTION.format(error=_invalid(task).correction)):
            key = llm.recording_key(prompt, sentence, task)
            rows.append({"key": key, "case_id": step, "sentence": sentence, "answer": answer})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _graphs(folder: Path, catalog: list, scripts: dict) -> dict:
    """The three channel graphs with the clarify step and recovery on, built afresh as a new process would."""
    rec = folder / "recordings.jsonl"
    db_path, outbox, checkpoints = folder / "business.db", folder / "outbox", folder / "checkpoints.sqlite"

    def client(channel, step):
        task = STEPS[channel][step]
        return Scripted(ModelClient("replay", catalog, rec, task=task), scripts.setdefault(step, []))

    def clarification():
        return ClarificationClients(
            ModelClient("replay", catalog, rec, task=llm.CLARIFICATION_QUESTION),
            ModelClient("replay", catalog, rec, task=llm.CLARIFICATION_ANSWER),
        )

    return {
        "web_form": build_web_form_graph(
            client("web_form", "match"), db_path, sqlite_checkpointer(checkpoints), clarification=clarification(),
            recovery=True,
        ),
        "email": build_email_order_graph(
            client("email", "intake"), client("email", "extract"), db_path, sqlite_checkpointer(checkpoints),
            clarification=clarification(), recovery=True,
        ),
        "whatsapp": build_whatsapp_order_graph(
            client("whatsapp", "intake"), client("whatsapp", "extract"), db_path, outbox,
            sqlite_checkpointer(checkpoints), clarification=clarification(),
        ),
    }  # fmt: skip


def _failure_status(db_path: Path, thread_id: str | None) -> str | None:
    conn = db.connect(db_path)
    try:
        row = db.get_failure(conn, thread_id) if thread_id else None
    finally:
        conn.close()
    return row["status"] if row else None


def _outcome(db_path: Path, thread_id: str | None, state: dict | None, error: BaseException | None) -> str:
    if isinstance(error, SimulatedCrash):
        return CRASH
    if error is not None:
        return PARKED if _failure_status(db_path, thread_id) == "needs_review" else ERROR
    return STORED if (state or {}).get("order_id") is not None else NO_ORDER


def _run_phase(phase: str, item: dict, folder: Path, catalog: list, scripts: dict, thread: str | None, run: int):
    """Run one phase and return its outcome and the thread it ran."""
    graphs = _graphs(folder, catalog, scripts)
    db_path = folder / "business.db"
    if phase == "deliver":
        result = router.run_inbox(folder / "inbox", graphs, db_path, f"run{run}", folder / "outbox")[0]
        if result["route"].kind == router.DUPLICATE:
            return DUPLICATE, thread
        return _outcome(db_path, result["thread_id"], result["state"], result["error"]), result["thread_id"]
    config = {"configurable": {"thread_id": thread}}
    state, error = None, None
    try:
        state = graphs[item["channel"]].invoke(None, config, durability="sync")
    except Exception as caught:  # a resumed thread that fails again stays parked
        error = caught
    if phase == "failures_resume" and error is None:
        conn = db.connect(db_path)
        try:
            db.resolve_failure(conn, thread)
        finally:
            conn.close()
    return _outcome(db_path, thread, state, error), thread


def _measure(folder: Path) -> dict:
    conn = db.connect(folder / "business.db")
    try:
        count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        measured = {
            "orders": count("SELECT COUNT(*) FROM orders"),
            "lines": count("SELECT COUNT(*) FROM order_lines"),
            "sources": count("SELECT COUNT(*) FROM order_sources"),
            "needs_review": count("SELECT COUNT(*) FROM failures WHERE status = 'needs_review'"),
            "resolved": count("SELECT COUNT(*) FROM failures WHERE status = 'resolved'"),
        }
    finally:
        conn.close()
    outbox = folder / "outbox"
    measured["outbox_files"] = len(list(outbox.iterdir())) if outbox.is_dir() else 0
    return measured


def grade(item: dict, measured: dict) -> dict:
    """Right when every phase outcome and every database and outbox count is the expected one."""
    return {**measured, "correct": measured == item["expected"]}


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    (folder / "inbox").mkdir(parents=True)
    conn = db.connect(folder / "business.db")
    try:
        db.seed(conn)
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()
    _, texts = _write_message(folder / "inbox", item["channel"], item["id"])
    _write_recordings(folder / "recordings.jsonl", catalog, item["channel"], texts)
    scripts = {step: list(script) for step, script in item["faults"].items()}
    real_crash_at, crash_point = faults.crash_at, item["crash_at"]

    def crash_at(point: str) -> None:
        if point == crash_point:
            raise SimulatedCrash(point)

    outcomes, thread = [], None
    for n, phase in enumerate(item["phases"]):
        faults.crash_at = crash_at if n == 0 and crash_point else real_crash_at
        try:
            outcome, ran = _run_phase(phase, item, folder, catalog, scripts, thread, n + 1)
        except SimulatedCrash:
            outcome, ran = CRASH, f"{item['channel']}-run{n + 1}-{item['id']}"
        finally:
            faults.crash_at = real_crash_at
        outcomes.append(outcome)
        thread = thread or ran
    measured = {"phases": outcomes, **_measure(folder)}
    return {"id": item["id"], "category": item["category"], "expected": item["expected"], **grade(item, measured)}


def _rate(rows: list[dict]) -> dict:
    hits, n = sum(r["correct"] for r in rows), len(rows)
    low, high = wilson_interval(hits, n)
    return {"value": hits / n if n else 0.0, "low": low, "high": high, "n": n, "hits": hits}


def evaluate(
    mode: str, split: str, *, dataset_path: Path = DATASET_PATH, set_baseline: bool = False, workers: int = 1
) -> int:
    """Run every scenario and print the report; exit 1 when one misses its outcome. No split or baseline."""
    items = load_dataset(dataset_path)
    retry = llm.MODEL_RETRY
    llm.MODEL_RETRY = retry._replace(initial_interval=0, max_interval=0, jitter=False)  # no backoff wait
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            results = [run_item(item, Path(tmp)) for item in items]
    finally:
        llm.MODEL_RETRY = retry
    s = _rate(results)
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: scripted faults, no model call, no split, no baseline)")  # fmt: skip
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    print(
        f"{'scenario_success':<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNIT:<16}{target:<9}{met:<12}"
        f"{_pct(TARGET):<11}{gate}" + zero_event_note(s)
    )
    print("per category:")
    for category in CATEGORIES:
        rows = [r for r in results if r["category"] == category]
        if rows:
            g = _rate(rows)
            print(f"  {category:<24} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    keys = ("phases", "orders", "lines", "sources", "needs_review", "resolved", "outbox_files")
    for r in failures:
        expected = {k: r["expected"][k] for k in keys}
        got = {k: r[k] for k in keys}
        print(f"  {r['id']}  expected {expected}  got {got}")
    if failures:
        print(f"GATE FAILED: scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}")
        return 1
    return 0


def main() -> None:
    items = build_items()
    write_jsonl(DATASET_PATH, items)
    print(f"Built {len(items)} failure recovery scenarios -> {DATASET_PATH}")


if __name__ == "__main__":
    main()
