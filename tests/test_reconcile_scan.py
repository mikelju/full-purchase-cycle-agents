"""C3 and C8: the reconcile graph and `reconcile scan FOLDER`."""

import sqlite3

import anthropic
import httpx
import pytest

from purchase_cycle import cli, db, llm, reconciliation
from purchase_cycle.graph import sqlite_checkpointer
from test_reconciliation import ANSWER, SUPPLIER_NAME, _client, _order, _write

NOTE = {**ANSWER, "kind": "delivery_note", "number": "DN-5", "currency": None, "supplier_text": SUPPLIER_NAME}
NOTE["lines"] = [{"product": ANSWER["lines"][0]["product"], "quantity": 10, "unit_price": None, "line_total": None}]
INVOICE = {**ANSWER, "supplier_text": SUPPLIER_NAME}


class World:
    def __init__(self, tmp_path, names=("a-invoice.txt", "b-note.txt"), answers=(INVOICE, NOTE)):
        self.tmp = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.folder = tmp_path / "docs"
        self.folder.mkdir()
        self.db = tmp_path / "w.db"
        self.checkpoints = tmp_path / "cp.sqlite"
        conn = db.connect(self.db)
        db.seed(conn)
        _order(conn)
        conn.close()
        self.paths = [_write(self.folder, name, f"document {name}") for name in names]
        self.client = _client(tmp_path, self.paths, list(answers))
        self.recordings = self.client.recordings_path

    def run(self, *args, mode="replay"):
        return cli.main(
            [
                "--db",
                str(self.db),
                "reconcile",
                "scan",
                str(self.folder),
                "--checkpoints",
                str(self.checkpoints),
                "--mode",
                mode,
                "--recordings",
                str(self.recordings),
                *args,
            ]
        )

    def rows(self):
        conn = db.connect(self.db)
        try:
            docs = [tuple(r) for r in conn.execute("SELECT kind, status, reason FROM supplier_documents ORDER BY kind")]
            recs = [tuple(r) for r in conn.execute("SELECT result FROM reconciliations")]
            diffs = [tuple(r) for r in conn.execute("SELECT sku, type, amount FROM discrepancies ORDER BY sku, type")]
            counts = [
                conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("supplier_documents", "reconciliations", "discrepancies", "document_lines")
            ]
        finally:
            conn.close()
        return docs, recs, diffs, counts


def test_five_nodes_run_per_document_with_a_checkpoint_thread_each(tmp_path):
    world = World(tmp_path)
    graph = reconciliation.build_reconcile_graph(world.client, world.db, sqlite_checkpointer(world.checkpoints))
    config = {"configurable": {"thread_id": "t-1"}}
    steps = [
        next(iter(update)) for update in graph.stream({"path": str(world.paths[0])}, config, stream_mode="updates")
    ]
    assert steps == ["read", "extract", "link", "match", "store"]
    assert graph.get_state(config).next == ()  # no pause
    other = World(tmp_path / "other")
    assert other.run() == 0
    threads = {r[0] for r in sqlite3.connect(other.checkpoints).execute("SELECT DISTINCT thread_id FROM checkpoints")}
    assert len({t for t in threads if t.startswith("reconcile-")}) == 2


def test_scan_stores_and_reconciles(tmp_path, capsys):
    world = World(tmp_path)
    assert world.run() == 0
    docs, recs, diffs, counts = world.rows()
    assert recs == [("matched",)] and diffs == [] and counts[:2] == [2, 1]
    out = capsys.readouterr().out
    assert "a-invoice.txt" in out and "matched" in out


def test_a_second_scan_adds_nothing_and_calls_no_model(tmp_path):
    world = World(tmp_path)
    assert world.run() == 0
    before = world.rows()
    world.recordings.unlink()
    client = llm.ModelClient("replay", [], world.recordings, task=llm.SUPPLIER_DOCUMENT_EXTRACTION)
    results = reconciliation.scan(world.db, sqlite_checkpointer(world.checkpoints), world.folder, client)
    assert client.calls == 0 and not any(item["new"] for item in results)
    assert world.run() == 0  # the command with no recordings file at all
    assert world.rows() == before


def test_the_same_documents_give_the_same_rows_and_output_in_a_second_world(tmp_path, capsys):
    outputs = []
    rows = []
    for name in ("one", "two"):
        world = World(tmp_path / name)
        assert world.run() == 0
        outputs.append(capsys.readouterr().out.replace(str(world.folder), "FOLDER"))
        rows.append(world.rows())
    assert outputs[0] == outputs[1] and rows[0] == rows[1]


def test_a_file_changed_in_content_is_a_new_document(tmp_path):
    world = World(tmp_path)
    assert world.run() == 0
    world.paths[1].write_text("document b-note.txt, corrected", encoding="utf-8")
    again = _client(tmp_path, [world.paths[1]], [NOTE], "again.jsonl")
    world.recordings.write_text(
        world.recordings.read_text(encoding="utf-8") + again.recordings_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert world.run() == 0
    docs, recs, _, counts = world.rows()
    assert counts[0] == 3 and ("delivery_note", "unlinked", "duplicate") in docs and recs == [("matched",)]


@pytest.mark.parametrize("names", [("a-invoice.txt", "b-note.txt"), ("b-invoice.txt", "a-note.txt")])
def test_the_order_of_the_files_does_not_change_the_result(tmp_path, names):
    answers = (INVOICE, NOTE) if names[0] < names[1] else (NOTE, INVOICE)
    world = World(tmp_path, names=names if names[0] < names[1] else tuple(sorted(names)), answers=answers)
    assert world.run() == 0
    docs, recs, diffs, counts = world.rows()
    assert recs == [("matched",)] and diffs == [] and counts[:3] == [2, 1, 0]


def test_a_transient_model_error_is_retried_through_model_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "MODEL_RETRY", llm.MODEL_RETRY._replace(initial_interval=0, max_interval=0, jitter=False))
    world = World(tmp_path, names=("a-invoice.txt",), answers=(INVOICE,))
    real = world.client.extract
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example.com"))
        return real(*args, **kwargs)

    world.client.extract = flaky
    results = reconciliation.scan(world.db, sqlite_checkpointer(world.checkpoints), world.folder, world.client)
    assert len(attempts) == 2 and results[0]["status"] == "linked" and results[0]["result"] == "awaiting_delivery_note"


def test_a_missing_recording_exits_1_with_a_message(tmp_path, capsys):
    world = World(tmp_path)
    world.recordings.unlink()
    assert world.run() == 1
    assert "No recording" in capsys.readouterr().err
