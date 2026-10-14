"""C3, C7, C8, C9 and C10: the claim commands, the restart between processes and the list and show output."""

from purchase_cycle import claims, cli, db
from test_claims import ROWS, _invoice, _two_claims_from

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
