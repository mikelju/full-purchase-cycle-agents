"""C14: `reconcile-demo` runs the sample folder from the scan to the stored results, each step in its own process, with the network blocked."""

import json
import shutil
import sqlite3

import pytest

from conftest import run_reconcile_demo
from purchase_cycle import claims, config, db, llm


def test_reconcile_demo_ends_with_every_outcome_with_the_network_blocked(reconcile_demo):
    run, log, workdir = reconcile_demo
    out = run.stdout
    assert run.returncode == 0, out + run.stderr
    assert run.stderr == ""
    noted = log.read_text(encoding="utf-8").split()
    assert "network" not in noted
    steps = [line for line in out.splitlines() if line.startswith("== step ")]
    assert len(steps) >= 10
    assert noted.count("process") == 1 + len(steps)  # the demo and one process per step
    assert "mode=replay" in out
    assert "extracted documents:" in out and "differences:" in out

    conn = sqlite3.connect(workdir / "business.db")
    try:
        results = [r[0] for r in conn.execute("SELECT result FROM reconciliations ORDER BY invoice_id")]
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT type FROM discrepancies")}
        unlinked = sorted(r[0] for r in conn.execute("SELECT reason FROM supplier_documents WHERE status = 'unlinked'"))
        late = conn.execute(  # the invoice that waited for its note was completed by the second scan
            "SELECT r.result, d.source_file FROM reconciliations r JOIN supplier_documents d ON d.id = r.delivery_note_id "
            "JOIN supplier_documents i ON i.id = r.invoice_id WHERE i.number = 'LV-0306'"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0]
        orders = conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
    finally:
        conn.close()
    assert orders == 7
    assert count == 7 == len(results)  # one reconciliation per linked invoice, none added by the second scan
    assert "matched" in results
    assert results.count("awaiting_delivery_note") == 1  # C14: the demo ends with one invoice still waiting
    assert kinds == {"price", "quantity", "extra_line", "missing_line"}
    assert unlinked == ["supplier_mismatch", "unknown_order"]
    assert late is not None and late[0] == "matched" and late[1].endswith("dn-006-thermometers.pdf")
    assert "result=awaiting_delivery_note" in out  # it was shown awaiting before the second scan
    assert "still awaiting a delivery note at the end: 1" in out
    assert out.count("result=matched") >= 3
    assert "unlinked (unknown_order)" in out and "unlinked (supplier_mismatch)" in out
    for kind in ("price", "quantity", "extra_line", "missing_line"):
        assert f"  {kind}  sku=" in out
    assert "claims: INV-0007=sent, INV-0008=sent edited, INV-0009=rejected, INV-0010=awaiting_approval" in out


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_the_demo_replays_whatever_the_line_endings_of_the_sample_text_files(tmp_path, ending, claim_recordings):
    examples = tmp_path / "examples"
    shutil.copytree(config.ROOT / "examples" / "invoices", examples)
    for path in examples.rglob("*.txt"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", ending))
    run, _, _ = run_reconcile_demo(tmp_path, "--examples", str(examples), "--claim-recordings", str(claim_recordings))
    assert run.returncode == 0, run.stdout + run.stderr
    assert "still awaiting a delivery note at the end: 1" in run.stdout


def test_the_recordings_hold_one_row_per_sample_document():
    rows = [
        json.loads(line)
        for line in (config.EVALS_DIR / "recordings" / "reconcile_demo.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 15


def test_the_versioned_claim_recordings_hold_one_row_per_demo_claim_and_replay_the_demo(tmp_path):
    path = config.EVALS_DIR / "recordings" / "claim_demo.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    run, _, workdir = run_reconcile_demo(tmp_path)  # the default claim recordings are the versioned file
    assert run.returncode == 0, run.stdout + run.stderr
    assert "claims: INV-0007=sent, INV-0008=sent edited, INV-0009=rejected, INV-0010=awaiting_approval" in run.stdout
    conn = db.connect(workdir / "business.db")
    try:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT invoice_id FROM reconciliations WHERE result = 'discrepancies' ORDER BY invoice_id"
            )
        ]
        facts = [claims.claim_facts(conn, invoice_id) for invoice_id in ids]
    finally:
        conn.close()
    system = llm.build_system_prompt([], llm.CLAIM_DRAFTING)
    keys = sorted(llm.recording_key(system, claims.facts_prompt(fact), llm.CLAIM_DRAFTING) for fact in facts)
    assert len(rows) == len(facts) == 4
    assert sorted(row["key"] for row in rows) == keys
