"""C4 and C5 (phase 05): the deterministic channel router and the WhatsApp answer rule."""

import dataclasses
import json
from types import SimpleNamespace

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
from purchase_cycle.router import DUPLICATE, EMAIL, REJECTED, WEB_FORM, WHATSAPP_ANSWER, WHATSAPP_NEW, route, run_inbox
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph, model_text, phone_digits
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
    assert sorted(p.name for p in outbox.iterdir()) == [
        "question-34600101201-wamid.ORDER-1.json",
        "reply-34600101201-wamid.ORDER.json",
    ]
    sent = json.loads((outbox / "reply-34600101201-wamid.ORDER.json").read_text(encoding="utf-8"))
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
    assert sorted(p.name for p in outbox.iterdir()) == [
        "question-34600101201-wamid.ORDER-1.json",
        "reply-34600101201-wamid.ORDER.json",
    ]
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
        assert sorted(p.name for p in outbox.iterdir()) == ["question-34600101201-wamid.ORDER-1.json"]
    sent = json.loads((outbox / "question-34600101201-wamid.ORDER-1.json").read_text(encoding="utf-8"))
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


def test_redelivered_whatsapp_order_of_a_paused_thread_is_a_duplicate_not_its_answer(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 1, F4: the paused order's own message delivered again is that order, not the answer to its question."""
    db_path, catalog = seeded_db
    recordings, _ = _record_whatsapp(catalog, write_recording)
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
    _write(folder, "WA-2.json", message("wamid.ORDER"))
    first, second = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    assert first["state"]["__interrupt__"]
    assert (second["route"].kind, second["thread_id"]) == (DUPLICATE, "whatsapp-run1-WA-1")
    assert second["state"] is None and second["error"] is None
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, round, status FROM clarifications")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert clarifications == [("whatsapp-run1-WA-1", 1, "pending")]
    assert orders == 0
    assert sorted(p.name for p in outbox.iterdir()) == ["question-34600101201-wamid.ORDER-1.json"]
    assert (intake.calls, extraction.calls, clients.question.calls, clients.answer.calls) == (1, 1, 1, 0)
    assert no_network == []


def test_redelivered_email_of_a_paused_thread_opens_no_second_clarification(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 1, F4: the same email delivered again while its thread waits for an answer is that thread."""
    from purchase_cycle.email_order import build_email_order_graph, parse_email
    from purchase_cycle.email_order import model_text as email_text
    from purchase_cycle.llm import EMAIL_EXTRACTION, EMAIL_INTAKE
    from test_clarification_graph import EMAIL_BODY, EMAIL_LINES, EMAIL_QUESTION, SENDER

    db_path, catalog = seeded_db
    path = make_email(folder / "MSG-1.eml", SENDER, "Order", EMAIL_BODY)
    (folder / "MSG-2.eml").write_bytes(path.read_bytes())
    text = email_text(parse_email(path.read_bytes()))
    write_recording(catalog, text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
    write_recording(catalog, text, {"lines": EMAIL_LINES}, task=EMAIL_EXTRACTION)
    doubts = detect(EMAIL_LINES, catalog, "email")
    recordings = write_recording(
        catalog, doubts_message(doubts), {"question": EMAIL_QUESTION}, task=CLARIFICATION_QUESTION
    )
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    intake = ModelClient("replay", catalog, recordings, task=EMAIL_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=EMAIL_EXTRACTION)
    graph = build_email_order_graph(
        intake, extraction, db_path, sqlite_checkpointer(tmp_path / "c.db"), clarification=clients, recovery=True
    )
    first, second = run_inbox(folder, {"email": graph}, db_path, "run1")
    assert first["state"]["__interrupt__"][0].value["question"] == EMAIL_QUESTION
    assert (second["route"].kind, second["thread_id"]) == (DUPLICATE, "email-run1-MSG-1")
    assert second["state"] is None and second["error"] is None
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, status FROM clarifications")]
    conn.close()
    assert clarifications == [("email-run1-MSG-1", "pending")]
    assert (intake.calls, extraction.calls, clients.question.calls) == (1, 1, 1)
    assert no_network == []


def test_same_whatsapp_message_id_from_another_customer_is_not_a_duplicate_of_a_paused_thread(seeded_db, folder):
    """Review 1, F4 with the F3 decision: only the same customer's paused thread makes a duplicate."""
    other = CUSTOMERS[2]
    _pending(seeded_db[0], "whatsapp-run1-WA-1", "whatsapp", CUSTOMER.code, "2026-10-01 10:00:00")
    _pending(seeded_db[0], "whatsapp-run1-WA-9", "whatsapp", other.code, "2026-10-01 09:00:00")
    sources = {"whatsapp-run1-WA-1": "wamid.ORDER", "whatsapp-run1-WA-9": "wamid.OTHER"}
    own = _write(folder, "WA-2.json", message("wamid.ORDER"))
    foreign = _write(folder, "WA-3.json", message("wamid.ORDER", sender=other.phone))
    conn = db.connect(seeded_db[0])
    try:
        routes = [route(path, conn, lambda channel, thread: sources[thread]) for path in (own, foreign)]
    finally:
        conn.close()
    assert routes == [(DUPLICATE, "whatsapp-run1-WA-1", None), (WHATSAPP_ANSWER, "whatsapp-run1-WA-9", None)]


def test_route_command_reports_a_duplicate_without_running_it(seeded_db, folder, tmp_path, monkeypatch, capsys):
    from purchase_cycle import cli, router
    from purchase_cycle.router import Route

    _write(folder, "WA-2.json", message("wamid.ORDER"))
    duplicate = {
        "item": "WA-2.json",
        "route": Route(DUPLICATE, "whatsapp-run1-WA-1"),
        "thread_id": "whatsapp-run1-WA-1",
    }
    monkeypatch.setattr(router, "run_inbox", lambda *args: [{**duplicate, "state": None, "error": None}])
    argv = ["--db", str(tmp_path / "b.db"), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    code = cli.main([*argv, "--outbox", str(tmp_path / "outbox")])
    out, err = capsys.readouterr()
    assert (code, err) == (0, ""), out
    assert "== WA-2.json  route=duplicate  thread_id=whatsapp-run1-WA-1" in out
    assert "re-delivery of an order that waits for an answer, nothing run or stored" in out


@pytest.mark.parametrize("command", [["answer", "--text", ANSWER], ["close"]])
def test_clarify_commands_take_the_outbox_folder(seeded_db, tmp_path, monkeypatch, capsys, command):
    """Review 1, I4: `clarify answer` and `close` take `--outbox` like the other commands and pass it to the graph."""
    from purchase_cycle import cli

    db_path, _ = seeded_db
    _pending(db_path, "whatsapp-run-1", "whatsapp", CUSTOMER.code, "2026-10-09 10:00:00")
    outboxes = []
    real = cli.build_whatsapp_order_graph

    def spy(intake, extraction, db_path, outbox, *rest, **kwargs):
        outboxes.append(outbox)
        return real(intake, extraction, db_path, outbox, *rest, **kwargs)

    monkeypatch.setattr(cli, "build_whatsapp_order_graph", spy)
    action, options = command[0], command[1:]
    argv = ["--db", str(db_path), "clarify", action, "whatsapp-run-1", *options]
    cli.main([*argv, "--checkpoints", str(tmp_path / "c.db"), "--outbox", str(tmp_path / "out")])
    capsys.readouterr()
    assert outboxes == [str(tmp_path / "out")]


def test_route_command_reads_bom_prefixed_whatsapp_files_like_any_other(
    seeded_db, write_recording, folder, tmp_path, no_network, capsys, monkeypatch
):
    """Review 1, S5: the router and the WhatsApp reader both accept a UTF-8 BOM, for an order and its answer."""
    from purchase_cycle import cli

    _, catalog = seeded_db
    recordings, _ = _record_whatsapp(catalog, write_recording)
    for name in ("WHATSAPP_INTAKE", "WHATSAPP_EXTRACTION", "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    for name, data in (("WA-1.json", message("wamid.ORDER")), ("WA-2.json", message("wamid.ANSWER", body=ANSWER))):
        (folder / name).write_text(json.dumps(data), encoding="utf-8-sig")
    argv = ["--db", str(tmp_path / "b.db"), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    code = cli.main([*argv, "--outbox", str(tmp_path / "outbox")])
    out, err = capsys.readouterr()
    assert (code, err) == (0, ""), out
    assert "route=whatsapp_new" in out and "route=whatsapp_answer" in out
    assert "Your WhatsApp order is registered as order 1" in out
    assert no_network == []


def _whatsapp_graph(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    recordings, question = _record_whatsapp(catalog, write_recording)
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    intake = ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION)
    graph = build_whatsapp_order_graph(
        intake, extraction, db_path, tmp_path / "outbox", sqlite_checkpointer(tmp_path / "c.db"), clarification=clients
    )
    return graph, (intake, extraction, clients.question, clients.answer), question


class _NoRun:
    """A graph that fails the test if the router runs it."""

    def invoke(self, *args, **kwargs):
        raise AssertionError("the item ran a graph")

    def get_state(self, config, **kwargs):
        return SimpleNamespace(values={}, next=())  # no checkpoint: the stored thread is unknown here


def test_redelivered_stored_whatsapp_order_is_a_duplicate_and_a_new_order_is_not_lost(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 2, B1: WA-1 pauses, WA-2 answers and stores order 1, WA-3 re-delivers WA-1, WA-4 is a new order."""
    db_path = seeded_db[0]
    graph, calls, question = _whatsapp_graph(seeded_db, write_recording, tmp_path)
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    _write(folder, "WA-3.json", message("wamid.ORDER"))
    _write(folder, "WA-4.json", message("wamid.NEW"))
    results = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    kinds = [(r["route"].kind, r["thread_id"], r["error"]) for r in results]
    assert kinds == [
        (WHATSAPP_NEW, "whatsapp-run1-WA-1", None),
        (WHATSAPP_ANSWER, "whatsapp-run1-WA-1", None),
        (DUPLICATE, "whatsapp-run1-WA-1", None),
        (WHATSAPP_NEW, "whatsapp-run1-WA-4", None),
    ]
    assert "registered as order 1" in results[2]["state"]["reply"]  # review 3, I1: the reply names the order
    assert results[2]["unfinished"] is False
    assert results[3]["state"]["__interrupt__"][0].value["question"] == question
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, status FROM clarifications ORDER BY rowid")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert clarifications == [("whatsapp-run1-WA-1", "answered"), ("whatsapp-run1-WA-4", "pending")]
    assert orders == 1
    assert [c.calls for c in calls] == [2, 2, 2, 1]
    assert no_network == []


def test_redelivered_stored_email_is_a_duplicate_with_no_graph_run(seeded_db, folder):
    """Review 2, B1 for email: an email already stored as an order is not extracted or asked again."""
    from purchase_cycle.email_order import read_email
    from test_clarification_graph import EMAIL_BODY, SENDER

    db_path = seeded_db[0]
    path = make_email(folder / "MSG-2.eml", SENDER, "Order", EMAIL_BODY)
    conn = db.connect(db_path)
    email = read_email(path, conn)
    order = db.insert_order(
        conn, email["customer"]["code"], "email", "new", [("GLV-NIT-M", 1)], source=(email["message_id"], "email-r-1")
    )
    conn.close()
    [result] = run_inbox(folder, {"email": _NoRun()}, db_path, "run2")
    assert (result["route"].kind, result["thread_id"], result["error"]) == (DUPLICATE, "email-r-1", None)
    assert result["route"].reason == f"already stored as order {order}"
    assert f'Your email order "Order" is registered as order {order}' in result["state"]["reply"]


@pytest.mark.parametrize("channel", ["email", "whatsapp"])
def test_message_id_stored_for_another_customer_is_rejected_before_any_question(seeded_db, folder, channel):
    """Review 2, I1: the same message id from another customer gets no question, no thread and no order."""
    db_path = seeded_db[0]
    owner, other = CUSTOMERS[1], CUSTOMERS[2]
    if channel == "email":
        item = make_email(folder / "MSG-1.eml", other.email, "Order", "40 boxes of nitrile gloves M")
        conn = db.connect(db_path)
        from purchase_cycle.email_order import read_email

        message_id = read_email(item, conn)["message_id"]
        conn.close()
    else:
        message_id = "wamid.SHARED"
        _write(folder, "WA-1.json", message(message_id, sender=other.phone))
    conn = db.connect(db_path)
    db.insert_order(conn, owner.code, channel, "new", [("GLV-NIT-M", 1)], source=(message_id, f"{channel}-r-1"))
    conn.close()
    [result] = run_inbox(folder, {channel: _NoRun()}, db_path, "run2")
    assert (result["route"].kind, result["state"], result["error"]) == (REJECTED, None, None)
    assert "already stored for another customer" in result["route"].reason
    conn = db.connect(db_path)
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "clarifications")]
    conn.close()
    assert counts == [1, 0]


def test_route_command_reports_a_stored_duplicate_without_running_it(seeded_db, folder, tmp_path, capsys):
    """Review 2, B1 and review 3, I1: `route` replies with the stored order a re-delivered message repeats, exits 0."""
    from purchase_cycle import cli

    db_path = seeded_db[0]
    _write(folder, "WA-3.json", message("wamid.ORDER"))
    conn = db.connect(db_path)
    db.insert_order(conn, CUSTOMER.code, "whatsapp", "new", [("GLV-NIT-M", 1)], source=("wamid.ORDER", "whatsapp-r-1"))
    conn.close()
    argv = ["--db", str(db_path), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    code = cli.main([*argv, "--outbox", str(tmp_path / "outbox")])
    out, err = capsys.readouterr()
    assert (code, err) == (0, ""), out
    assert "== WA-3.json  route=duplicate  thread_id=whatsapp-r-1" in out
    assert "re-delivery of a message already stored as order 1, nothing run or stored" in out
    [reply] = (tmp_path / "outbox").iterdir()
    assert reply.name == f"reply-{phone_digits(CUSTOMER.phone)}-wamid.ORDER.json"
    assert "registered as order 1" in json.loads(reply.read_text(encoding="utf-8"))["text"]
    conn = db.connect(db_path)
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "order_sources", "failures")]
    conn.close()
    assert counts == [1, 1, 0]


def test_answered_second_thread_of_a_stored_message_is_finished_not_left_pending(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 2, B1 defence: a thread whose `store` returns an order stored before still finishes its question."""
    from langgraph.types import Command

    db_path = seeded_db[0]
    graph, _, _ = _whatsapp_graph(seeded_db, write_recording, tmp_path)
    path = _write(folder, "WA-1.json", message("wamid.ORDER"))
    states = []
    for thread in ("whatsapp-t1", "whatsapp-t2"):  # the channel graph alone does not check the stored sources
        config = {"configurable": {"thread_id": thread}}
        assert graph.invoke({"message_path": str(path)}, config)["__interrupt__"]
        states.append(graph.invoke(Command(resume={"answer": ANSWER}), config))
    assert [s["order_id"] for s in states] == [1, 1]
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, status FROM clarifications ORDER BY rowid")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert clarifications == [("whatsapp-t1", "answered"), ("whatsapp-t2", "answered")]
    assert orders == 1
    assert no_network == []


def _paused_then_taken(seeded_db, write_recording, folder, tmp_path):
    """Customer B's WhatsApp order pauses, then customer A's order is stored with the same message id."""
    db_path = seeded_db[0]
    graph, _, _ = _whatsapp_graph(seeded_db, write_recording, tmp_path)
    other = CUSTOMERS[2]
    _write(folder, "WA-1.json", message("wamid.SHARED", sender=other.phone))
    [first] = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    assert first["state"]["__interrupt__"]
    (folder / "WA-1.json").unlink()
    conn = db.connect(db_path)
    db.insert_order(conn, CUSTOMER.code, "whatsapp", "new", [("GLV-NIT-M", 1)], source=("wamid.SHARED", "wa-a"))
    conn.close()
    return graph, other


def _status(db_path, thread):
    conn = db.connect(db_path)
    try:
        return conn.execute("SELECT status FROM clarifications WHERE thread_id = ?", (thread,)).fetchone()[0]
    finally:
        conn.close()


def test_answer_whose_order_id_another_customer_took_closes_the_thread(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 2, I1: the answer fails with SourceConflict once and the customer's next message is a new order."""
    from purchase_cycle.db import SourceConflict

    db_path = seeded_db[0]
    graph, other = _paused_then_taken(seeded_db, write_recording, folder, tmp_path)
    _write(folder, "WA-2.json", message("wamid.ANSWER", sender=other.phone, body=ANSWER))
    [answer] = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    assert (answer["route"].kind, answer["thread_id"]) == (WHATSAPP_ANSWER, "whatsapp-run1-WA-1")
    assert isinstance(answer["error"], SourceConflict)
    assert _status(db_path, "whatsapp-run1-WA-1") == "closed"
    (folder / "WA-2.json").unlink()
    _write(folder, "WA-3.json", message("wamid.NEXT", sender=other.phone))
    assert _route(seeded_db, folder / "WA-3.json").kind == WHATSAPP_NEW


def test_clarify_answer_reports_a_source_conflict_cleanly(
    seeded_db, write_recording, folder, tmp_path, no_network, capsys, monkeypatch
):
    """Review 2, I1: `clarify answer` prints an Error line, exits 1 and closes the thread, with no traceback."""
    from purchase_cycle import cli

    db_path, catalog = seeded_db
    _paused_then_taken(seeded_db, write_recording, folder, tmp_path)
    recordings = tmp_path / "recordings.jsonl"
    for name in ("WHATSAPP_INTAKE", "WHATSAPP_EXTRACTION", "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    argv = ["--db", str(db_path), "clarify", "answer", "whatsapp-run1-WA-1", "--text", ANSWER]
    code = cli.main([*argv, "--checkpoints", str(tmp_path / "c.db"), "--outbox", str(tmp_path / "outbox")])
    out, err = capsys.readouterr()
    assert code == 1, out
    assert err.startswith("Error: ") and "already stored for another customer" in err
    assert _status(db_path, "whatsapp-run1-WA-1") == "closed"


def test_two_customers_with_the_same_message_id_never_share_an_outbox_file(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 2, I1 and SEC-008: outbox file names carry the sender, so one question never overwrites the other."""
    graph, _, question = _whatsapp_graph(seeded_db, write_recording, tmp_path)
    other = CUSTOMERS[2]
    for name, sender in (("WA-1.json", PHONE), ("WA-2.json", other.phone)):
        path = _write(folder, name, message("wamid.ORDER", sender=sender))
        assert graph.invoke({"message_path": str(path)}, {"configurable": {"thread_id": name}})["__interrupt__"]
    outbox = tmp_path / "outbox"
    digits = "".join(c for c in other.phone if c.isdigit())
    names = sorted(p.name for p in outbox.iterdir())
    assert names == sorted([f"question-{PHONE}-wamid.ORDER-1.json", f"question-{digits}-wamid.ORDER-1.json"])
    for name in names:
        assert json.loads((outbox / name).read_text(encoding="utf-8"))["text"] == question


def test_redelivered_answer_of_a_still_pending_thread_is_a_duplicate_not_a_second_answer(
    seeded_db, write_recording, folder, tmp_path, no_network, capsys, monkeypatch
):
    """Review 2, M1: WA-2 answers but leaves the line unclear, so round 2 waits; WA-3 re-delivers WA-2."""
    from purchase_cycle import cli

    _, catalog = seeded_db
    text = model_text({"body": BODY})
    write_recording(catalog, text, ORDER, task=WHATSAPP_INTAKE)
    recordings = write_recording(catalog, text, {"lines": LINES}, task=WHATSAPP_EXTRACTION)
    [doubt] = detect(LINES, catalog, "whatsapp")
    question = f'We do not carry "{doubt["text"]}"; shall we remove it?'
    write_recording(catalog, doubts_message([doubt]), {"question": question}, task=CLARIFICATION_QUESTION)
    unclear = [{"line_id": doubt["line_id"], "action": "unclear", "sku": None, "quantity": None}]
    write_recording(
        catalog, answer_message([doubt], question, ANSWER), {"resolutions": unclear}, task=CLARIFICATION_ANSWER
    )
    for name in ("WHATSAPP_INTAKE", "WHATSAPP_EXTRACTION", "CLARIFICATION_QUESTION", "CLARIFICATION_ANSWER"):
        monkeypatch.setattr(cli, name, dataclasses.replace(getattr(cli, name), recordings_path=recordings))
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    _write(folder, "WA-3.json", message("wamid.ANSWER", body=ANSWER))
    outbox = tmp_path / "outbox"
    argv = ["--db", str(tmp_path / "b.db"), "route", str(folder), "--checkpoints", str(tmp_path / "c.db")]
    code = cli.main([*argv, "--outbox", str(outbox)])
    out, err = capsys.readouterr()
    assert (code, err) == (0, ""), out
    assert out.count("route=whatsapp_answer") == 1
    assert "== WA-3.json  route=duplicate" in out
    assert "re-delivery of a message already applied as an answer to this thread, nothing run or stored" in out
    assert sorted(p.name for p in outbox.iterdir()) == [
        f"question-{PHONE}-wamid.ORDER-1.json",
        f"question-{PHONE}-wamid.ORDER-2.json",
    ]
    conn = db.connect(tmp_path / "b.db")
    pending = conn.execute("SELECT status, round FROM clarifications").fetchall()
    failures = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    conn.close()
    assert [tuple(r) for r in pending] == [("pending", 2)]
    assert failures == 0
    assert no_network == []


class _QuestionOnce:
    """A question client that drafts round 1 and then fails, so the next question can never be drafted."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def extract(self, *args, **kwargs):
        from purchase_cycle.llm import MissingRecording

        if self.inner.calls:
            raise MissingRecording("no recorded question for round 2")
        return self.inner.extract(*args, **kwargs)


@pytest.mark.parametrize("failure", ["next_question", "invalid_answer"])
def test_an_answer_not_applied_is_not_kept_and_its_redelivery_is_an_answer(
    seeded_db, write_recording, folder, tmp_path, no_network, failure
):
    """Review 3, M1: an answer undone by a failed next question, or rejected by its checks, is not in
    `applied_answers`, so its re-delivery is routed as an answer again, not as a duplicate."""
    db_path, catalog = seeded_db
    text = model_text({"body": BODY})
    write_recording(catalog, text, ORDER, task=WHATSAPP_INTAKE)
    recordings = write_recording(catalog, text, {"lines": LINES}, task=WHATSAPP_EXTRACTION)
    [doubt] = detect(LINES, catalog, "whatsapp")
    question = f'We do not carry "{doubt["text"]}"; shall we remove it?'
    write_recording(catalog, doubts_message([doubt]), {"question": question}, task=CLARIFICATION_QUESTION)
    if failure == "next_question":  # the line stays unclear, so round 2 needs a question that fails
        resolution = {"action": "unclear", "sku": None, "quantity": None}
    else:
        resolution = {"action": "set", "sku": "NOT-A-SKU", "quantity": 1}
    answer = {"resolutions": [{"line_id": doubt["line_id"], **resolution}]}
    write_recording(catalog, answer_message([doubt], question, ANSWER), answer, task=CLARIFICATION_ANSWER)
    clients = ClarificationClients(
        _QuestionOnce(ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION)),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    intake = ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION)
    graph = build_whatsapp_order_graph(
        intake, extraction, db_path, tmp_path / "outbox", sqlite_checkpointer(tmp_path / "c.db"), clarification=clients
    )
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    first, second = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    assert (second["route"].kind, second["error"]) == (WHATSAPP_ANSWER, None)
    waiting = second["state"]["__interrupt__"][0].value
    expected = "the next question could not be drafted" if failure == "next_question" else "SKU 'NOT-A-SKU'"
    assert expected in waiting["rejected"]
    assert (waiting["question"], waiting["round"]) == (question, 1)
    config = {"configurable": {"thread_id": "whatsapp-run1-WA-1"}}

    def applied(channel, thread):
        snapshot = graph.get_state({"configurable": {"thread_id": thread}}, subgraphs=True)
        return [m for task in snapshot.tasks if task.state for m in task.state.values.get("applied_answers", [])]

    assert applied("whatsapp", "whatsapp-run1-WA-1") == []
    assert graph.get_state(config).next  # still paused on the round 1 question
    redelivered = _write(folder, "WA-3.json", message("wamid.ANSWER", body=ANSWER))
    conn = db.connect(db_path)
    try:
        routed = route(redelivered, conn, lambda channel, thread: "wamid.ORDER", applied)
    finally:
        conn.close()
    assert routed == (WHATSAPP_ANSWER, "whatsapp-run1-WA-1", None)
    assert no_network == []


def test_reused_run_id_with_a_repeated_file_stem_runs_nothing_on_the_old_thread(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 4, S1: a second run with the same run id and file stem must not resume the first customer's thread."""
    db_path = seeded_db[0]
    graph, calls, _ = _whatsapp_graph(seeded_db, write_recording, tmp_path)
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    [first] = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    assert first["state"]["__interrupt__"]
    before = [c.calls for c in calls]
    _write(folder, "WA-1.json", message("wamid.OTHER", sender=CUSTOMERS[2].phone))
    [second] = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    assert (second["route"].kind, second["thread_id"], second["state"]) == (WHATSAPP_NEW, "whatsapp-run1-WA-1", None)
    assert "already used" in str(second["error"]) and "--run-id" in str(second["error"])
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, customer_code, status FROM clarifications")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert clarifications == [("whatsapp-run1-WA-1", CUSTOMER.code, "pending")]
    assert orders == 0
    assert [c.calls for c in calls] == before
    assert no_network == []


def test_redelivered_answer_of_a_finished_thread_is_a_duplicate_not_an_answer_to_a_later_thread(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 4, K1: WA-2 answers WA-1 and stores it, WA-4 pauses, WA-5 re-delivers WA-2 and must not answer WA-4."""
    db_path = seeded_db[0]
    graph, calls, _ = _whatsapp_graph(seeded_db, write_recording, tmp_path)
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    _write(folder, "WA-4.json", message("wamid.NEW"))
    _write(folder, "WA-5.json", message("wamid.ANSWER", body=ANSWER))
    results = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    kinds = [(r["route"].kind, r["thread_id"], r["error"]) for r in results]
    assert kinds == [
        (WHATSAPP_NEW, "whatsapp-run1-WA-1", None),
        (WHATSAPP_ANSWER, "whatsapp-run1-WA-1", None),
        (WHATSAPP_NEW, "whatsapp-run1-WA-4", None),
        (DUPLICATE, "whatsapp-run1-WA-1", None),
    ]
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, status FROM clarifications ORDER BY rowid")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert clarifications == [("whatsapp-run1-WA-1", "answered"), ("whatsapp-run1-WA-4", "pending")]
    assert orders == 1
    assert [c.calls for c in calls] == [2, 2, 2, 1]
    assert no_network == []


class _Crashed(_NoRun):
    """A graph whose checkpoint for the thread stopped mid-run (a crash), with steps still to run."""

    def get_state(self, config, **kwargs):
        return SimpleNamespace(values={"customer_code": CUSTOMER.code}, next=("store",), interrupts=())


def test_reused_thread_id_of_an_unfinished_thread_points_to_resume(seeded_db, folder):
    """Review 5, R5-1: a new --run-id would leave the crashed thread behind; `resume` is the way to finish it."""
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    [result] = run_inbox(folder, {"whatsapp": _Crashed()}, seeded_db[0], "run1")
    assert (result["route"].kind, result["state"]) == (WHATSAPP_NEW, None)
    error = str(result["error"])
    assert "has not finished" in error and "purchase-cycle resume whatsapp-run1-WA-1" in error


def test_redelivered_round_one_answer_of_a_finished_two_answer_thread_is_a_duplicate(
    seeded_db, write_recording, folder, tmp_path, no_network
):
    """Review 5, R5-3: `applied_answers` keeps the round 1 answer after round 2 finishes the thread."""
    db_path, catalog = seeded_db
    text = model_text({"body": BODY})
    write_recording(catalog, text, ORDER, task=WHATSAPP_INTAKE)
    recordings = write_recording(catalog, text, {"lines": LINES}, task=WHATSAPP_EXTRACTION)
    [doubt] = detect(LINES, catalog, "whatsapp")
    question = f'We do not carry "{doubt["text"]}"; shall we remove it?'
    write_recording(catalog, doubts_message([doubt]), {"question": question}, task=CLARIFICATION_QUESTION)
    second_answer = "Yes, remove it."
    for answer, action in ((ANSWER, "unclear"), (second_answer, "remove")):
        resolutions = [{"line_id": doubt["line_id"], "action": action, "sku": None, "quantity": None}]
        message_text = answer_message([doubt], question, answer)
        write_recording(catalog, message_text, {"resolutions": resolutions}, task=CLARIFICATION_ANSWER)
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    intake = ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION)
    graph = build_whatsapp_order_graph(
        intake, extraction, db_path, tmp_path / "outbox", sqlite_checkpointer(tmp_path / "c.db"), clarification=clients
    )
    _write(folder, "WA-1.json", message("wamid.ORDER"))
    _write(folder, "WA-2.json", message("wamid.ANSWER", body=ANSWER))
    _write(folder, "WA-3.json", message("wamid.ANSWER2", body=second_answer))
    _write(folder, "WA-4.json", message("wamid.ANSWER", body=ANSWER))
    results = run_inbox(folder, {"whatsapp": graph}, db_path, "run1")
    kinds = [(r["route"].kind, r["thread_id"], r["error"]) for r in results]
    assert kinds == [
        (WHATSAPP_NEW, "whatsapp-run1-WA-1", None),
        (WHATSAPP_ANSWER, "whatsapp-run1-WA-1", None),
        (WHATSAPP_ANSWER, "whatsapp-run1-WA-1", None),
        (DUPLICATE, "whatsapp-run1-WA-1", None),
    ]
    assert "__interrupt__" not in results[2]["state"]
    conn = db.connect(db_path)
    clarifications = [tuple(r) for r in conn.execute("SELECT thread_id, status, round FROM clarifications")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert clarifications == [("whatsapp-run1-WA-1", "answered", 2)]
    assert orders == 1
    assert (clients.question.calls, clients.answer.calls) == (2, 2)
    assert no_network == []
