"""Phase 12 increment 10 (C7): `cycle-demo` runs the four-order scenario, each step in its own process, network blocked."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import types

import pytest

from conftest import SITECUSTOMIZE
from purchase_cycle import cli, cycle_cli, sales_cli
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
from test_cycle import ANSWERS as BASE
from test_cycle_cli import RecordingClient

SIZES = ", ".join(f"Nitrile examination gloves, powder-free, size {s}" for s in ("XS", "S", "M", "L", "XL"))
GLOVES_S = "Nitrile examination gloves, powder-free, size S"
GLOVES_XS = "Nitrile examination gloves, powder-free, size XS"


def _offer(quantity):
    return {
        "offers": True,
        "quantity": quantity,
        "unit_price": 4.1,
        "currency": "EUR",
        "vat_included": False,
        "vat_rate": None,
        "lead_time_days": 5,
        "working_days": True,
        "price_text": "4.10 EUR per box",
    }


def _document(kind, number, po, product, quantity, price):
    return {
        "kind": kind,
        "number": number,
        "po_reference": po,
        "issue_date": "2026-10-20",
        "currency": "EUR" if kind == "invoice" else None,
        "supplier_text": "Distribuciones Sanitarias del Norte S.A.",
        "lines": [
            {
                "product": product,
                "quantity": quantity,
                "unit_price": price,
                "line_total": round(price * quantity, 2) if price else None,
            }
        ],
    }


# Hand-written answers, keyed by a part of the prompt text the demo sends to each task.
ANSWERS = {
    MATCHING.name: {"nitrile gloves M": {"sku": "GLV-NIT-M"}, "nitrile gloves XS": {"sku": "GLV-NIT-XS"}},
    EMAIL_INTAKE.name: {"": {"is_order": True, "reason": "an order"}},
    EMAIL_EXTRACTION.name: {
        "10 boxes of nitrile gloves": {
            "lines": [{"source": "body", "source_text": "nitrile gloves", "sku": None, "quantity": 10}]
        }
    },
    WHATSAPP_INTAKE.name: BASE[WHATSAPP_INTAKE.name],
    WHATSAPP_EXTRACTION.name: BASE[WHATSAPP_EXTRACTION.name],
    CLARIFICATION_QUESTION.name: {
        "": {"question": f'For "nitrile gloves" (10 boxes), which one do you want: {SIZES}?'}
    },
    CLARIFICATION_ANSWER.name: {
        "": {"resolutions": [{"line_id": 1, "action": "set", "sku": "GLV-NIT-L", "quantity": 10}]}
    },
    OFFER_EXTRACTION.name: {"4 boxes of GLV-NIT-S": _offer(4), "3 boxes of GLV-NIT-XS": _offer(3)},
    SUPPLIER_DOCUMENT_EXTRACTION.name: {
        "INVOICE F-0101": _document("invoice", "F-0101", "PO-0001", GLOVES_S, 4, 4.6),
        "DELIVERY NOTE A-0101": _document("delivery_note", "A-0101", "PO-0001", GLOVES_S, 4, None),
        "INVOICE F-0102": _document("invoice", "F-0102", "PO-0002", GLOVES_XS, 3, 4.1),
        "DELIVERY NOTE A-0102": _document("delivery_note", "A-0102", "PO-0002", GLOVES_XS, 2, None),
    },
    CLAIM_DRAFTING.name: {"": {"body": "Dear supplier, please review the invoice."}},
    RESPONSE_DRAFTING.name: {"": {"body": "Dear customer, we are sorry for the damaged units."}},
}


def write_recordings(path, tmp_path, monkeypatch):
    """Run the demo in this process with the hand-written answers and keep each under the key a replay looks up."""
    calls, recorded = [], {}

    def factory(mode, catalog, recordings=None, task=None):
        return RecordingClient(task, calls, ANSWERS, catalog, recorded)

    def in_process(command, capture_output=True, text=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(command[3:])  # drop: python -m purchase_cycle.cli
            except SystemExit as stop:
                code = stop.code
        return types.SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())

    for module in (cli, sales_cli, cycle_cli):
        monkeypatch.setattr(module, "ModelClient", factory)
    monkeypatch.setattr(cycle_cli.subprocess, "run", in_process)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert cli.main(["cycle-demo", "--workdir", str(tmp_path / "recording"), "--recordings", str(empty)]) == 0
    path.write_text("".join(json.dumps(row) + "\n" for row in recorded.values()), encoding="utf-8")
    return len(calls)


@pytest.fixture(scope="module")
def cycle_demo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cycle")
    recordings = tmp / "recordings.jsonl"
    with pytest.MonkeyPatch.context() as patch:
        asked = write_recordings(recordings, tmp, patch)
    site = tmp / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    log = tmp / "processes.log"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY")}
    env.update(PYTHONPATH=str(site), ORDERS_DEMO_TEST_LOG=str(log))
    workdir = tmp / "work"
    command = [sys.executable, "-m", "purchase_cycle.cli", "cycle-demo", "--workdir", str(workdir)]
    command += ["--recordings", str(recordings)]
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    return run, log, workdir, asked


def test_the_run_asks_for_seventeen_model_calls(cycle_demo):
    assert cycle_demo[3] == 17


def test_the_demo_runs_with_the_network_blocked_each_step_in_its_own_process(cycle_demo):
    run, log, _, _ = cycle_demo
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) == 14
    assert all("exit code 0" in line for line in steps)
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    assert "mode=replay" in out


def test_the_printed_output_shows_stages_pending_decisions_and_operator_decisions(cycle_demo):
    out = cycle_demo[0].stdout
    assert "pending decisions:" in out
    assert out.count("operator decision:") == 6
    for text in ("== step 1:", "== step 14:", "summary:"):
        assert text in out
    summary = out.split("summary:")[1]
    for line in (
        "order 1 (A): served, invoice matched",
        "order 2 (C): served, invoice awaiting_delivery_note",
        "order 3 (D): received, not invoiced",
        "order 4 (B): served, invoice discrepancies, credit note sent",
        "supplier claims: 1 rejected, 1 sent",
        "quote requests waiting: QR-0003 GLV-NIT-XS x1",
    ):
        assert line in summary


def test_the_database_holds_the_four_final_states(cycle_demo):
    run, _, workdir, _ = cycle_demo
    assert run.returncode == 0, run.stdout + run.stderr
    conn = sqlite3.connect(workdir / "business.db")
    try:
        orders = {r[0]: r[1] for r in conn.execute("SELECT id, status FROM orders")}
        results = sorted(r[0] for r in conn.execute("SELECT result FROM sales_reconciliations"))
        corrections = [r[0] for r in conn.execute("SELECT kind FROM customer_corrections")]
        responses = [r[0] for r in conn.execute("SELECT status FROM customer_responses")]
        claims = sorted(r[0] for r in conn.execute("SELECT status FROM claims"))
        waiting = [r[0] for r in conn.execute("SELECT id FROM quote_requests WHERE status = 'waiting'")]
    finally:
        conn.close()
    assert orders == {1: "served", 2: "served", 3: "received", 4: "served"}  # A, C, D, B
    assert results == ["awaiting_delivery_note", "discrepancies", "matched"]
    assert corrections == ["credit_note"] and responses == ["sent"]
    assert claims == ["rejected", "sent"]
    assert waiting == [3]
