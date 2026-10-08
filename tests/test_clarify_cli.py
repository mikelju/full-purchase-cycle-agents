"""C6 and C9 (phase 04): the clarify answer, list and close commands and resumption from a new process."""

import dataclasses
import json
import subprocess
import sys
import textwrap

import pytest

from purchase_cycle import cli, db
from purchase_cycle.clarification import detect
from test_clarification_graph import (
    ANSWER_1,
    ANSWER_2,
    DOUBT_FORM,
    EMAIL_ANSWER,
    EMAIL_LINES,
    EMAIL_QUESTION,
    QUESTION_1,
    QUESTION_2,
    RESOLUTIONS_1,
    RESOLUTIONS_2,
    TWO_ROUND_REPLY,
    _clients,
    _email_run,
    _form_graph,
    _form_lines,
    _record_form,
    _record_round,
    _rows,
)

WEB = {"configurable": {"thread_id": "web-1"}}
EMAIL = {"configurable": {"thread_id": "email-1"}}


@pytest.fixture
def paused(seeded_db, write_recording, tmp_path, monkeypatch):
    """One paused web form order (web-1) and one paused email (email-1); the CLI reads the same recordings."""
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    _record_round(catalog, write_recording, doubts[2:], QUESTION_2, ANSWER_2, RESOLUTIONS_2)
    email_doubts = detect(EMAIL_LINES, catalog, "email")
    resolutions = [{"line_id": 3, "action": "remove", "sku": None, "quantity": None}]
    recordings = _record_round(catalog, write_recording, email_doubts, EMAIL_QUESTION, EMAIL_ANSWER, resolutions)
    _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings)).invoke({"submission": DOUBT_FORM}, WEB)
    graph, _, _, graph_input = _email_run(seeded_db, write_recording, tmp_path, EMAIL_LINES, recordings)
    graph.invoke(graph_input, EMAIL)
    for name in ("MATCHING", "EMAIL_INTAKE", "EMAIL_EXTRACTION", "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    return db_path


def _cli(tmp_path, *args):
    return cli.main(["--db", str(tmp_path / "business.db"), "clarify", *args])


def _run(tmp_path, *args):
    extra = [] if args[0] == "list" else ["--checkpoints", str(tmp_path / "checkpoints.db")]
    return _cli(tmp_path, *args, *extra)


def test_list_shows_pending_threads_with_channel_customer_round_and_age(paused, tmp_path, capsys, no_network):
    assert _run(tmp_path, "list") == 0
    out = capsys.readouterr().out
    assert "pending clarifications: 2" in out
    assert "web-1  channel=web_form  customer=CLI-002  round=1  age=0h00m" in out
    assert "email-1  channel=email  customer=CLI-002  round=1  age=0h00m" in out


def test_list_shows_the_age_of_an_older_thread_in_hours_and_minutes(paused, tmp_path, capsys):
    conn = db.connect(paused)
    with conn:
        conn.execute("UPDATE clarifications SET created_at = datetime('now', '-150 minutes') WHERE thread_id = 'web-1'")
    conn.close()
    assert _run(tmp_path, "list") == 0
    out = capsys.readouterr().out
    assert "web-1  channel=web_form  customer=CLI-002  round=1  age=2h30m" in out
    assert out.index("web-1") < out.index("email-1")


def test_list_without_pending_threads(seeded_db, tmp_path, capsys):
    assert _run(tmp_path, "list") == 0
    assert "no pending clarifications" in capsys.readouterr().out


def test_close_stores_clear_lines_and_lists_the_rest_as_unanswered(paused, tmp_path, capsys, no_network):
    assert _run(tmp_path, "close", "web-1") == 0
    out = capsys.readouterr().out
    assert "stored order: 1" in out
    assert "interpretation" not in out
    orders, lines, pending = _rows(paused)
    assert [(line["sku"], line["quantity"]) for line in lines] == [("GLV-NIT-M", 40)]
    assert {row["thread_id"]: row["status"] for row in pending} == {"web-1": "closed", "email-1": "pending"}
    unanswered = (
        "These lines are not part of the order because we received no answer about them:\n"
        '- "nitrile gloves" (10)\n- "hospital bed" (1)\n- "GLV-NIT-S" (900)'
    )
    assert unanswered in out
    assert _run(tmp_path, "list") == 0
    assert "web-1" not in capsys.readouterr().out


def test_answer_or_close_of_a_thread_not_pending_changes_nothing(paused, tmp_path, capsys):
    assert _run(tmp_path, "close", "web-1") == 0
    before = _rows(paused)
    capsys.readouterr()
    assert _run(tmp_path, "close", "web-1") == 1
    assert "thread web-1 is not pending (status closed); nothing changed" in capsys.readouterr().err
    assert _run(tmp_path, "answer", "web-1", "--text", ANSWER_1) == 1
    assert "thread web-1 is not pending" in capsys.readouterr().err
    assert _run(tmp_path, "answer", "nobody", "--text", ANSWER_1) == 1
    assert "no clarification found for thread nobody" in capsys.readouterr().err
    assert _run(tmp_path, "close", "nobody") == 1
    assert _rows(paused) == before


def test_thread_finished_by_another_process_after_the_check_stores_nothing(paused, tmp_path, capsys, monkeypatch):
    checked = cli._pending_row

    def finished_after_check(args):
        row = checked(args)
        conn = db.connect(paused)
        with conn:
            conn.execute("UPDATE clarifications SET status = 'answered' WHERE thread_id = ?", (args.thread_id,))
        conn.close()
        return row

    monkeypatch.setattr(cli, "_pending_row", finished_after_check)
    assert _run(tmp_path, "close", "web-1") == 1
    assert "thread web-1 is not pending; nothing changed" in capsys.readouterr().err
    orders, lines, pending = _rows(paused)
    assert (orders, lines, pending[0]["status"]) == ([], [], "answered")


def test_round_two_question_does_not_reopen_a_thread_closed_meanwhile(paused, tmp_path, capsys, monkeypatch):
    checked = cli._pending_row

    def closed_after_check(args):
        row = checked(args)
        # Another process closes the thread and stores its order after the pending check.
        conn = db.connect(paused)
        db.insert_order(conn, "CLI-002", "web_form", "received", [("GLV-NIT-M", 40)], (args.thread_id, "closed"))
        conn.close()
        return row

    monkeypatch.setattr(cli, "_pending_row", closed_after_check)
    assert _run(tmp_path, "answer", "web-1", "--text", ANSWER_1) == 1
    assert "thread web-1 is not pending; nothing changed" in capsys.readouterr().err
    monkeypatch.setattr(cli, "_pending_row", checked)
    assert _run(tmp_path, "answer", "web-1", "--text", ANSWER_2) == 1
    assert "thread web-1 is not pending (status closed); nothing changed" in capsys.readouterr().err
    orders, _, pending = _rows(paused)
    assert (len(orders), pending[0]["status"], pending[0]["round"]) == (1, "closed", 1)


def test_answer_from_a_file_prints_interpretation_order_and_reply(paused, tmp_path, capsys, no_network):
    answer = tmp_path / "answer.txt"
    answer.write_text(EMAIL_ANSWER + "\n", encoding="utf-8")
    assert _run(tmp_path, "answer", "email-1", "--file", str(answer)) == 0
    out = capsys.readouterr().out
    assert "channel=email  customer=CLI-002" in out
    assert "interpretation:\n  line 3: remove" in out
    assert "stored order: 1" in out
    assert '- "2 hospital beds" (2)' in out
    assert {row["thread_id"]: row["status"] for row in _rows(paused)[2]}["email-1"] == "answered"


def test_answer_with_a_second_round_prints_the_new_question(paused, tmp_path, capsys, no_network):
    assert _run(tmp_path, "answer", "web-1", "--text", ANSWER_1) == 0
    out = capsys.readouterr().out
    assert "line 2: set GLV-NIT-L x 10\n  line 3: remove\n  line 4: unclear" in out
    assert f"new question (round 2):\n{QUESTION_2}" in out
    assert _rows(paused)[:2] == ([], [])
    assert _run(tmp_path, "answer", "web-1", "--text", ANSWER_2) == 0
    assert TWO_ROUND_REPLY in capsys.readouterr().out


PAUSE_STEP = textwrap.dedent(
    """
    import json, sys
    from purchase_cycle import db
    from purchase_cycle.clarification import ClarificationClients
    from purchase_cycle.graph import sqlite_checkpointer
    from purchase_cycle.llm import CLARIFICATION_ANSWER, CLARIFICATION_QUESTION, MATCHING, ModelClient
    from purchase_cycle.web_form import build_web_form_graph

    db_path, recordings, checkpoints, submission = sys.argv[1:5]
    conn = db.connect(db_path)
    catalog = db.catalog_rows(conn)
    conn.close()
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    matcher = ModelClient("replay", catalog, recordings, task=MATCHING)
    graph = build_web_form_graph(matcher, db_path, sqlite_checkpointer(checkpoints), clarification=clients)
    state = graph.invoke({"submission": json.loads(submission)}, {"configurable": {"thread_id": "web-restart"}})
    print(json.dumps({"question": state["__interrupt__"][0].value["question"]}))
    """
)

ANSWER_STEP = textwrap.dedent(
    """
    import dataclasses, socket, sys
    from pathlib import Path
    from purchase_cycle import cli

    def blocked(*args, **kwargs):
        raise OSError("network blocked by test")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    recordings = Path(sys.argv[1])
    for name in ("MATCHING", "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    sys.exit(cli.main(sys.argv[2:]))
    """
)


def test_paused_thread_resumes_from_a_new_process(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2, ANSWER_2, RESOLUTIONS_2)
    checkpoints = tmp_path / "checkpoints.db"
    first = subprocess.run(
        [sys.executable, "-c", PAUSE_STEP, str(db_path), str(recordings), str(checkpoints), json.dumps(DOUBT_FORM)],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout.strip().splitlines()[-1]) == {"question": QUESTION_1}
    assert _rows(db_path) == (
        [],
        [],
        [{"thread_id": "web-restart", "channel": "web_form", "round": 1, "status": "pending"}],
    )
    outputs = []
    for answer in (ANSWER_1, ANSWER_2):
        args = ["--db", str(db_path), "clarify", "answer", "web-restart", "--text", answer]
        step = subprocess.run(
            [sys.executable, "-c", ANSWER_STEP, str(recordings), *args, "--checkpoints", str(checkpoints)],
            capture_output=True,
            text=True,
        )
        assert step.returncode == 0, step.stderr
        outputs.append(step.stdout)
    assert f"new question (round 2):\n{QUESTION_2}" in outputs[0]
    assert TWO_ROUND_REPLY in outputs[1]
    orders, lines, pending = _rows(db_path)
    assert [(line["sku"], line["quantity"]) for line in lines] == [("GLV-NIT-M", 40), ("GLV-NIT-L", 10)]
    assert pending[0]["status"] == "answered"


def test_answer_failing_its_checks_leaves_the_thread_answerable(
    paused, seeded_db, write_recording, tmp_path, capsys, no_network
):
    _, catalog = seeded_db
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    bad = [RESOLUTIONS_1[0], {"line_id": 3, "action": "set", "sku": "BED-1", "quantity": 1}]
    _record_round(catalog, write_recording, doubts, QUESTION_1, "Size L, and a bed.", bad)
    assert _run(tmp_path, "answer", "web-1", "--text", "Size L, and a bed.") == 1
    assert "SKU 'BED-1' is not in the catalog" in capsys.readouterr().err
    assert _rows(paused)[:2] == ([], [])
    assert _run(tmp_path, "list") == 0
    assert "web-1  channel=web_form  customer=CLI-002  round=1" in capsys.readouterr().out
    assert _run(tmp_path, "answer", "web-1", "--text", ANSWER_1) == 0
    out = capsys.readouterr().out
    assert "line 2: set GLV-NIT-L x 10\n  line 3: remove\n  line 4: unclear" in out
    assert f"new question (round 2):\n{QUESTION_2}" in out
