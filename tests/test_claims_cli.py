"""C3, C7, C8, C9 and C10: the claim commands, the restart between processes and the list and show output."""

import subprocess
import sys

from purchase_cycle import claims, cli, db
from test_claims import ROWS, _invoice, _two_claims_from
from test_reconcile_demo import claim_recordings  # noqa: F401  (fixture)

SNAPSHOT = ("reconciliations", "discrepancies", "purchase_orders", "stock", "supplier_documents")


class World:
    """Two `discrepancies` invoices (INV-0001, INV-0002) and one `matched` (INV-0003) with recordings for the claims."""

    def __init__(self, tmp_path):
        self.db = tmp_path / "w.db"
        self.checkpoints = tmp_path / "cp.sqlite"
        self.outbox = tmp_path / "outbox"
        self.recordings = tmp_path / "two.jsonl"
        conn = db.connect(self.db)
        try:
            db.seed(conn)
            ids = [_invoice(conn, number, "discrepancies", ROWS) for number in ("F-1", "F-2")]
            _invoice(conn, "F-3", "matched")
            facts = [claims.claim_facts(conn, i) for i in ids]
            _two_claims_from(conn, tmp_path, facts, [claims.fill_template(f) for f in facts])
            self.recordings = tmp_path / "two.jsonl"
        finally:
            conn.close()

    def main(self, *words):
        return cli.main(["--db", str(self.db), "reconcile", *words])

    def claims(self, recordings=None):
        return self.main(
            "claims", "--checkpoints", str(self.checkpoints), "--recordings", str(recordings or self.recordings)
        )

    def approve(self, reference, *extra):
        return self.main(
            "approve", reference, "--checkpoints", str(self.checkpoints), "--outbox", str(self.outbox), *extra
        )

    def reject(self, reference):
        return self.main("reject", reference, "--checkpoints", str(self.checkpoints))

    def query(self, sql):
        conn = db.connect(self.db)
        try:
            return [tuple(r) for r in conn.execute(sql)]
        finally:
            conn.close()

    def snapshot(self):
        return {table: self.query(f"SELECT * FROM {table} ORDER BY 1") for table in SNAPSHOT}

    def emails(self):
        return sorted(p.name for p in self.outbox.glob("*.eml")) if self.outbox.exists() else []


def test_claims_drafts_one_claim_per_candidate_and_prints_draft_facts_and_source(tmp_path, capsys):
    world = World(tmp_path)
    before = world.snapshot()
    assert world.claims() == 0
    out = capsys.readouterr().out
    assert "claims drafted: 2" in out and "INV-0001" in out and "INV-0002" in out and "INV-0003" not in out
    assert "amount in dispute=11.00 EUR" in out and "source=model" in out and "Surgical masks" in out
    assert "Please send us a corrected invoice or a credit note." in out
    assert world.query("SELECT status, draft_source FROM claims ORDER BY id") == [("awaiting_approval", "model")] * 2
    assert world.snapshot() == before


def test_a_second_run_drafts_nothing_and_calls_no_model(tmp_path, capsys):
    world = World(tmp_path)
    assert world.claims() == 0
    capsys.readouterr()
    rows = world.query("SELECT * FROM claims")
    assert world.claims(recordings=tmp_path / "removed.jsonl") == 0
    assert "nothing to be drafted" in capsys.readouterr().out
    assert world.query("SELECT * FROM claims") == rows


def test_a_missing_recording_exits_1_and_stores_no_claim(tmp_path, capsys):
    world = World(tmp_path)
    assert world.claims(recordings=tmp_path / "removed.jsonl") == 1
    assert "Error" in capsys.readouterr().err
    assert world.query("SELECT * FROM claims") == []


def test_approve_sends_the_draft_and_edit_sends_the_file_text(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    before = world.snapshot()
    body = tmp_path / "edit.txt"
    body.write_text("Corrected invoice please.\n", encoding="utf-8")
    assert world.approve("INV-0001") == 0
    assert world.approve("INV-0002", "--body-file", str(body)) == 0
    out = capsys.readouterr().out
    assert "INV-0001 claim sent (as drafted)" in out and "INV-0002 claim sent (edited)" in out
    assert world.emails() == ["CLAIM-INV-0001-SUP-001.eml", "CLAIM-INV-0002-SUP-001.eml"]
    assert world.query("SELECT status, edited, final_body FROM claims ORDER BY id")[1] == (
        "sent",
        1,
        "Corrected invoice please.\n",
    )
    assert world.snapshot() == before


def test_reject_closes_the_claim_and_writes_nothing(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    capsys.readouterr()
    assert world.reject("INV-0001") == 0
    assert "INV-0001 claim rejected" in capsys.readouterr().out
    assert world.query("SELECT status FROM claims ORDER BY id") == [("rejected",), ("awaiting_approval",)]
    assert world.emails() == []


def test_an_empty_or_blank_body_file_is_refused_and_changes_nothing(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    capsys.readouterr()
    before = world.query("SELECT * FROM claims")
    for text in ("", "  \n\t\n"):
        path = tmp_path / "blank.txt"
        path.write_text(text, encoding="utf-8")
        assert world.approve("INV-0001", "--body-file", str(path)) == 1
        assert "empty" in capsys.readouterr().err
    assert world.approve("INV-0001", "--body-file", str(tmp_path / "missing.txt")) == 1
    assert "Error" in capsys.readouterr().err
    assert world.query("SELECT * FROM claims") == before and world.emails() == []


def test_refused_commands_exit_1_with_a_message_and_leave_at_most_one_email(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    assert world.approve("INV-0001") == 0
    assert world.reject("INV-0002") == 0
    capsys.readouterr()
    before = (world.query("SELECT * FROM claims"), world.emails())
    for run in (
        lambda: world.approve("INV-0001"),  # second approve
        lambda: world.reject("INV-0001"),  # reject of a sent claim
        lambda: world.approve("INV-0002"),  # approve of a rejected claim
        lambda: world.reject("INV-0002"),  # second reject
        lambda: world.approve("INV-0003"),  # invoice with no claim
        lambda: world.reject("INV-0003"),
        lambda: world.approve("INV-0099"),  # unknown invoice
        lambda: world.approve("nonsense"),
    ):
        assert run() == 1
        assert capsys.readouterr().err.startswith("Error:")
    assert (world.query("SELECT * FROM claims"), world.emails()) == before
    assert len(world.emails()) == 1


def _process(world, *words):
    run = subprocess.run(
        [sys.executable, "-m", "purchase_cycle.cli", "--db", str(world.db), "reconcile", *words],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def _final(world):
    root = str(world.outbox.parent)
    rows = [tuple(str(v).replace(root, "ROOT") for v in r) for r in world.query("SELECT * FROM claims ORDER BY id")]
    mails = {p.name: p.read_bytes() for p in world.outbox.glob("*.eml")}
    return rows, mails


def test_claims_and_each_decision_in_separate_processes_equal_one_process_with_no_model_call(tmp_path):
    single, split = World(tmp_path / "single"), World(tmp_path / "split")
    body = tmp_path / "edit.txt"
    body.write_text("Corrected invoice please.\n", encoding="utf-8")
    assert single.claims() == 0
    assert single.approve("INV-0001") == 0
    assert single.approve("INV-0002", "--body-file", str(body)) == 0
    cp = ["--checkpoints", str(split.checkpoints)]
    _process(split, "claims", *cp, "--recordings", str(split.recordings))
    split.recordings.unlink()  # from here any model call fails
    _process(split, "approve", "INV-0001", *cp, "--outbox", str(split.outbox))
    _process(split, "approve", "INV-0002", *cp, "--outbox", str(split.outbox), "--body-file", str(body))
    assert "nothing to be drafted" in _process(split, "claims", *cp, "--recordings", str(split.recordings))
    assert _final(split) == _final(single)
    assert [r[0] for r in split.query("SELECT status FROM claims ORDER BY id")] == ["sent", "sent"]


def test_list_and_show_carry_the_claim_status_text_and_source(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    body = tmp_path / "edit.txt"
    body.write_text("Corrected invoice please.\n", encoding="utf-8")
    assert world.approve("INV-0001", "--body-file", str(body)) == 0
    assert world.reject("INV-0002") == 0
    capsys.readouterr()
    assert world.main("list") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2].endswith("claim=sent") and lines[3].endswith("claim=rejected") and "claim=" not in lines[4]
    assert world.main("show", "INV-0001") == 0
    sent = capsys.readouterr().out
    assert "claim: status=sent  source=model  edited=True" in sent and "Corrected invoice please." in sent
    assert "draft body:" in sent and "final body:" in sent and "CLAIM-INV-0001-SUP-001.eml" in sent
    assert world.main("show", "INV-0002") == 0
    rejected = capsys.readouterr().out
    assert "claim: status=rejected" in rejected and "final body" not in rejected and "outbox file: -" in rejected


def test_an_invoice_with_no_claim_prints_the_phase_08_output(tmp_path, capsys):
    world = World(tmp_path)
    assert world.main("show", "INV-0003") == 0
    assert "claim" not in capsys.readouterr().out
    assert world.main("list") == 0
    assert "claim=" not in capsys.readouterr().out


def test_the_demo_ends_with_one_claim_sent_one_edited_one_rejected_and_one_waiting(tmp_path, claim_recordings):  # noqa: F811
    from test_reconcile_demo import _run_demo

    run, _, workdir = _run_demo(tmp_path, "--claim-recordings", str(claim_recordings))
    assert run.returncode == 0, run.stdout + run.stderr
    conn = db.connect(workdir / "business.db")
    try:
        rows = conn.execute(
            "SELECT d.number, c.status, c.edited, c.final_body, c.draft_body, c.outbox_file FROM claims c "
            "JOIN reconciliations r ON r.id = c.reconciliation_id JOIN supplier_documents d ON d.id = r.invoice_id "
            "ORDER BY d.number"
        ).fetchall()
        others = conn.execute("SELECT COUNT(*) FROM reconciliations WHERE result != 'discrepancies'").fetchone()[0]
    finally:
        conn.close()
    by_number = {r["number"]: r for r in rows}
    assert len(rows) == 4 and others == 3  # no claim for the matched, waiting and unlinked documents
    assert by_number["N-77002"]["status"] == "sent" and not by_number["N-77002"]["edited"]
    assert by_number["N-77002"]["final_body"] == by_number["N-77002"]["draft_body"]
    edited = by_number["LV-0303"]
    assert edited["status"] == "sent" and edited["edited"] and "invoice LV-0303" in edited["final_body"]
    assert by_number["F-2026-1004"]["status"] == "rejected" and by_number["F-2026-1004"]["outbox_file"] is None
    assert by_number["N-77005"]["status"] == "awaiting_approval"
    assert len(list((workdir / "supplier_outbox").glob("CLAIM-*.eml"))) == 2
    assert (
        run.stdout.index("== step 1:")
        < run.stdout.index("still awaiting a delivery note")
        < run.stdout.index("claim summary:")
    )


def test_the_demo_fails_clearly_when_the_claim_recordings_are_missing(tmp_path):
    from test_reconcile_demo import _run_demo

    run, _, _ = _run_demo(tmp_path, "--claim-recordings", str(tmp_path / "none.jsonl"))
    assert run.returncode == 1
    assert "claim recordings not found" in run.stderr and "still awaiting a delivery note at the end: 1" in run.stdout


def test_a_claim_is_decided_with_a_missing_thread_and_resolves_its_failure(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    conn = db.connect(world.db)
    try:
        db.park_failure(conn, "claim-INV-0001", "reconcile", "INV-0001", "claim_drafting", "template")
    finally:
        conn.close()
    world.checkpoints = tmp_path / "other.sqlite"  # `approve` reads another checkpoints file: no thread
    capsys.readouterr()
    assert world.approve("INV-0001") == 0
    assert "INV-0001 claim sent (as drafted)" in capsys.readouterr().out
    assert world.query("SELECT status FROM failures") == [("resolved",)]
    assert world.reject("INV-0002") == 0


def test_failures_resume_refuses_a_claim_and_points_to_reconcile(tmp_path, capsys):
    world = World(tmp_path)
    conn = db.connect(world.db)
    try:
        db.park_failure(conn, "claim-INV-0001", "reconcile", "INV-0001", "claim_drafting", "template")
    finally:
        conn.close()
    assert cli.main(["--db", str(world.db), "failures", "resume", "claim-INV-0001"]) == 1
    assert "reconcile approve|reject" in capsys.readouterr().err
    assert world.query("SELECT status FROM failures") == [("needs_review",)]


def test_a_different_file_in_the_outbox_exits_1_and_a_body_file_cannot_change_a_pending_send(tmp_path, capsys):
    world = World(tmp_path)
    world.claims()
    world.outbox.mkdir()
    (world.outbox / "CLAIM-INV-0001-SUP-001.eml").write_bytes(b"Subject: x\n\nsomething else\n")
    capsys.readouterr()
    assert world.approve("INV-0001") == 1
    assert "already exists" in capsys.readouterr().err
    assert world.query("SELECT status FROM claims ORDER BY id")[0] == ("awaiting_approval",)
    body = tmp_path / "edit.txt"
    body.write_text("Other words.\n", encoding="utf-8")
    assert world.approve("INV-0001", "--body-file", str(body)) == 1  # the thread is already at send
    assert "earlier decision" in capsys.readouterr().err
    assert world.query("SELECT status FROM claims ORDER BY id")[0] == ("awaiting_approval",)
