"""C4 and C5 (phase 05): the deterministic channel router and the WhatsApp answer rule."""

import dataclasses
import json

import pytest

from conftest import make_email
from purchase_cycle import db
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.clarification import ClarificationClients, answer_message, detect, doubts_message
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
    ModelClient,
)
from purchase_cycle.router import EMAIL, REJECTED, WEB_FORM, WHATSAPP_ANSWER, WHATSAPP_NEW, route, run_inbox
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph, model_text
from test_whatsapp_order import BODY, CUSTOMER, LINES, ORDER, PHONE, message

FORM = {"submission_id": "WF-1", "customer_code": CUSTOMER.code, "lines": [{"product": "gloves", "quantity": 1}]}
ANSWER = "Remove the bed please."


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def counted_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ModelClient, "extract", lambda self, *args, **kwargs: calls.append(args))
    return calls


def _write(folder, name, content):
    path = folder / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def _route(seeded_db, path):
    conn = db.connect(seeded_db[0])
    try:
        return route(path, conn)
    finally:
        conn.close()


def test_mixed_inbox_is_routed_with_no_model_call(seeded_db, folder, counted_calls, no_network):
    items = {
        "a-form.json": FORM,
        "b-email.eml": None,
        "c-whatsapp.json": message(),
        "d-image.json": message("wamid.IMG", kind="image"),
        "e-unknown-number.json": message("wamid.UNK", sender="34999999999"),
        "f-broken.json": "{not json",
        "g-other.json": {"hello": "world"},
        "h-list.json": [1, 2],
        "i-notes.txt": "40 boxes of gloves",
    }
    for name, content in items.items():
        if content is None:
            make_email(folder / name, CUSTOMER.email, body="Please send 40 boxes of nitrile gloves M")
        else:
            _write(folder, name, content)
    routes = {name: _route(seeded_db, folder / name) for name in items}
    assert {name: r.kind for name, r in routes.items()} == {
        "a-form.json": WEB_FORM,
        "b-email.eml": EMAIL,
        "c-whatsapp.json": WHATSAPP_NEW,
        "d-image.json": WHATSAPP_NEW,  # the WhatsApp graph rejects it and sends the text-only reply
        "e-unknown-number.json": WHATSAPP_NEW,  # the WhatsApp graph rejects it with its reason
        "f-broken.json": REJECTED,
        "g-other.json": REJECTED,
        "h-list.json": REJECTED,
        "i-notes.txt": REJECTED,
    }
    assert "not valid JSON" in routes["f-broken.json"].reason
    assert "neither a web form submission nor a WhatsApp message" in routes["g-other.json"].reason
    assert "neither a web form submission nor a WhatsApp message" in routes["h-list.json"].reason
    assert "'.txt' fits no channel" in routes["i-notes.txt"].reason
    assert all(r.reason is None for r in routes.values() if r.kind != REJECTED)
    assert counted_calls == []
    assert no_network == []


def _pending(db_path, thread_id, channel, customer, when):
    conn = db.connect(db_path)
    try:
        db.save_clarification(conn, thread_id, channel, customer, "Which gloves?", 1)
        with conn:
            conn.execute("UPDATE clarifications SET updated_at = ? WHERE thread_id = ?", (when, thread_id))
    finally:
        conn.close()


def test_whatsapp_text_answers_the_most_recent_pending_whatsapp_thread(seeded_db, folder, counted_calls):
    path = _write(folder, "WA.json", message())
    _pending(seeded_db[0], "whatsapp-old", "whatsapp", CUSTOMER.code, "2026-10-01 10:00:00")
    _pending(seeded_db[0], "whatsapp-new", "whatsapp", CUSTOMER.code, "2026-10-02 10:00:00")
    _pending(seeded_db[0], "whatsapp-other", "whatsapp", CUSTOMERS[0].code, "2026-10-03 10:00:00")
    assert _route(seeded_db, path) == (WHATSAPP_ANSWER, "whatsapp-new", None)
    assert counted_calls == []


def test_whatsapp_text_starts_a_new_order_when_the_latest_pending_thread_is_not_whatsapp(seeded_db, folder):
    path = _write(folder, "WA.json", message())
    _pending(seeded_db[0], "whatsapp-1", "whatsapp", CUSTOMER.code, "2026-10-01 10:00:00")
    _pending(seeded_db[0], "email-1", "email", CUSTOMER.code, "2026-10-02 10:00:00")
    assert _route(seeded_db, path).kind == WHATSAPP_NEW


def test_whatsapp_text_starts_a_new_order_when_no_thread_is_pending(seeded_db, folder):
    path = _write(folder, "WA.json", message())
    assert _route(seeded_db, path).kind == WHATSAPP_NEW
    _pending(seeded_db[0], "whatsapp-1", "whatsapp", CUSTOMER.code, "2026-10-01 10:00:00")
    conn = db.connect(seeded_db[0])
    with conn:
        db.finish_clarification(conn, "whatsapp-1", "answered")
    conn.close()
    assert _route(seeded_db, path).kind == WHATSAPP_NEW


def test_non_text_whatsapp_message_never_answers_a_pending_thread(seeded_db, folder):
    path = _write(folder, "WA.json", message(kind="image"))
    _pending(seeded_db[0], "whatsapp-1", "whatsapp", CUSTOMER.code, "2026-10-01 10:00:00")
    assert _route(seeded_db, path).kind == WHATSAPP_NEW


def _record_whatsapp(catalog, write_recording):
    """Recordings for BODY (an order with an unknown line) and the answer that removes that line."""
    text = model_text({"body": BODY})
    write_recording(catalog, text, ORDER, task=WHATSAPP_INTAKE)
    recordings = write_recording(catalog, text, {"lines": LINES}, task=WHATSAPP_EXTRACTION)
    [doubt] = detect(LINES, catalog, "whatsapp")
    question = f'We do not carry "{doubt["text"]}"; shall we remove it?'
    assert not doubt["candidates"]
    write_recording(catalog, doubts_message([doubt]), {"question": question}, task=CLARIFICATION_QUESTION)
    resolutions = [{"line_id": doubt["line_id"], "action": "remove", "sku": None, "quantity": None}]
    message_text = answer_message([doubt], question, ANSWER)
    write_recording(catalog, message_text, {"resolutions": resolutions}, task=CLARIFICATION_ANSWER)
    return recordings, question


def test_paused_whatsapp_thread_is_resumed_by_a_later_whatsapp_text(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    db_path, catalog = seeded_db
    recordings, question = _record_whatsapp(catalog, write_recording)
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    intake = ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION)
    outbox = tmp_path / "outbox"
    graph = build_whatsapp_order_graph(
        intake, extraction, db_path, outbox, sqlite_checkpointer(tmp_path / "c.db"), clarification=clients
    )
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    results = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    first, second = results
    assert (first["route"].kind, first["thread_id"]) == (WHATSAPP_NEW, "whatsapp-run1-WA-1")
    assert first["state"]["__interrupt__"][0].value["question"] == question
    assert (second["route"].kind, second["thread_id"]) == (WHATSAPP_ANSWER, "whatsapp-run1-WA-1")
    conn = db.connect(db_path)
    orders = [tuple(r) for r in conn.execute("SELECT id, customer_code, channel FROM orders")]
    lines = [tuple(r) for r in conn.execute("SELECT sku, quantity FROM order_lines")]
    status = conn.execute("SELECT status FROM clarifications WHERE thread_id = 'whatsapp-run1-WA-1'").fetchone()[0]
    conn.close()
    assert orders == [(1, CUSTOMER.code, "whatsapp")]
    assert lines == [("GLV-NIT-M", 40)]
    assert status == "answered"
    assert second["state"]["order_id"] == 1
    assert sorted(p.name for p in outbox.iterdir()) == ["question-wamid.ORDER-1.json", "reply-wamid.ORDER.json"]
    sent = json.loads((outbox / "reply-wamid.ORDER.json").read_text(encoding="utf-8"))
    assert (sent["to"], sent["in_reply_to"]) == (PHONE, "wamid.ORDER")
    assert "Your WhatsApp order is registered as order 1" in sent["text"]
    assert (intake.calls, extraction.calls, clients.question.calls, clients.answer.calls) == (1, 1, 1, 1)
    assert no_network == []


def test_run_inbox_reports_rejected_items_without_running_a_graph(seeded_db, folder, counted_calls):
    _write(folder, "notes.txt", "hello")
    [result] = run_inbox(folder, {}, seeded_db[0], "run1")
    assert result["route"].kind == REJECTED
    assert result["thread_id"] is None and result["state"] is None
    assert counted_calls == []


def test_route_command_runs_a_mixed_inbox(
    seeded_db, write_recording, folder, tmp_path, no_network, capsys, monkeypatch
):
    from purchase_cycle import cli

    _, catalog = seeded_db
    recordings, question = _record_whatsapp(catalog, write_recording)
    for name in ("WHATSAPP_INTAKE", "WHATSAPP_EXTRACTION", "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    _write(folder, "notes.txt", "hello")
    outbox = tmp_path / "outbox"
    argv = ["--db", str(tmp_path / "b.db"), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    code = cli.main([*argv, "--outbox", str(outbox)])
    out, err = capsys.readouterr()
    assert code == 1, out
    assert "items=3" in out
    assert "route=whatsapp_new" in out
    assert "route=whatsapp_answer" in out
    assert f"question (round 1):\n{question}" in out
    assert "route=rejected  reason: the file type '.txt' fits no channel" in out
    assert [line for line in out.splitlines() if line.startswith("stored order:")] == ["stored order: 1"]
    assert "Your WhatsApp order is registered as order 1" in out
    assert sorted(p.name for p in outbox.iterdir()) == ["question-wamid.ORDER-1.json", "reply-wamid.ORDER.json"]
    assert err == ""
    assert no_network == []


def test_paused_whatsapp_thread_sends_its_question_to_the_outbox(seeded_db, write_recording, folder, tmp_path):
    db_path, catalog = seeded_db
    recordings, question = _record_whatsapp(catalog, write_recording)
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    intake = ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION)
    outbox = tmp_path / "outbox"
    graph = build_whatsapp_order_graph(
        intake, extraction, db_path, outbox, sqlite_checkpointer(tmp_path / "c.db"), clarification=clients
    )
    path = _write(folder, "WA-1.json", message("wamid.ORDER"))
    for thread in ("whatsapp-1", "whatsapp-2"):  # a second delivery overwrites the same keyed file
        state = graph.invoke({"message_path": str(path)}, {"configurable": {"thread_id": thread}})
        assert state["__interrupt__"][0].value["question"] == question
        assert sorted(p.name for p in outbox.iterdir()) == ["question-wamid.ORDER-1.json"]
    sent = json.loads((outbox / "question-wamid.ORDER-1.json").read_text(encoding="utf-8"))
    assert sent == {"to": PHONE, "in_reply_to": "wamid.ORDER", "text": question}


def test_route_runs_an_email_through_the_re_ask_with_recovery_on(
    seeded_db, write_recording, folder, tmp_path, no_network, capsys, monkeypatch
):
    """Coordinator decision 2026-10-09: recovery is on for `route`, so an invalid extraction is re-asked once."""
    from purchase_cycle import cli
    from purchase_cycle.email_order import model_text as email_text
    from purchase_cycle.email_order import parse_email
    from purchase_cycle.llm import CORRECTION, EMAIL_EXTRACTION, EMAIL_INTAKE
    from test_clarification_graph import EMAIL_BODY, EMAIL_LINES, SENDER

    _, catalog = seeded_db
    path = make_email(folder / "MSG-1.eml", SENDER, "Order", EMAIL_BODY.replace(", plus 2 hospital beds", ""))
    text = email_text(parse_email(path.read_bytes()))
    lines = EMAIL_LINES[:2]
    bad = {"lines": [{**lines[0], "sku": "GLV-NIT-XXL"}, lines[1]]}
    error = "Extracted line 1: SKU 'GLV-NIT-XXL' is not in the catalog"
    write_recording(catalog, text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
    write_recording(catalog, text, bad, task=EMAIL_EXTRACTION)
    recordings = write_recording(
        catalog, text + CORRECTION.format(error=error), {"lines": lines}, task=EMAIL_EXTRACTION
    )
    for name in ("EMAIL_INTAKE", "EMAIL_EXTRACTION"):
        monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    argv = ["--db", str(tmp_path / "b.db"), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    code = cli.main([*argv, "--outbox", str(tmp_path / "outbox")])
    out, err = capsys.readouterr()
    assert (code, err) == (0, ""), out
    assert "route=email" in out
    assert "stored order: 1" in out
    conn = db.connect(tmp_path / "b.db")
    try:
        stored = [tuple(r) for r in conn.execute("SELECT sku, quantity FROM order_lines ORDER BY id")]
        failures = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    finally:
        conn.close()
    assert stored == [(line["sku"], line["quantity"]) for line in lines]
    assert failures == 0
    assert no_network == []


@pytest.mark.parametrize("command", [["answer", "--text", ANSWER], ["close"]])
def test_clarify_commands_build_the_channel_graphs_with_recovery_on(seeded_db, tmp_path, monkeypatch, capsys, command):
    """Coordinator decision 2026-10-09: a thread started by `route` resumes with the same node wiring."""
    from purchase_cycle import cli

    db_path, _ = seeded_db
    _pending(db_path, "email-run-1", "email", CUSTOMER.code, "2026-10-09 10:00:00")
    built = []
    real = cli._clarify_graph

    def spy(args, channel, *rest, **kwargs):
        built.append((channel, kwargs.get("recovery", False)))
        return real(args, channel, *rest, **kwargs)

    monkeypatch.setattr(cli, "_clarify_graph", spy)
    action, options = command[0], command[1:]
    argv = ["--db", str(db_path), "clarify", action, "email-run-1", *options]
    cli.main([*argv, "--checkpoints", str(tmp_path / "c.db")])
    capsys.readouterr()
    assert built == [("email", True)]


def test_route_saves_the_recordings_of_every_channel_client(seeded_db, folder, tmp_path, monkeypatch, capsys):
    """In record mode the channel recordings (matching, intake, extraction) are saved, not only the clarification ones."""
    from purchase_cycle import cli

    saved = []
    monkeypatch.setattr(ModelClient, "save_recordings", lambda self: saved.append(self.task.name) or 0)
    _write(folder, "notes.txt", "hello")
    argv = ["--db", str(tmp_path / "b.db"), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    cli.main([*argv, "--outbox", str(tmp_path / "outbox")])
    capsys.readouterr()
    channel_tasks = {MATCHING.name, EMAIL_INTAKE.name, EMAIL_EXTRACTION.name, WHATSAPP_INTAKE.name}
    assert channel_tasks | {WHATSAPP_EXTRACTION.name, CLARIFICATION_QUESTION.name} <= set(saved)
