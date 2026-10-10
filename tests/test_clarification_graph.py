"""C2, C4, C5, C7 and C8 (phase 04): the clarify step in both channel graphs with recorded answers."""

import pytest
from langgraph.types import Command

from conftest import make_email
from purchase_cycle import db
from purchase_cycle.catalog import CUSTOMERS
from purchase_cycle.clarification import (
    ClarificationClients,
    InvalidQuestion,
    answer_message,
    detect,
    doubts_message,
)
from purchase_cycle.email_order import build_email_order_graph, model_text, parse_email
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    ModelClient,
)
from purchase_cycle.web_form import build_reply, build_web_form_graph

NAMES = {size: f"Nitrile examination gloves, powder-free, size {size}" for size in ("L", "M", "S", "XL", "XS")}
CLEAR_FORM = {
    "submission_id": "WF-CLEAR-1",
    "customer_code": "CLI-002",
    "lines": [{"product": "GLV-NIT-M", "quantity": 40}, {"product": "alcohol 70 250ml", "quantity": 12}],
}
DOUBT_FORM = {
    "submission_id": "WF-DOUBT-1",
    "customer_code": "CLI-002",
    "lines": [
        {"product": "GLV-NIT-M", "quantity": 40},
        {"product": "nitrile gloves", "quantity": 10},
        {"product": "hospital bed", "quantity": 1},
        {"product": "GLV-NIT-S", "quantity": 900},
    ],
}
QUESTION_1 = (
    'Thank you for your order. For "nitrile gloves" (10 boxes), which one do you want: '
    + ", ".join(NAMES.values())
    + '? We do not carry "hospital bed"; can you describe it in another way or shall we remove it? '
    'For "GLV-NIT-S" we read 900 boxes; please confirm or correct the quantity.'
)
QUESTION_2 = 'For "GLV-NIT-S" we read 900 boxes; please confirm or correct the quantity.'
ANSWER_1 = "Size L for the gloves, forget the bed. Not sure about the small ones yet."
ANSWER_2 = "Still checking, sorry."
RESOLUTIONS_1 = [
    {"line_id": 2, "action": "set", "sku": "GLV-NIT-L", "quantity": 10},
    {"line_id": 3, "action": "remove", "sku": None, "quantity": None},
    {"line_id": 4, "action": "unclear", "sku": None, "quantity": None},
]
RESOLUTIONS_2 = [{"line_id": 4, "action": "unclear", "sku": None, "quantity": None}]
TWO_ROUND_REPLY = """Dear Iker Zubiri,

Thank you. Your web form order WF-DOUBT-1 is registered as order 1:
- Nitrile examination gloves, powder-free, size M: 40 x box of 100 at 6.90 EUR = 276.00 EUR
- Nitrile examination gloves, powder-free, size L: 10 x box of 100 at 6.90 EUR = 69.00 EUR
Order total: 345.00 EUR

As you asked, these lines are removed from the order:
- "hospital bed" (1)

These lines are not part of the order because we could not clarify them:
- "GLV-NIT-S" (900)

Products not in your original order are not added from your answer; please send them as a new order.

Kind regards,
Customer service"""

SENDER = CUSTOMERS[1].email  # CLI-002, Iker Zubiri
EMAIL_LINES = [
    {"source": "body", "source_text": "40 boxes of nitrile gloves M", "sku": "GLV-NIT-M", "quantity": 40},
    {"source": "body", "source_text": "12 x alcohol 70% 250 ml", "sku": "ALC70-250", "quantity": 12},
    {"source": "body", "source_text": "2 hospital beds", "sku": None, "quantity": 2},
]
EMAIL_BODY = (
    "Hello,\n\nPlease send 40 boxes of nitrile gloves M and 12 x alcohol 70% 250 ml, plus 2 hospital beds.\n\nIker"
)
EMAIL_QUESTION = 'We do not carry "2 hospital beds"; can you describe them in another way or shall we remove them?'
EMAIL_ANSWER = "Please remove the beds."


def _record_form(catalog, write_recording):
    write_recording(catalog, "alcohol 70 250ml", {"sku": "ALC70-250"}, task=MATCHING)
    write_recording(catalog, "nitrile gloves", {"sku": None}, task=MATCHING)
    return write_recording(catalog, "hospital bed", {"sku": None}, task=MATCHING)


def _form_lines(catalog):
    """The lines `match` produces for DOUBT_FORM with the recordings above."""
    skus = ["GLV-NIT-M", None, None, "GLV-NIT-S"]
    return [{**line, "sku": sku} for line, sku in zip(DOUBT_FORM["lines"], skus, strict=True)]


def _record_round(catalog, write_recording, doubts, question, answer=None, resolutions=None):
    path = write_recording(catalog, doubts_message(doubts), {"question": question}, task=CLARIFICATION_QUESTION)
    if answer is not None:
        message = answer_message(doubts, question, answer)
        path = write_recording(catalog, message, {"resolutions": resolutions}, task=CLARIFICATION_ANSWER)
    return path


def _clients(catalog, recordings):
    return ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )


def _form_graph(seeded_db, recordings, tmp_path, clarification):
    db_path, catalog = seeded_db
    matcher = ModelClient("replay", catalog, recordings, task=MATCHING)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    return build_web_form_graph(matcher, db_path, checkpointer, clarification=clarification)


def _rows(db_path):
    conn = db.connect(db_path)
    try:
        orders = [dict(r) for r in conn.execute("SELECT id, customer_code, channel, status FROM orders")]
        lines = [dict(r) for r in conn.execute("SELECT order_id, sku, quantity FROM order_lines ORDER BY id")]
        pending = [dict(r) for r in conn.execute("SELECT thread_id, channel, round, status FROM clarifications")]
    finally:
        conn.close()
    return orders, lines, pending


RUN = {"configurable": {"thread_id": "thread-1"}}


def test_clear_submission_goes_through_clarify_like_phase_02(seeded_db, write_recording, tmp_path, no_network):
    db_path, catalog = seeded_db
    recordings = _record_form(catalog, write_recording)
    clients = _clients(catalog, recordings)
    state = _form_graph(seeded_db, recordings, tmp_path, clients).invoke({"submission": CLEAR_FORM}, RUN)
    assert "__interrupt__" not in state
    assert clients.question.calls == clients.answer.calls == 0
    with_clarify = _rows(db_path)

    phase_02_db = tmp_path / "phase02.db"
    conn = db.connect(phase_02_db)
    db.seed(conn)
    conn.close()
    plain = build_web_form_graph(ModelClient("replay", catalog, recordings, task=MATCHING), phase_02_db)
    expected = plain.invoke({"submission": CLEAR_FORM})
    assert state["reply"] == expected["reply"]
    assert with_clarify == _rows(phase_02_db)
    assert with_clarify[2] == []


def _email_run(seeded_db, write_recording, tmp_path, lines, clarification_recordings=None):
    db_path, catalog = seeded_db
    path = make_email(tmp_path / "EML-DOUBT-1.eml", SENDER, "Order", EMAIL_BODY)
    text = model_text(parse_email(path.read_bytes()))
    write_recording(catalog, text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
    recordings = write_recording(catalog, text, {"lines": lines}, task=EMAIL_EXTRACTION)
    intake = ModelClient("replay", catalog, recordings, task=EMAIL_INTAKE)
    extraction = ModelClient("replay", catalog, recordings, task=EMAIL_EXTRACTION)
    clients = _clients(catalog, clarification_recordings or recordings)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    graph = build_email_order_graph(intake, extraction, db_path, checkpointer, clarification=clients)
    conn = db.connect(tmp_path / "phase03.db")
    db.seed(conn)
    conn.close()
    plain = build_email_order_graph(intake, extraction, tmp_path / "phase03.db")
    return graph, plain, clients, {"email_path": str(path)}


def test_clear_email_goes_through_clarify_like_phase_03(seeded_db, write_recording, tmp_path, no_network):
    graph, plain, clients, graph_input = _email_run(seeded_db, write_recording, tmp_path, EMAIL_LINES[:2])
    state = graph.invoke(graph_input, RUN)
    assert "__interrupt__" not in state
    assert clients.question.calls == clients.answer.calls == 0
    expected = plain.invoke(graph_input)
    assert state["reply"] == expected["reply"]
    assert _rows(seeded_db[0]) == _rows(tmp_path / "phase03.db")


def test_order_with_doubts_pauses_with_question_and_no_order(seeded_db, write_recording, tmp_path, no_network):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    assert [(d["line_id"], d["types"]) for d in doubts] == [(2, ["ambiguous"]), (3, ["unknown"]), (4, ["quantity"])]
    recordings = _record_round(catalog, write_recording, doubts, QUESTION_1)
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    state = graph.invoke({"submission": DOUBT_FORM}, RUN)
    assert state["__interrupt__"][0].value["question"] == QUESTION_1
    snapshot = graph.get_state(RUN, subgraphs=True)
    assert snapshot.next == ("clarify",)
    assert snapshot.tasks[0].state.values["question"] == QUESTION_1
    assert snapshot.tasks[0].state.next == ("wait",)
    assert _rows(db_path) == (
        [],
        [],
        [{"thread_id": "thread-1", "channel": "web_form", "round": 1, "status": "pending"}],
    )


def test_question_missing_a_line_stops_before_the_pause(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    recordings = _record_round(catalog, write_recording, doubts, QUESTION_1.replace('"hospital bed"', "the bed"))
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    with pytest.raises(InvalidQuestion, match='line 3 text "hospital bed"'):
        graph.invoke({"submission": DOUBT_FORM}, RUN)
    assert not any(task.interrupts for task in graph.get_state(RUN).tasks)
    assert _rows(db_path) == ([], [], [])


def test_invalid_answer_stops_with_no_rows(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    bad = [RESOLUTIONS_1[0], {"line_id": 3, "action": "set", "sku": "BED-1", "quantity": 1}]
    recordings = _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, bad)
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    state = graph.invoke(Command(resume={"answer": ANSWER_1}), RUN)
    waiting = state["__interrupt__"][0].value
    assert "SKU 'BED-1' is not in the catalog; line 4 is not answered" in waiting["rejected"]
    assert (waiting["question"], waiting["round"]) == (QUESTION_1, 1)
    orders, lines, pending = _rows(db_path)
    assert (orders, lines, pending[0]["status"], pending[0]["round"]) == ([], [], "pending", 1)


@pytest.mark.parametrize(
    ("answer", "changed", "message"),
    [
        ("Size L for the gloves, 501 boxes.", {"quantity": 501}, "quantity is above the limit of 500"),
        ("Size L for the gloves, a huge amount.", {"quantity": 10**30}, "quantity is above the limit of 500"),
        ("Gel for the gloves line.", {"sku": "GEL-500"}, "SKU 'GEL-500' is not one of the offered candidates"),
    ],
)
def test_answer_above_the_ceiling_or_outside_the_candidates_waits_again(
    seeded_db, write_recording, tmp_path, answer, changed, message
):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    bad = [{**RESOLUTIONS_1[0], **changed}, *RESOLUTIONS_1[1:]]
    _record_round(catalog, write_recording, doubts, QUESTION_1, answer, bad)
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2)
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    waiting = graph.invoke(Command(resume={"answer": answer}), RUN)["__interrupt__"][0].value
    assert waiting["rejected"] == f"Clarification answer rejected: line 2: {message}"
    assert (waiting["question"], waiting["round"]) == (QUESTION_1, 1)
    orders, lines, pending = _rows(db_path)
    assert (orders, lines, pending[0]["status"], pending[0]["round"]) == ([], [], "pending", 1)
    second = graph.invoke(Command(resume={"answer": ANSWER_1}), RUN)
    assert second["__interrupt__"][0].value["question"] == QUESTION_2


def test_unreadable_answer_leaves_the_thread_answerable(seeded_db, write_recording, tmp_path, no_network):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    bad_answer = "Size L, 2.5 boxes."
    not_whole = [{**RESOLUTIONS_1[0], "quantity": 2.5}, *RESOLUTIONS_1[1:]]
    _record_round(catalog, write_recording, doubts, QUESTION_1, bad_answer, not_whole)
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2)
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    for answer in (bad_answer, "No recording exists for this text."):
        waiting = graph.invoke(Command(resume={"answer": answer}), RUN)["__interrupt__"][0].value
        assert waiting["rejected"].startswith("Clarification answer not read:")
        assert (waiting["question"], waiting["round"]) == (QUESTION_1, 1)
        assert _rows(db_path)[2][0]["round"] == 1
    # The new text is read, not the stale one.
    second = graph.invoke(Command(resume={"answer": ANSWER_1}), RUN)
    assert second["__interrupt__"][0].value["question"] == QUESTION_2


def test_next_question_failure_undoes_the_answer_and_keeps_the_thread_closable(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], "Please confirm the small gloves.")
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    waiting = graph.invoke(Command(resume={"answer": ANSWER_1}), RUN)["__interrupt__"][0].value
    assert "the next question could not be drafted" in waiting["rejected"]
    assert (waiting["question"], waiting["round"], waiting["doubts"]) == (QUESTION_1, 1, doubts)
    assert _rows(db_path)[2][0]["round"] == 1
    state = graph.invoke(Command(resume={"close": True}), RUN)
    assert "__interrupt__" not in state
    orders, lines, pending = _rows(db_path)
    assert lines == [{"order_id": 1, "sku": "GLV-NIT-M", "quantity": 40}]
    assert pending[0]["status"] == "closed"


def test_two_rounds_store_resolved_lines_and_list_the_rest(seeded_db, write_recording, tmp_path, no_network):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2, ANSWER_2, RESOLUTIONS_2)
    clients = _clients(catalog, recordings)
    graph = _form_graph(seeded_db, recordings, tmp_path, clients)
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    second = graph.invoke(Command(resume={"answer": ANSWER_1}), RUN)
    assert second["__interrupt__"][0].value["question"] == QUESTION_2
    assert _rows(db_path)[:2] == ([], [])
    assert _rows(db_path)[2][0]["round"] == 2
    state = graph.invoke(Command(resume={"answer": ANSWER_2}), RUN)
    assert "__interrupt__" not in state
    assert (clients.question.calls, clients.answer.calls) == (2, 2)
    orders, lines, pending = _rows(db_path)
    assert orders == [{"id": 1, "customer_code": "CLI-002", "channel": "web_form", "status": "received"}]
    assert lines == [
        {"order_id": 1, "sku": "GLV-NIT-M", "quantity": 40},
        {"order_id": 1, "sku": "GLV-NIT-L", "quantity": 10},
    ]
    assert pending == [{"thread_id": "thread-1", "channel": "web_form", "round": 2, "status": "answered"}]
    assert state["reply"] == TWO_ROUND_REPLY


def test_reply_after_an_answer_says_new_products_are_ignored():
    customer = {"contact_name": "Iker Zubiri"}
    note = "Products not in your original order are not added from your answer; please send them as a new order."
    answered = build_reply(customer, "web form order WF-1", None, [], [], answered=True)
    assert note in answered
    assert answered.isascii()
    assert note not in build_reply(customer, "web form order WF-1", None, [], [], closed=True)


def test_store_and_status_change_share_one_transaction(seeded_db, write_recording, tmp_path):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2, ANSWER_2, RESOLUTIONS_2)
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    graph.invoke(Command(resume={"answer": ANSWER_1}), RUN)
    conn = db.connect(db_path)
    conn.execute(
        "CREATE TRIGGER refuse BEFORE INSERT ON order_lines WHEN NEW.sku = 'GLV-NIT-L' BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="refused"):
        graph.invoke(Command(resume={"answer": ANSWER_2}), RUN)
    orders, lines, pending = _rows(db_path)
    assert (orders, lines, pending[0]["status"]) == ([], [], "pending")


def test_answered_thread_that_crashed_after_the_store_commit_resumes_to_one_order(
    seeded_db, write_recording, tmp_path, monkeypatch, no_network
):
    """C10, C6: resuming re-runs `store`; the stored source returns its order without touching the answered row."""
    from purchase_cycle import faults

    class Crash(Exception):
        pass

    def crash_at(point):
        if point == faults.AFTER_STORE_COMMIT:
            raise Crash(point)

    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2, ANSWER_2, RESOLUTIONS_2)
    graph = _form_graph(seeded_db, recordings, tmp_path, _clients(catalog, recordings))
    graph.invoke({"submission": DOUBT_FORM}, RUN, durability="sync")
    graph.invoke(Command(resume={"answer": ANSWER_1}), RUN, durability="sync")
    with monkeypatch.context() as patch:
        patch.setattr(faults, "crash_at", crash_at)
        with pytest.raises(Crash):
            graph.invoke(Command(resume={"answer": ANSWER_2}), RUN, durability="sync")
    assert len(_rows(db_path)[0]) == 1
    state = graph.invoke(None, RUN, durability="sync")
    orders, lines, pending = _rows(db_path)
    assert [order["id"] for order in orders] == [1]
    assert [(line["sku"], line["quantity"]) for line in lines] == [("GLV-NIT-M", 40), ("GLV-NIT-L", 10)]
    assert pending[0]["status"] == "answered"
    assert state["order_id"] == 1
    assert state["reply"] == TWO_ROUND_REPLY


def test_finishing_a_thread_that_is_not_pending_rolls_back(seeded_db):
    db_path, _ = seeded_db
    conn = db.connect(db_path)
    try:
        db.save_clarification(conn, "thread-1", "web_form", "CLI-002", "Which size?", 1)
        db.insert_order(conn, "CLI-002", "web_form", "received", [("GLV-NIT-M", 1)], ("thread-1", "answered"))
        with pytest.raises(db.NotPending, match="thread thread-1 is not pending"):
            db.insert_order(conn, "CLI-002", "web_form", "received", [("GLV-NIT-M", 2)], ("thread-1", "answered"))
        with pytest.raises(db.NotPending), conn:
            db.finish_clarification(conn, "thread-1", "closed")
    finally:
        conn.close()
    orders, lines, pending = _rows(db_path)
    assert (len(orders), lines[0]["quantity"], len(lines), pending[0]["status"]) == (1, 1, 1, "answered")


def test_email_with_a_doubt_pauses_then_stores_without_the_removed_line(
    seeded_db, write_recording, tmp_path, no_network
):
    db_path, catalog = seeded_db
    doubts = detect(EMAIL_LINES, catalog, "email")
    assert [(d["line_id"], d["types"]) for d in doubts] == [(3, ["unknown"])]
    resolutions = [{"line_id": 3, "action": "remove", "sku": None, "quantity": None}]
    recordings = _record_round(catalog, write_recording, doubts, EMAIL_QUESTION, EMAIL_ANSWER, resolutions)
    graph, _, _, graph_input = _email_run(seeded_db, write_recording, tmp_path, EMAIL_LINES, recordings)
    paused = graph.invoke(graph_input, RUN)
    assert paused["__interrupt__"][0].value["question"] == EMAIL_QUESTION
    assert _rows(db_path) == ([], [], [{"thread_id": "thread-1", "channel": "email", "round": 1, "status": "pending"}])
    state = graph.invoke(Command(resume={"answer": EMAIL_ANSWER}), RUN)
    orders, lines, pending = _rows(db_path)
    assert [line["sku"] for line in lines] == ["GLV-NIT-M", "ALC70-250"]
    assert pending[0]["status"] == "answered"
    assert 'As you asked, these lines are removed from the order:\n- "2 hospital beds" (2)' in state["reply"]
    assert "could not find" not in state["reply"]
