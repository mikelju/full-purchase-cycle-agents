"""Deterministic `customer_response` evaluation (phase 11, D6, C13, C1).

Each scenario is one versioned JSON file: the delivered orders, the signed note files, the hand-written draft the model
double returns for each invoice (a good one, an invalid answer or a draft that fails the fact check) and a script of
`responses`, `approve` and `reject` steps. The expected final state is written by hand from the rules: one response per
invoice with discrepancies, a decision only while it is awaiting, the correcting documents written only on approval,
one keyed email per approved invoice holding exactly the approved text, and the document amounts untouched by an edit.

The runner seeds a temporary database through the real sales path (served orders, then `sales scan` with the signed
note files), drafts with the double through `customer_responses.draft_responses` and decides with `sales approve` and
`sales reject` (a `new_process` step runs in a new process, a restart between draft and decision). No model is called:
the double counts its calls and the real model client is replaced by a counter. The gate is 100% scenario success and
0 critical errors.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.customer_response_eval`.
"""

import json
import re
import tempfile
from collections import Counter
from email import message_from_bytes, policy
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from purchase_cycle import customer_responses, db, llm
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_eval import _ci, _pct
from purchase_cycle.evaluation.recovery_eval import _rate
from purchase_cycle.evaluation.sales_reconciliation_eval import O1, O2, X, Y, Z, _command, _serve, form
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.graph import sqlite_checkpointer

SUITE = "customer_response"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "customer_response"
TAGS = (
    "discrepancies_response",
    "none_for_matched",
    "none_for_awaiting",
    "template_fallback",
    "approve",
    "approve_edit",
    "empty_edit_refused",
    "reject",
    "second_decision",
    "no_response_command",
    "second_responses",
    "restart",
    "both_documents",
)
MINIMUMS = {"scenarios": 24, **dict.fromkeys(TAGS, 2)}
TARGET = 1.0  # deterministic: every scenario must end as scripted
UNIT = "scenario"
CRITICAL_KINDS = (
    "unapproved_send",
    "duplicate_email",
    "sent_text_differs",
    "document_amount_changed",
    "status_outbox_mismatch",
    "model_call",
)
INITIAL_STATE = {"seed": "db.seed, the orders served and the signed notes scanned; no response drafted yet"}
INVALID, TEMPLATE = "<invalid model output>", "<fixed template>"
NO_NOTE = "no_signed_note"
EDIT = "Dear customer,\n\nWe have reviewed your signed delivery note and will send the documents shortly.\n\nBest regards,\nSales department"  # noqa: E501
ACTIVE_CALLS: list[int] = []  # real model client calls of the scenario being run (any call is a critical error)

# ---------------------------------------------------------------------------------------------------------------
# The scenarios, written by hand. Invoices are numbered in the order the orders are served; correcting documents are
# numbered CN-/CI- with the id they get in invoice order, the credit note before the corrective invoice.

CREDIT, CORRECTIVE = "credit_note", "corrective_invoice"
SENT, SENT_E = ("sent", False, "draft"), ("sent", True, "edit")
REJ, WAIT = ("rejected", False, None), ("awaiting_approval", False, None)


def inv(incidents, docs=(), draft="good", o=O1) -> dict:
    return {"order": o, "incidents": incidents, "docs": [list(d) for d in docs], "draft": draft}


def sc(invoices, steps, resp, codes, calls) -> dict:
    return {"invoices": invoices, "steps": steps, "resp": resp, "codes": codes, "calls": calls}


def step(op, n=None, body=None, new_process=False) -> dict:
    out = {"op": op, "invoice": None if n is None else f"SINV-{n:04d}", "new_process": new_process}
    return out | ({} if body is None else {"body": body})


RESP = step("responses")


def approve(n, body=None, np=False):
    return step("approve", n, body, np)


def reject(n, np=False):
    return step("reject", n, None, np)


A = inv([(X, "missing", 2)], [(CREDIT, -13.80)])
B = inv([(Y, "damaged", 2)], [(CREDIT, -25.00)])
C = inv([(X, "extra", 3)], [(CORRECTIVE, 20.70)])
D = inv([(Z, "extra", 2)], [(CORRECTIVE, 8.20)])
E = inv([(X, "missing", 1), (Y, "damaged", 2), (Z, "extra", 1)], [(CREDIT, -31.90), (CORRECTIVE, 4.10)])
F = inv([(Z, "missing", 3)], [(CREDIT, -12.30)], o=O2)
FULL, LATE = inv(None), inv(NO_NOTE)
A_INVALID = {**A, "draft": "invalid"}
B_BAD = {**B, "draft": "bad"}
E_BAD = {**E, "draft": "bad"}

CASES = [
    # a response for a discrepancies invoice, approved as drafted
    sc([A], [RESP, approve(1)], [SENT], [0, 0], 1),
    sc([B], [RESP, approve(1)], [SENT], [0, 0], 1),
    sc([C], [RESP, approve(1)], [SENT], [0, 0], 1),
    sc([D], [RESP, approve(1)], [SENT], [0, 0], 1),
    sc([E], [RESP, approve(1)], [SENT], [0, 0], 1),
    sc([F], [RESP, approve(1)], [SENT], [0, 0], 1),
    # approved with an edited body: the text is the edit, the documents keep their amounts
    sc([A], [RESP, approve(1, EDIT)], [SENT_E], [0, 0], 1),
    sc([E], [RESP, approve(1, EDIT)], [SENT_E], [0, 0], 1),
    sc([C], [RESP, approve(1, EDIT + "\nWe credit you 99.99 EUR.")], [SENT_E], [0, 0], 1),
    # an empty edit is refused and changes nothing
    sc([A], [RESP, approve(1, "   "), approve(1)], [SENT], [0, 1, 0], 1),
    sc([B], [RESP, approve(1, "")], [WAIT], [0, 1], 1),
    # rejected: no email and no document file
    sc([A], [RESP, reject(1)], [REJ], [0, 0], 1),
    sc([E], [RESP, reject(1)], [REJ], [0, 0], 1),
    # a second decision on a decided response is refused
    sc([A], [RESP, reject(1), approve(1)], [REJ], [0, 0, 1], 1),
    sc([A], [RESP, approve(1), reject(1)], [SENT], [0, 0, 1], 1),
    sc([A], [RESP, approve(1), approve(1)], [SENT], [0, 0, 1], 1),
    sc([A], [RESP, reject(1), reject(1)], [REJ], [0, 0, 1], 1),
    # no response for a matched invoice or one still awaiting its signed note
    sc([FULL], [RESP, approve(1)], [None], [0, 1], 0),
    sc([LATE], [RESP, reject(1)], [None], [0, 1], 0),
    sc([FULL, B], [RESP, approve(1), approve(2)], [None, SENT], [0, 1, 0], 1),
    sc([LATE, A], [RESP, approve(2), reject(1)], [None, SENT], [0, 0, 1], 1),
    # a command before any response exists
    sc([A], [approve(1), RESP, approve(1)], [SENT], [1, 0, 0], 1),
    sc([A], [reject(1)], [None], [1], 0),
    # the template replaces an invalid answer or a draft that fails the fact check
    sc([A_INVALID], [RESP, approve(1)], [SENT], [0, 0], 1),
    sc([B_BAD], [RESP, approve(1, EDIT)], [SENT_E], [0, 0], 1),
    sc([E_BAD], [RESP, reject(1)], [REJ], [0, 0], 1),
    sc([E_BAD], [RESP, approve(1)], [SENT], [0, 0], 1),
    # a second `sales responses` drafts nothing new
    sc([A], [RESP, RESP, approve(1)], [SENT], [0, 0, 0], 1),
    sc([A], [RESP, approve(1), RESP], [SENT], [0, 0, 0], 1),
    sc([A], [RESP, RESP, reject(1), RESP], [REJ], [0, 0, 0, 0], 1),
    # a restart between draft and decision: the decision runs in a new process
    sc([A], [RESP, approve(1, np=True)], [SENT], [0, 0], 1),
    sc([E], [RESP, approve(1, EDIT, np=True)], [SENT_E], [0, 0], 1),
    sc([B], [RESP, reject(1, np=True)], [REJ], [0, 0], 1),
    sc([A], [RESP, approve(1, np=True), approve(1, np=True)], [SENT], [0, 0, 1], 1),
    sc([A_INVALID], [RESP, approve(1, np=True)], [SENT], [0, 0], 1),
    # two invoices decided differently, one left undecided
    sc([A, E], [RESP, approve(1), reject(2)], [SENT, REJ], [0, 0, 0], 2),
    sc([C, B], [RESP, approve(1, EDIT), approve(2)], [SENT_E, SENT], [0, 0, 0], 2),
    sc([A, B, F], [RESP, approve(2)], [WAIT, SENT, WAIT], [0, 0], 3),
    sc([A, FULL, E], [RESP, approve(3, np=True), reject(1, np=True)], [REJ, None, SENT], [0, 0, 0], 2),
    sc([A], [RESP], [WAIT], [0], 1),
]  # fmt: skip


def _numbers(spec: dict) -> dict[str, list[list]]:
    """The document numbers of every invoice, in invoice order: `[number, kind, total]`."""
    out, count = {}, 0
    for n, invoice in enumerate(spec["invoices"], start=1):
        rows = []
        for kind, total in invoice["docs"]:
            count += 1
            rows.append([f"{'CN' if kind == CREDIT else 'CI'}-{count:04d}", kind, total])
        out[f"SINV-{n:04d}"] = rows
    return out


def draft_text(n: int, invoice: dict, docs: list[list], kind: str) -> str:
    """A response written from the facts by hand; `kind` `bad` leaves the incidents out so the fact check fails."""
    if kind == "invalid":
        return INVALID
    lines = [f"- {sku}: {type_}, {units} units" for sku, type_, units in invoice["incidents"]]
    if kind == "bad":
        lines = ["- nothing special"]
    paragraphs = [f"{number} is a {'credit' if k == CREDIT else 'corrective'} note with a total of {abs(t):.2f} EUR."
                  for number, k, t in docs]  # fmt: skip
    return (
        f"Dear customer,\n\nThank you for signing delivery note CDN-{n:04d} for invoice SINV-{n:04d}.\n"
        "You reported these incidents:\n" + "\n".join(lines) + "\n\n" + "\n".join(paragraphs)
        + "\n\nBest regards,\nSales department"
    )  # fmt: skip


def edit_text(spec: dict, ref: str) -> str:
    """The body of the approve step with a non-blank edit for the invoice, as the operator wrote it."""
    return next(
        s["body"] for s in spec["steps"] if s["op"] == "approve" and s["invoice"] == ref and s.get("body", " ").strip()
    )


def _result(invoice: dict) -> str:
    if invoice["incidents"] == NO_NOTE:
        return "awaiting_delivery_note"
    return "matched" if invoice["incidents"] is None else "discrepancies"


def _output(op: dict, code: int) -> str:
    if code:
        return "error"
    if op["op"] == "reject":
        return "rejected"
    if op["op"] == "approve":
        return "sent (edited)" if op.get("body") is not None else "sent (as drafted)"
    return ""


def build_items() -> list[dict]:
    items = []
    for n, spec in enumerate(CASES, start=1):
        numbers = _numbers(spec)
        refs = [f"SINV-{k:04d}" for k in range(1, len(spec["invoices"]) + 1)]
        forms = {
            f"n{k}": form(k, i["incidents"], i["order"]["customer"])
            for k, i in enumerate(spec["invoices"], start=1)
            if i["incidents"] != NO_NOTE
        }
        drafts = {
            ref: draft_text(k, i, numbers[ref], i["draft"])
            for k, (ref, i) in enumerate(zip(refs, spec["invoices"], strict=True), start=1)
            if i["docs"]
        }
        responses = {}
        for ref, i, r in zip(refs, spec["invoices"], spec["resp"], strict=True):
            if r is None:
                responses[ref] = None
                continue
            status, edited, sent = r
            body = {"draft": TEMPLATE if i["draft"] != "good" else drafts[ref], "edit": None, None: None}[sent]
            responses[ref] = {"status": status, "source": "model" if i["draft"] == "good" else "template",
                              "edited": edited, "sent": edit_text(spec, ref) if sent == "edit" else body}  # fmt: skip
        expected = {
            "results": {ref: _result(i) for ref, i in zip(refs, spec["invoices"], strict=True)},
            "docs": numbers,
            "responses": responses,
            "codes": spec["codes"],
            "outputs": [_output(s, c) for s, c in zip(spec["steps"], spec["codes"], strict=True)],
            "calls": spec["calls"],
            "open_failures": sum(
                1
                for ref, i in zip(refs, spec["invoices"], strict=True)
                if i["draft"] != "good"
                and responses[ref] is not None
                and responses[ref]["status"] == "awaiting_approval"
            ),  # fmt: skip
        }
        item = {"id": f"CR-{n:03d}", "dataset_version": DATASET_VERSION, "initial_state": INITIAL_STATE,
                "orders": [i["order"] for i in spec["invoices"]], "forms": forms, "drafts": drafts,
                "steps": spec["steps"], "expected": expected}  # fmt: skip
        item["tags"] = derive_tags(item, spec)
        items.append(item)
    return items


def derive_tags(item: dict, spec: dict) -> list[str]:
    """The coverage tags follow from the files and the expected results: the files hold what the scenario really is."""
    tags = set()
    expected = item["expected"]
    if any(rows for rows in expected["docs"].values()):
        tags.add("discrepancies_response")
    results = set(expected["results"].values())
    tags |= {"none_for_matched"} if "matched" in results else set()
    tags |= {"none_for_awaiting"} if "awaiting_delivery_note" in results else set()
    if any(i["draft"] != "good" for i in spec["invoices"] if i["docs"]):
        tags.add("template_fallback")
    if any(len(rows) == 2 for rows in expected["docs"].values()):
        tags.add("both_documents")
    decided, drafted = set(), False
    for s, code in zip(item["steps"], expected["codes"], strict=True):
        blank = s.get("body") is not None and not s["body"].strip()
        if s["op"] == "responses":
            if drafted:
                tags.add("second_responses")
            drafted = True
            continue
        if s["new_process"]:
            tags.add("restart")
        if blank:
            tags.add("empty_edit_refused")
        elif s["op"] == "approve" and not code:
            tags.add("approve_edit" if s.get("body") is not None else "approve")
        elif s["op"] == "reject" and not code:
            tags.add("reject")
        if s["invoice"] in decided:
            tags.add("second_decision")
        if not drafted or (code and expected["responses"][s["invoice"]] is None):
            tags.add("no_response_command")
        if not code:
            decided.add(s["invoice"])
    return [t for t in TAGS if t in tags]


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("CR-*.json"))]


# ---------------------------------------------------------------------------------------------------------------
# The runner: product path on a temporary database.


class DraftDouble:
    """The model client of the scenario: returns the stored draft of the invoice and counts its calls."""

    def __init__(self, drafts: dict[str, str]):
        self.drafts, self.calls = drafts, 0

    def extract(self, prompt: str, case_id: str | None = None, correction: str | None = None):
        self.calls += 1
        draft = self.drafts[case_id.removeprefix("response-")]
        if draft == INVALID:
            try:
                llm.ResponseBody.model_validate({"body": 1})
            except ValidationError as error:
                raise llm.InvalidModelOutput(error, case_id) from error
        return SimpleNamespace(body=draft)


def _setup(item: dict, paths: dict, folder: Path) -> None:
    conn = db.connect(paths["db"])
    try:
        db.seed(conn)
        for o in item["orders"]:
            _serve(conn, folder, o)
    finally:
        conn.close()
    for name, file in item["forms"].items():
        (paths["inbox"] / f"{name}.txt").write_text(file["text"], encoding="utf-8", newline="\n")
    code, _, err = _command(
        paths, "scan", str(paths["inbox"]), "--invoices-dir", str(paths["invoices"]),
        "--checkpoints", str(paths["sales_checkpoints"]),
    )  # fmt: skip
    assert code == 0, err


def _run_step(item: dict, op: dict, paths: dict, double: DraftDouble, folder: Path) -> tuple[int, str, str]:
    """The exit code, the printed output and the error output of one step."""
    if op["op"] == "responses":
        checkpointer = sqlite_checkpointer(paths["checkpoints"])
        try:
            customer_responses.draft_responses(paths["db"], checkpointer, double, paths["invoices"], paths["outbox"])
        finally:
            checkpointer.conn.close()
        return 0, "", ""
    words = [op["op"], op["invoice"], "--checkpoints", str(paths["checkpoints"])]
    if op["op"] == "approve":
        words += ["--invoices-dir", str(paths["invoices"]), "--outbox", str(paths["outbox"])]
        if "body" in op:
            body_file = folder / "body.txt"
            body_file.write_text(op["body"], encoding="utf-8", newline="\n")
            words += ["--body-file", str(body_file)]
    return _command(paths, *words, new_process=op["new_process"])


def _email_ref(name: str) -> str | None:
    found = re.match(r"RESPONSE-(SINV-\d{4})\.eml$", name)
    return found.group(1) if found else None


def _measure(paths: dict) -> dict:
    """The responses, the documents, the results, the open failures, the emails and the document files."""
    conn = db.connect(paths["db"])
    try:
        responses = {}
        for row in conn.execute(
            "SELECT p.*, i.number FROM customer_responses p JOIN sales_reconciliations r ON r.id = p.reconciliation_id "
            "JOIN customer_invoices i ON i.id = r.invoice_id ORDER BY i.id"
        ):
            facts = customer_responses.response_facts(conn, conn.execute(
                "SELECT invoice_id FROM sales_reconciliations WHERE id = ?", (row["reconciliation_id"],)
            ).fetchone()[0])  # fmt: skip
            responses[row["number"]] = {**dict(row), "template_text": customer_responses.fill_template(facts).strip()}
        docs = {}
        for row in conn.execute(
            "SELECT c.*, i.number AS invoice FROM customer_corrections c JOIN customer_invoices i ON i.id = c.invoice_id "
            "ORDER BY c.id"
        ):
            docs.setdefault(row["invoice"], []).append([row["number"], row["kind"], row["total"]])
        results = {
            r["number"]: r["result"]
            for r in conn.execute(
                "SELECT i.number, r.result FROM sales_reconciliations r JOIN customer_invoices i ON i.id = r.invoice_id"
            )
        }
        open_failures = conn.execute("SELECT COUNT(*) FROM failures WHERE status = 'needs_review'").fetchone()[0]
    finally:
        conn.close()
    emails = {}
    for path in sorted(paths["outbox"].glob("*.eml")) if paths["outbox"].is_dir() else []:
        message = message_from_bytes(path.read_bytes(), policy=policy.default)
        emails[path.name] = {"to": message["To"], "subject": message["Subject"], "body": message.get_content().strip()}
    files = {}
    for path in sorted(paths["invoices"].glob("C?-*.txt")) if paths["invoices"].is_dir() else []:
        total = re.search(r"^Total: (-?[\d.]+) EUR", path.read_text(encoding="utf-8"), re.M)
        files[path.stem] = None if total is None else float(total.group(1))
    return {"responses": responses, "docs": docs, "results": results, "open_failures": open_failures,
            "emails": emails, "files": files}  # fmt: skip


def _norm_docs(docs: dict, refs) -> dict:
    return {ref: [[n, k, round(t, 2)] for n, k, t in docs.get(ref, [])] for ref in refs}


def _got(item: dict, final: dict) -> dict:
    """The final state in the shape of the expectation."""
    refs = list(item["expected"]["results"])
    sent = {}
    responses = {}
    for ref in refs:
        row = final["responses"].get(ref)
        email = next((e for name, e in final["emails"].items() if _email_ref(name) == ref), None)
        text = None if email is None else email["body"]
        if text is not None and row is not None and row["draft_source"] == "template" and text == row["template_text"]:
            text = TEMPLATE
        sent[ref] = text
        responses[ref] = (
            None
            if row is None
            else {
                "status": row["status"],
                "source": row["draft_source"],
                "edited": bool(row["edited"]),
                "sent": text,
            }  # fmt: skip
        )
    return {
        "results": final["results"],
        "docs": _norm_docs(final["docs"], refs),
        "responses": responses,
        "codes": final["codes"],
        "outputs": final["outputs"],
        "calls": final["calls"],
        "open_failures": final["open_failures"],
        "files": sorted(final["files"]),
        "emails": sorted(final["emails"]),
    }


def _expected_view(item: dict) -> dict:
    expected = dict(item["expected"])
    expected["docs"] = _norm_docs(expected["docs"], expected["results"])
    sent = [ref for ref, r in expected["responses"].items() if r is not None and r["status"] == "sent"]
    expected["files"] = sorted(number for ref in sent for number, _, _ in expected["docs"][ref])
    expected["emails"] = sorted(f"RESPONSE-{ref}.eml" for ref in sent)
    return expected


def _errors(item: dict, final: dict) -> dict[str, int]:
    """The critical errors of a run, counted from the final state."""
    errors = dict.fromkeys(CRITICAL_KINDS, 0)
    expected = item["expected"]
    for ref, doc_rows in expected["docs"].items():
        row = final["responses"].get(ref)
        status = None if row is None else row["status"]
        mail = [name for name, e in final["emails"].items() if _email_ref(name) == ref or ref in (e["subject"] or "")]
        files = [number for number, _, _ in final["docs"].get(ref, []) if number in final["files"]]
        if status != "sent" and (mail or files):
            errors["unapproved_send"] += 1
        errors["duplicate_email"] += max(0, len(mail) - 1)
        if status == "sent" and (not mail or row["outbox_file"] is None or row["final_body"] is None):
            errors["status_outbox_mismatch"] += 1
        elif status == "sent" and final["emails"][mail[0]]["body"] != row["final_body"].strip():
            errors["status_outbox_mismatch"] += 1
        want = expected["responses"][ref]
        if mail and want is not None and want["sent"] is not None:
            approved = row["template_text"] if want["sent"] == TEMPLATE and row else want["sent"]
            if any(final["emails"][name]["body"] != approved for name in mail):
                errors["sent_text_differs"] += 1
        stored = {n: round(t, 2) for n, _, t in final["docs"].get(ref, [])}
        for number, _, total in doc_rows:
            if stored.get(number) != round(total, 2):
                errors["document_amount_changed"] += 1
            elif final["files"].get(number) is not None and abs(final["files"][number] - total) > 0.001:
                errors["document_amount_changed"] += 1
    errors["model_call"] = max(0, final["calls"] - expected["calls"]) + final["real_calls"]
    return errors


def run_item(item: dict, tmp: Path) -> dict:
    folder = tmp / item["id"]
    paths = {"db": folder / "case.db", "invoices": folder / "invoices", "inbox": folder / "inbox",
             "outbox": folder / "outbox", "checkpoints": folder / "responses.sqlite",
             "sales_checkpoints": folder / "sales.sqlite"}  # fmt: skip
    paths["inbox"].mkdir(parents=True)
    ACTIVE_CALLS.clear()
    _setup(item, paths, folder)
    double = DraftDouble(item["drafts"])
    codes, outputs = [], []
    for op in item["steps"]:
        code, out, err = _run_step(item, op, paths, double, folder)
        codes.append(code)
        if code:
            outputs.append("error" if err.startswith("Error:") else "unexpected")
        else:
            found = re.search(r"response (sent \((?:edited|as drafted)\)|rejected)", out)
            outputs.append(found.group(1) if found else "")
    final = _measure(paths)
    final |= {"codes": codes, "outputs": outputs, "calls": double.calls, "real_calls": len(ACTIVE_CALLS)}
    expected = _expected_view(item)
    got = _got(item, final)
    return {"id": item["id"], "tags": item["tags"], "expected": expected, "got": got,
            "correct": got == expected, "critical": _errors(item, final)}  # fmt: skip


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


def _count_calls(target=llm.ModelClient):
    """Replace the model client entry points by counters for the whole run; returns the restore function."""
    saved = (target.__init__, target._call_model)

    def counted(*args, **kwargs):
        ACTIVE_CALLS.append(1)
        raise llm.MissingRecording("customer_response makes no real model call")

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
    print(f"{SUITE}  mode={mode}  scenarios={len(results)}  (deterministic: scripted drafts and decisions, no model call, no split, no baseline)")  # fmt: skip
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
    counts = Counter(tag for item in items for tag in item["tags"])
    print(f"Built {len(items)} customer response scenarios -> {DATASET_DIR}  tags: {dict(counts)}")


if __name__ == "__main__":
    main()
