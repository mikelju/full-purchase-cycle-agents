"""`order_scenarios` evaluation (phase 05, deviation 05.1, C21, C22).

Each scenario is one versioned JSON file: the initial database state, the inbox message, the scripted customer
answers (written from the customer's intent before seeing any question), the steps (deliver, answer, re-deliver,
resume after a crash), the allowed effects and the expected final state.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.scenarios_eval`.
"""

import json
import sys
import tempfile
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from langgraph.types import Command
from langsmith import tracing_context

from purchase_cycle import db, faults, llm, router
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.clarification import ClarificationClients
from purchase_cycle.config import EVALS_DIR, MODEL_ID
from purchase_cycle.email_order import build_email_order_graph, parse_email
from purchase_cycle.email_order import model_text as email_text
from purchase_cycle.evaluation import critical, recovery_eval
from purchase_cycle.evaluation.email_eval import _ci, _mcnemar, _pct
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import MissingRecording, ModelClient
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph, phone_digits
from purchase_cycle.whatsapp_order import model_text as whatsapp_text

SUITE = "order_scenarios"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "order_scenarios"
RECORDINGS_PATH = EVALS_DIR / "recordings" / "order_scenarios.jsonl"  # one file for every task: the key holds the task
BASELINE_PATH = EVALS_DIR / "baselines" / "order_scenarios.json"
TARGET = 0.90  # C21: scenario success target, fixed before measuring
ALPHA = 0.05
UNIT = "scenario"
TASKS = {
    "match": llm.MATCHING,
    "email_intake": llm.EMAIL_INTAKE,
    "email_extract": llm.EMAIL_EXTRACTION,
    "whatsapp_intake": llm.WHATSAPP_INTAKE,
    "whatsapp_extract": llm.WHATSAPP_EXTRACTION,
    "question": llm.CLARIFICATION_QUESTION,
    "answer": llm.CLARIFICATION_ANSWER,
}
CHANNELS = ("whatsapp", "email", "web_form")
TAGS = ("clarification", "two_answers", "unknown_product", "crash_resume", "redelivery")
# C21 minimums: scenarios in all, per channel and per tag.
MINIMUMS = {"scenarios": 30, "per_channel": 8, "clarification": 10, "two_answers": 5, "unknown_product": 3,
            "crash_resume": 3, "redelivery": 3}  # fmt: skip
INITIAL_STATE = {"seed": "db.seed: catalog, stock and customers; no orders, clarifications or failures"}
PRICES = {p.sku: p.price_eur for p in PRODUCTS}

# name: (unit word, text the customer writes, SKU the customer means; None when the catalog does not hold it)
PRODUCTS_WRITTEN = {
    "gloves_m": ("boxes", "nitrile gloves M", "GLV-NIT-M"),
    "saline": ("bottles", "saline 500 ml", "SAL-500"),
    "tape": ("boxes", "paper tape 2.5 cm", "TAPE-PAP-25"),
    "bandage": ("packs", "cohesive bandage 7.5 cm", "BND-COH-7"),
    "sharps": ("units", "sharps container 5 litres", "SHARPS-5"),
    "lumbar": ("units", "lumbar support belt", "SUP-LUMBAR"),
    "spot": ("boxes", "spot plasters", "PLST-SPOT"),
    "cushion": ("units", "wheelchair cushion", "WHEELCHAIR-CUSH"),
    # Ambiguous texts: the catalog holds several candidates; the SKU is the one the scripted answer names.
    "gloves": ("boxes", "nitrile gloves", "GLV-NIT-L"),
    "syringe": ("boxes", "syringe 10 ml", "SYR-LL-10"),
    "strips": ("boxes", "blood glucose test strips", "GLU-STRIP-100"),
    "cream": ("tubes", "hand cream", "CREAM-HAND-50"),
    "thermo": ("units", "digital thermometer", "THERM-DIG-FLEX"),
    "oxi": ("units", "pulse oximeter", "OXI-FING"),
    # Unknown products: out of the catalog.
    "oxygen": ("units", "oxygen concentrator", None),
}
ANSWERS = {
    "gloves": "Size L please, all of them.",
    "syringe": "The luer lock ones.",
    "strips": "The boxes of 100 strips.",
    "cream": "The 50 ml tubes.",
    "thermo": "The ones with the flexible tip.",
    "oxi": "The adult ones.",
    "oxygen": "Then leave the oxygen concentrator out, we will buy it elsewhere.",
}
VAGUE = "Let me check with the nurse in charge and I will get back to you."

# (channel, lines as (product, quantity), tags, steps, answers, crash point, expected lines as (product, quantity))
_SAME = None  # expected lines equal the requested ones
SCENARIOS = [
    ("whatsapp", [("gloves_m", 20), ("saline", 12)], [], ["deliver"], [], None, _SAME),
    ("whatsapp", [("tape", 5), ("bandage", 8), ("spot", 10)], [], ["deliver"], [], None, _SAME),
    ("whatsapp", [("gloves", 20), ("sharps", 6)], ["clarification"], ["deliver", "answer"], ["gloves"], None, _SAME),
    ("whatsapp", [("syringe", 10), ("lumbar", 2)], ["clarification"], ["deliver", "answer"], ["syringe"], None, _SAME),
    ("whatsapp", [("strips", 4), ("saline", 24)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "strips"], None, _SAME),
    ("whatsapp", [("cream", 6), ("gloves_m", 10)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "cream"], None, _SAME),
    ("whatsapp", [("oxygen", 1), ("gloves_m", 10)], ["clarification", "unknown_product"], ["deliver", "answer"],
     ["oxygen"], None, [("gloves_m", 10)]),
    ("whatsapp", [("cushion", 2), ("tape", 4)], ["crash_resume"], ["deliver", "resume"], [], "after_store_commit",
     _SAME),
    ("whatsapp", [("bandage", 6), ("spot", 5)], ["redelivery"], ["deliver", "redeliver"], [], None, _SAME),
    ("whatsapp", [("thermo", 3), ("sharps", 2)], ["clarification", "redelivery"], ["deliver", "redeliver", "answer"],
     ["thermo"], None, _SAME),
    ("email", [("gloves_m", 20), ("saline", 12)], [], ["deliver"], [], None, _SAME),
    ("email", [("tape", 5), ("bandage", 8), ("spot", 10)], [], ["deliver"], [], None, _SAME),
    ("email", [("gloves", 20), ("sharps", 6)], ["clarification"], ["deliver", "answer"], ["gloves"], None, _SAME),
    ("email", [("syringe", 10), ("lumbar", 2)], ["clarification"], ["deliver", "answer"], ["syringe"], None, _SAME),
    ("email", [("strips", 4), ("saline", 24)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "strips"], None, _SAME),
    ("email", [("oxi", 2), ("lumbar", 1)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "oxi"], None, _SAME),
    ("email", [("oxygen", 1), ("gloves_m", 10)], ["clarification", "unknown_product"], ["deliver", "answer"],
     ["oxygen"], None, [("gloves_m", 10)]),
    ("email", [("cushion", 2), ("tape", 4)], ["crash_resume"], ["deliver", "resume"], [], "after_channel_steps", _SAME),
    ("email", [("bandage", 6), ("spot", 5)], ["redelivery"], ["deliver", "redeliver"], [], None, _SAME),
    ("email", [("sharps", 10)], [], ["deliver"], [], None, _SAME),
    ("web_form", [("gloves_m", 20), ("saline", 12)], [], ["deliver"], [], None, _SAME),
    ("web_form", [("tape", 5), ("bandage", 8), ("spot", 10)], [], ["deliver"], [], None, _SAME),
    ("web_form", [("gloves", 20), ("sharps", 6)], ["clarification"], ["deliver", "answer"], ["gloves"], None, _SAME),
    ("web_form", [("syringe", 10), ("lumbar", 2)], ["clarification"], ["deliver", "answer"], ["syringe"], None, _SAME),
    ("web_form", [("strips", 4), ("saline", 24)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "strips"], None, _SAME),
    ("web_form", [("oxygen", 1), ("gloves_m", 10)], ["clarification", "unknown_product"], ["deliver", "answer"],
     ["oxygen"], None, [("gloves_m", 10)]),
    ("web_form", [("cushion", 2), ("tape", 4)], ["crash_resume"], ["deliver", "resume"], [], "in_reply", _SAME),
    ("web_form", [("bandage", 6), ("spot", 5)], ["redelivery"], ["deliver", "redeliver"], [], None, _SAME),
    ("web_form", [("thermo", 5)], ["clarification"], ["deliver", "answer"], ["thermo"], None, _SAME),
    ("web_form", [("gloves_m", 600), ("saline", 6)], ["clarification"], ["deliver", "answer"],
     ["Sorry, that was a typo: 60 boxes of the gloves, not 600."], None, [("gloves_m", 60), ("saline", 6)]),
]  # fmt: skip


def _written(product: str, quantity: int) -> str:
    unit, text, _ = PRODUCTS_WRITTEN[product]
    return f"{quantity} {unit} of {text}"


def _message(channel: str, lines: list, customer) -> dict:
    """The inbox message as the customer sends it; the runner writes it in the channel file format."""
    if channel == "web_form":
        return {"lines": [{"product": PRODUCTS_WRITTEN[p][1], "quantity": q} for p, q in lines]}
    written = [_written(p, q) for p, q in lines]
    if channel == "whatsapp":
        return {"body": "hi, pls send " + " and ".join(written) + " thx"}
    body = "Hello,\nPlease send:\n" + "".join(f"- {w}\n" for w in written) + f"Thanks,\n{customer.contact_name}"
    return {"subject": "Order", "body": body}


def _expected_lines(lines: list) -> list[dict]:
    rows = [(PRODUCTS_WRITTEN[p][2], q) for p, q in lines]
    return [{"sku": sku, "quantity": q, "price_eur": PRICES[sku]} for sku, q in rows if sku is not None]


def build_items() -> list[dict]:
    items = []
    for n, (channel, lines, tags, steps, answers, crash_at, expected) in enumerate(SCENARIOS, start=1):
        customer = CUSTOMERS[n % len(CUSTOMERS)]
        asks = "clarification" in tags
        effects = ["store_order", "reply"] + (["ask_question"] if asks else [])
        effects += ["skip_duplicate"] if "redelivery" in tags else []
        items.append(
            {
                "id": f"OS-{n:03d}",
                "dataset_version": DATASET_VERSION,
                "channel": channel,
                "customer_code": customer.code,
                "tags": tags,
                "initial_state": INITIAL_STATE,
                "message": _message(channel, lines, customer),
                "answers": [ANSWERS.get(a, a) for a in answers],
                "steps": steps,
                "crash_at": crash_at,
                "requested": [{"text": PRODUCTS_WRITTEN[p][1], "sku": PRODUCTS_WRITTEN[p][2]} for p, _ in lines],
                "allowed_effects": effects,
                "expected": {
                    "orders": 1,
                    "lines": _expected_lines(lines if expected is None else expected),
                    "clarification": "answered" if asks else None,
                    "needs_review": 0,
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
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("OS-*.json"))]


# ---------------------------------------------------------------------------------------------------------------------
# Run, grade and report


class Watched:
    """The recorded client, noting each missing recording: the clarify step swallows it as a step failure."""

    def __init__(self, client: ModelClient, missing: list):
        self.client, self.missing = client, missing

    def __getattr__(self, name):
        return getattr(self.client, name)

    def extract(self, *args, **kwargs):
        try:
            return self.client.extract(*args, **kwargs)
        except MissingRecording as error:
            self.missing.append(str(error))
            raise


def new_clients(mode: str, catalog: list, recordings_path: Path, missing: list) -> dict:
    """One client per task, shared by every scenario and step, all on the one recordings file."""
    return {
        name: Watched(ModelClient(mode, catalog, recordings_path, task=task), missing) for name, task in TASKS.items()
    }


def build_graphs(folder: Path, clients: dict) -> dict:
    """The three channel graphs with the clarify step and recovery on, built afresh as a new process would."""
    db_path, outbox, checkpoints = folder / "business.db", folder / "outbox", folder / "checkpoints.sqlite"

    def clarification():
        return ClarificationClients(clients["question"], clients["answer"])

    return {
        "web_form": build_web_form_graph(
            clients["match"], db_path, sqlite_checkpointer(checkpoints), clarification=clarification(), recovery=True
        ),
        "email": build_email_order_graph(
            clients["email_intake"], clients["email_extract"], db_path, sqlite_checkpointer(checkpoints),
            clarification=clarification(), recovery=True,
        ),
        "whatsapp": build_whatsapp_order_graph(
            clients["whatsapp_intake"], clients["whatsapp_extract"], db_path, outbox,
            sqlite_checkpointer(checkpoints), clarification=clarification(),
        ),
    }  # fmt: skip


def _customer(code: str):
    return next(c for c in CUSTOMERS if c.code == code)


def write_message(inbox: Path, item: dict) -> str:
    """Write the scenario message in its channel file format and return its text as the model reads it."""
    inbox.mkdir(parents=True, exist_ok=True)
    customer, message = _customer(item["customer_code"]), item["message"]
    if item["channel"] == "whatsapp":
        data = {"message_id": f"wamid.{item['id']}", "from": phone_digits(customer.phone), "timestamp": "1760000000",
                "type": "text", "text": {"body": message["body"]}}  # fmt: skip
        (inbox / f"{item['id']}.json").write_text(json.dumps(data), encoding="utf-8")
        return whatsapp_text({"body": message["body"]})
    if item["channel"] == "email":
        mail = EmailMessage()
        mail["From"] = customer.email
        mail["To"] = "orders@example.com"
        mail["Subject"] = message["subject"]
        mail["Message-ID"] = f"<{item['id']}@example.com>"
        mail.set_content(message["body"])
        path = inbox / f"{item['id']}.eml"
        path.write_bytes(bytes(mail))
        return email_text(parse_email(path.read_bytes()))
    form = {"submission_id": item["id"], "customer_code": customer.code, "lines": message["lines"]}
    (inbox / f"{item['id']}.json").write_text(json.dumps(form), encoding="utf-8")
    return "\n".join(line["product"] for line in message["lines"])


def _write_answer(inbox: Path, item: dict, n: int, text: str) -> None:
    """A WhatsApp answer is a new message from the scenario customer."""
    inbox.mkdir(parents=True, exist_ok=True)
    data = {"message_id": f"wamid.{item['id']}.a{n}", "from": phone_digits(_customer(item["customer_code"]).phone),
            "timestamp": str(1760000000 + n), "type": "text", "text": {"body": text}}  # fmt: skip
    (inbox / f"{item['id']}-a{n}.json").write_text(json.dumps(data), encoding="utf-8")


def _run_step(step: str, n: int, item: dict, folder: Path, clients: dict, state: dict) -> None:
    """Run one step with freshly built graphs; `state` keeps the scenario thread, the threads run and the replies."""
    graphs, channel = build_graphs(folder, clients), item["channel"]
    db_path, inbox = folder / "business.db", folder / f"inbox{n}"
    if step in ("deliver", "redeliver") or (step == "answer" and channel == "whatsapp"):
        if step == "answer":
            _write_answer(inbox, item, n, item["answers"][state["answered"]])
            state["answered"] += 1
        else:
            write_message(inbox, item)
        try:
            result = router.run_inbox(inbox, graphs, db_path, f"run{n}", folder / "outbox")[0]
        except recovery_eval.SimulatedCrash:
            ran = f"{channel}-run{n}-{item['id']}"
        else:
            recovery_eval._keep_reply(state["replies"], channel, result["state"])
            ran = result["thread_id"]
        state["thread"] = state["thread"] or ran
        state["threads"].append(ran)
        return
    if step == "answer":  # email and web form answers resume the paused thread, as `clarify answer` does
        received = Command(resume={"answer": item["answers"][state["answered"]]})
        state["answered"] += 1
    else:  # resume after the crash
        received = None
    result = None
    try:
        result = graphs[channel].invoke(received, {"configurable": {"thread_id": state["thread"]}}, durability="sync")
    except Exception:  # noqa: S110 - a failed step stays in the final state the grader reads
        pass
    recovery_eval._keep_reply(state["replies"], channel, result)
    state["threads"].append(state["thread"])


def _measure(folder: Path, replies: list) -> dict:
    """The final database and outbox state the grader compares with the expected one."""
    conn = db.connect(folder / "business.db")
    try:
        orders = [r["id"] for r in conn.execute("SELECT id FROM orders ORDER BY id")]
        lines = [dict(r) for r in conn.execute(
            "SELECT l.sku, l.quantity, p.price_eur FROM order_lines l JOIN products p USING (sku) ORDER BY l.sku"
        )]  # fmt: skip
        statuses = sorted({r["status"] for r in conn.execute("SELECT status FROM clarifications")})
        review = conn.execute("SELECT COUNT(*) FROM failures WHERE status = 'needs_review'").fetchone()[0]
    finally:
        conn.close()
    named = {int(m) for r in replies for m in critical.CONFIRMED.findall(r.get("text") or "")}
    return {
        "orders": len(orders),
        "lines": lines,
        "clarification": (statuses[0] if len(statuses) == 1 else statuses) if statuses else None,
        "needs_review": review,
        "confirmed": bool(orders) and set(orders) <= named,
    }


def grade(item: dict, measured: dict, errors: dict[str, int]) -> dict:
    """Right when the final state is the expected one, a reply confirms the stored order and no critical error."""
    expected = item["expected"]
    right = {
        "orders": measured["orders"] == expected["orders"],
        "lines": measured["lines"] == sorted(expected["lines"], key=lambda line: line["sku"]),
        "clarification": measured["clarification"] == expected["clarification"],
        "needs_review": measured["needs_review"] == expected["needs_review"],
        "confirmed": measured["confirmed"],
        "critical": not sum(errors.values()),
    }
    return {**measured, "checks": right, "correct": all(right.values())}


def run_item(item: dict, tmp: Path, clients: dict) -> dict:
    folder = tmp / item["id"]
    folder.mkdir(parents=True)
    conn = db.connect(folder / "business.db")
    try:
        db.seed(conn)
    finally:
        conn.close()
    message_text = write_message(folder / "message", item)
    real_crash_at, crash_point = faults.crash_at, item["crash_at"]

    def crash_at(point: str) -> None:
        if point == crash_point:
            raise recovery_eval.SimulatedCrash(point)

    state = {"thread": None, "threads": [], "replies": [], "answered": 0}
    for n, step in enumerate(item["steps"], start=1):
        faults.crash_at = crash_at if n == 1 and crash_point else real_crash_at
        try:
            _run_step(step, n, item, folder, clients, state)
        except recovery_eval.SimulatedCrash:  # a crash while resuming ends the step as a crash would
            state["threads"].append(state["thread"])
        finally:
            faults.crash_at = real_crash_at
    replies = state["replies"] + recovery_eval.outbox_replies(folder / "outbox")
    graph = build_graphs(folder, clients)[item["channel"]]
    errors = critical.count(
        folder / "business.db", recovery_eval.thread_values(graph, state["threads"]), replies, item["requested"],
        message_text,
    )  # fmt: skip
    graded = grade(item, _measure(folder, replies), errors)
    return {"id": item["id"], "channel": item["channel"], "tags": item["tags"], "expected": item["expected"],
            **graded, "critical": errors}  # fmt: skip


def load_baseline(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_baseline(path: Path, mode: str, summary: dict, results: list[dict]) -> None:
    payload = {
        "model": MODEL_ID,
        "dataset_version": DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "threshold": TARGET,
        "metrics": {"scenario_success": summary},
        "scenarios": {r["id"]: r["correct"] for r in results},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar over the scenarios, each one paired with its own baseline result."""
    scenarios = baseline["scenarios"]
    test = _mcnemar([(scenarios[r["id"]], r["correct"]) for r in results if r["id"] in scenarios])
    failures = []
    if test["lost"] > test["gained"] and test["p"] < ALPHA:
        failures.append(
            f"scenario_success dropped against the baseline (lost {test['lost']}, gained {test['gained']}, "
            f"McNemar p={test['p']:.4f})"
        )
    if set(scenarios) != {r["id"] for r in results}:
        failures.append("the evaluated scenarios differ from the baseline")
    return failures, test


def print_report(mode: str, results: list[dict], s: dict, errors: dict, baseline: dict | None, regression) -> None:
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  model={MODEL_ID}  (no split: every scenario runs)")
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    target, met = target_cells(s["value"], TARGET)
    gate = "PASS" if s["value"] >= TARGET else "FAIL"
    print(
        f"{'scenario_success':<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNIT:<16}{target:<9}{met:<12}"
        f"{_pct(TARGET):<11}{gate}" + zero_event_note(s)
    )
    recovery_eval.print_critical(errors, len(results))
    if baseline is None:
        print("regression vs baseline: no baseline stored")
    else:
        verdict = "FAIL" if regression[0] else "no significant drop"
        t = regression[1]
        print(
            f"regression vs baseline {baseline['measured_at']}: {verdict} "
            f"(scenario_success lost {t['lost']} gained {t['gained']} p={t['p']:.2f})"
        )
    print("per channel:")
    for channel in CHANNELS:
        rows = [r for r in results if r["channel"] == channel]
        if rows:
            g = recovery_eval._rate(rows)
            print(f"  {channel:<24} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    print("per tag:")
    for tag in TAGS:
        rows = [r for r in results if tag in r["tags"]]
        if rows:
            g = recovery_eval._rate(rows)
            print(f"  {tag:<24} {_pct(g['value'])} {_ci(g)} n={g['n']}")
    failures = [r for r in results if not r["correct"]]
    print(f"failures: {len(failures)}")
    for r in failures:
        wrong = [k for k, ok in r["checks"].items() if not ok]
        got = {k: r[k] for k in ("orders", "lines", "clarification", "needs_review", "confirmed")}
        print(f"  {r['id']}  wrong {wrong}  expected {r['expected']}  got {got}")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_dir: Path = DATASET_DIR,
    recordings_path: Path = RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 1,
) -> int:
    """Run every scenario in order (the crash patch is process wide, so no workers) and print the report.

    Exit 1 for a failed gate, 2 for a missing recording (even one the clarify step swallowed), 3 for a stopped
    baseline. The suite has no split: every scenario runs.
    """
    items = load_dataset(dataset_dir)
    conn = db.connect(":memory:")
    try:
        db.seed(conn)
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()
    missing: list[str] = []
    clients = new_clients(mode, catalog, recordings_path, missing)
    try:
        with tracing_context(enabled=False), tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            results = [run_item(item, Path(tmp), clients) for item in items]
    finally:
        for client in clients.values():
            client.save_recordings()
    if missing:
        print(f"Error: {len(missing)} missing recordings; the first: {missing[0]}", file=sys.stderr)
        return 2
    s = recovery_eval._rate(results)
    errors = recovery_eval.critical_totals(results)
    meets = s["value"] >= TARGET and not sum(errors.values())
    if set_baseline and meets:
        save_baseline(baseline_path, mode, s, results)
    baseline = load_baseline(baseline_path)
    regression = regression_gate(results, baseline) if baseline else None
    print_report(mode, results, s, errors, baseline, regression)
    usage = [u for client in clients.values() for u in client.usage]
    if usage:
        total = {k: sum(u[k] for u in usage) for k in usage[0]}
        print(
            f"tokens: calls={len(usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  "
            f"cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )
    if set_baseline and not meets:
        print("STOP: the scenarios did not reach the 90% target with no critical error; no baseline was stored.")
        return 3
    failures = [] if s["value"] >= TARGET else [f"scenario_success {_pct(s['value'])} below threshold {_pct(TARGET)}"]
    failures += regression[0] if regression else []
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return max(1 if failures else 0, recovery_eval.critical_gate(errors, results))


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} order scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
