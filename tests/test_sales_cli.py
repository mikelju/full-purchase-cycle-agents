"""Phase 11 C3, C4, C8, C9 and C12: the sales commands on a temporary database with recordings."""

from purchase_cycle import cli, db
from purchase_cycle import customer_responses as cr
from test_customer_responses import _client
from test_sales import A, B, C, _form, _inbox, _incidents, _served

SNAPSHOT = ("customer_invoices", "signed_delivery_notes", "sales_reconciliations", "customer_corrections")


class World:
    """Three delivered orders: SINV-0001 and SINV-0002 report incidents, SINV-0003 is received in full."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.db = tmp_path / "w.db"
        self.checkpoints = tmp_path / "cp.sqlite"
        self.invoices = tmp_path / "docs"
        self.outbox = tmp_path / "outbox"
        conn = db.connect(self.db)
        try:
            db.seed(conn)
            self.notes = [_served(tmp_path, conn, [(A, 5), (B, 4)]) for _ in range(3)]
        finally:
            conn.close()
        self.inbox = _inbox(
            tmp_path,
            n1=_form(self.notes[0], _incidents((A, "missing", 2), (B, "damaged", 1))),
            n2=_form(self.notes[1], _incidents((A, "damaged", 2), (C, "extra", 1))),
            n3=_form(self.notes[2]),
        )
        self.recordings = tmp_path / "response_recordings.jsonl"

    def main(self, *words):
        return cli.main(["--db", str(self.db), "sales", *words])

    def invoice(self):
        return self.main("invoice", "--invoices-dir", str(self.invoices))

    def scan(self, folder=None):
        return self.main(
            "scan",
            str(folder or self.inbox),
            "--invoices-dir",
            str(self.invoices),
            "--checkpoints",
            str(self.checkpoints),
        )

    def record(self):
        """Write the response recordings (the template as the model answer) for the stored facts."""
        conn = db.connect(self.db)
        try:
            facts = [cr.response_facts(conn, i) for i in (1, 2)]
        finally:
            conn.close()
        client = _client(self.tmp, facts, [cr.fill_template(f) for f in facts])
        assert client.mode == "replay"

    def responses(self):
        return self.main("responses", "--checkpoints", str(self.checkpoints), "--recordings", str(self.recordings))

    def approve(self, reference, *extra):
        return self.main(
            "approve",
            reference,
            "--checkpoints",
            str(self.checkpoints),
            "--invoices-dir",
            str(self.invoices),
            "--outbox",
            str(self.outbox),
            *extra,
        )

    def reject(self, reference):
        return self.main("reject", reference, "--checkpoints", str(self.checkpoints))

    def ready(self):
        assert self.invoice() == 0 and self.scan() == 0
        self.record()

    def query(self, sql):
        conn = db.connect(self.db)
        try:
            return [tuple(r) for r in conn.execute(sql)]
        finally:
            conn.close()

    def snapshot(self):
        return {t: self.query(f"SELECT * FROM {t} ORDER BY 1") for t in SNAPSHOT}

    def emails(self):
        return sorted(p.name for p in self.outbox.glob("*.eml")) if self.outbox.exists() else []

    def documents(self):
        return sorted(p.name for p in self.invoices.glob("C*.txt")) if self.invoices.exists() else []


def test_invoice_issues_one_per_note_and_a_second_run_issues_nothing(tmp_path, capsys):
    world = World(tmp_path)
    assert world.invoice() == 0
    out = capsys.readouterr().out
    assert [f"SINV-000{n}" in out for n in (1, 2, 3)] == [True] * 3
    assert world.query("SELECT number FROM customer_invoices ORDER BY id") == [
        ("SINV-0001",),
        ("SINV-0002",),
        ("SINV-0003",),
    ]
    assert sorted(p.name for p in world.invoices.glob("SINV-*.txt")) == [
        "SINV-0001.txt",
        "SINV-0002.txt",
        "SINV-0003.txt",
    ]
    before = world.snapshot()
    assert world.invoice() == 0
    assert "nothing changed" in capsys.readouterr().out and world.snapshot() == before


def test_scan_stores_notes_reconciles_and_a_second_scan_changes_nothing(tmp_path, capsys):
    world = World(tmp_path)
    assert world.scan() == 0
    out = capsys.readouterr().out
    assert "n1.txt  linked" in out and "reconciled SINV-0001  discrepancies" in out and "planned CN-0001" in out
    assert world.query("SELECT result FROM sales_reconciliations ORDER BY id") == [
        ("discrepancies",),
        ("discrepancies",),
        ("matched",),
    ]
    before = world.snapshot()
    assert world.scan() == 0
    assert "nothing changed" in capsys.readouterr().out and world.snapshot() == before


def test_scan_of_a_missing_folder_exits_1(tmp_path, capsys):
    world = World(tmp_path)
    assert world.scan(tmp_path / "none") == 1
    assert capsys.readouterr().err.startswith("Error:")


def test_scan_parks_a_file_out_of_form_and_still_reads_the_others(tmp_path, capsys):
    world = World(tmp_path)
    (world.inbox / "bad.txt").write_text("not a form\n", encoding="utf-8")
    assert world.scan() == 0
    assert world.query("SELECT COUNT(*) FROM signed_delivery_notes") == [(3,)]
    assert world.query("SELECT step, status FROM failures") == [("signed_note", "needs_review")]


def test_responses_draft_one_per_discrepancies_invoice_and_a_second_run_calls_no_model(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    capsys.readouterr()
    before = world.snapshot()
    assert world.responses() == 0
    out = capsys.readouterr().out
    assert "responses drafted: 2" in out and "SINV-0001" in out and "SINV-0002" in out and "SINV-0003" not in out
    assert "source=model" in out and "incident" in out and "document CN-0001" in out
    assert (
        world.query("SELECT status, draft_source FROM customer_responses ORDER BY id")
        == [("awaiting_approval", "model")] * 2
    )
    assert world.snapshot() == before
    rows = world.query("SELECT * FROM customer_responses")
    world.recordings.unlink()
    assert world.responses() == 0
    assert "nothing to be drafted" in capsys.readouterr().out
    assert world.query("SELECT * FROM customer_responses") == rows


def test_a_missing_recording_exits_1_and_stores_no_response(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.recordings.unlink()
    capsys.readouterr()
    assert world.responses() == 1
    assert "Error" in capsys.readouterr().err
    assert world.query("SELECT * FROM customer_responses") == []


def test_approve_sends_the_draft_and_edit_sends_the_file_text_with_the_documents(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.responses()
    capsys.readouterr()
    body = tmp_path / "edit.txt"
    body.write_text("Your credit is on its way.\n", encoding="utf-8")
    assert world.approve("SINV-0001") == 0
    assert world.approve("SINV-0002", "--body-file", str(body)) == 0
    out = capsys.readouterr().out
    assert "SINV-0001 response sent (as drafted)" in out and "SINV-0002 response sent (edited)" in out
    assert world.emails() == ["RESPONSE-SINV-0001.eml", "RESPONSE-SINV-0002.eml"]
    assert world.documents() == ["CI-0003.txt", "CN-0001.txt", "CN-0002.txt"]
    rows = world.query("SELECT status, edited, final_body FROM customer_responses ORDER BY id")
    assert rows[0][:2] == ("sent", 0) and rows[1] == ("sent", 1, "Your credit is on its way.\n")
    assert world.query("SELECT COUNT(*) FROM customer_corrections WHERE document_file IS NOT NULL") == [(3,)]


def test_reject_closes_the_response_and_writes_nothing(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.responses()
    capsys.readouterr()
    assert world.reject("SINV-0001") == 0
    assert "SINV-0001 response rejected" in capsys.readouterr().out
    assert world.query("SELECT status FROM customer_responses ORDER BY id") == [("rejected",), ("awaiting_approval",)]
    assert world.emails() == [] and world.documents() == []


def test_an_empty_or_blank_body_file_is_refused_and_changes_nothing(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.responses()
    capsys.readouterr()
    before = world.query("SELECT * FROM customer_responses")
    for text in ("", "  \n\t\n"):
        path = tmp_path / "blank.txt"
        path.write_text(text, encoding="utf-8")
        assert world.approve("SINV-0001", "--body-file", str(path)) == 1
        assert "empty" in capsys.readouterr().err
    assert world.approve("SINV-0001", "--body-file", str(tmp_path / "missing.txt")) == 1
    assert "Error" in capsys.readouterr().err
    assert world.query("SELECT * FROM customer_responses") == before and world.emails() == []


def test_refused_commands_exit_1_with_a_message_and_leave_at_most_one_email(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.responses()
    assert world.approve("SINV-0001") == 0
    assert world.reject("SINV-0002") == 0
    capsys.readouterr()
    before = (world.query("SELECT * FROM customer_responses"), world.emails(), world.documents())
    for run in (
        lambda: world.approve("SINV-0001"),  # second approve
        lambda: world.reject("SINV-0001"),  # reject of a sent response
        lambda: world.approve("SINV-0002"),  # approve of a rejected response
        lambda: world.reject("SINV-0002"),  # second reject
        lambda: world.approve("SINV-0003"),  # invoice with no response
        lambda: world.reject("SINV-0003"),
        lambda: world.approve("SINV-0099"),  # unknown invoice
        lambda: world.reject("SINV-0099"),
        lambda: world.approve("nonsense"),
    ):
        assert run() == 1
        assert capsys.readouterr().err.startswith("Error:")
    assert (world.query("SELECT * FROM customer_responses"), world.emails(), world.documents()) == before
    assert len(world.emails()) == 1


def test_list_prints_the_result_incident_counts_and_response_status(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.responses()
    assert world.approve("SINV-0001") == 0
    capsys.readouterr()
    assert world.main("list") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "invoices: 3"
    assert "SINV-0001  CDN-0001" in lines[1] and "result=discrepancies" in lines[1]
    assert "incidents: missing=2 extra=0 damaged=1" in lines[1] and lines[1].endswith("response=sent")
    assert "incidents: missing=0 extra=1 damaged=2" in lines[2] and lines[2].endswith("response=awaiting_approval")
    assert "result=matched" in lines[3] and lines[3].endswith("response=-")


def test_show_prints_invoice_incidents_differences_documents_and_response(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    world.responses()
    capsys.readouterr()
    assert world.main("show", "SINV-0001") == 0
    out = capsys.readouterr().out
    for text in (
        "SINV-0001  CDN-0001",
        "line 1",
        "result: discrepancies",
        "signed note:",
        f"incident {A}",
        "difference quantity  incident=missing",
        "document CN-0001  credit_note",
        "response: awaiting_approval",
        "source=model",
        "draft:",
    ):
        assert text in out
    assert world.main("show", "SINV-0003") == 0
    assert "response: none" in capsys.readouterr().out


def test_unknown_or_malformed_reference_in_show_fails_and_changes_nothing(tmp_path, capsys):
    world = World(tmp_path)
    world.ready()
    before = world.snapshot()
    for reference in ("SINV-0099", "nonsense"):
        assert world.main("show", reference) == 1
        assert capsys.readouterr().err.startswith("Error:")
    assert world.snapshot() == before


def test_list_on_an_empty_database_prints_zero_invoices(tmp_path, capsys):
    world = World(tmp_path)
    assert world.main("list") == 0
    assert capsys.readouterr().out.splitlines()[0] == "invoices: 0"
