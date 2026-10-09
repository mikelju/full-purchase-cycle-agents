"""C7 and C8 (phase 05): an invalid model answer is re-asked once, and a thread that cannot go on is parked."""

import json

import anthropic
import pytest
from pydantic import ValidationError

from conftest import make_email
from purchase_cycle import db, llm
from purchase_cycle.clarification import ClarificationClients, detect, doubts_message
from purchase_cycle.email_order import build_email_order_graph, parse_email
from purchase_cycle.email_order import model_text as email_text
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
    InvalidModelOutput,
    ModelClient,
)
from purchase_cycle.recovery import NeedsReview
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph
from purchase_cycle.whatsapp_order import model_text as whatsapp_text
from test_clarification_graph import (
    CLEAR_FORM,
    DOUBT_FORM,
    EMAIL_BODY,
    EMAIL_LINES,
    QUESTION_1,
    RUN,
    SENDER,
    _form_lines,
    _record_form,
    _rows,
)
from test_retry import Flaky, authentication, connection, no_backoff, server, timeout  # noqa: F401 (autouse fixture)
from test_whatsapp_order import BODY, LINES, ORDER, message

INTAKE = {"is_order": True, "reason": "An order."}
CASE = "MSG-REASK-1"


def schema_error(task, answer) -> str:
    """The text of the InvalidModelOutput the client raises for this answer of case CASE."""
    try:
        task.answer.model_validate(answer)
    except ValidationError as error:
        return str(InvalidModelOutput(error, f"case {CASE}"))
    raise AssertionError("the answer is valid")


def reasked(text: str, error: str) -> str:
    return text + llm.CORRECTION.format(error=error)


class Channel:
    """One channel graph with recovery on, its recordings and its clients; `text` is the model text of the message."""

    def __init__(self, name, seeded_db, write_recording, tmp_path):
        self.name, self.tmp_path, self.write = name, tmp_path, write_recording
        self.db_path, self.catalog = seeded_db
        if name == "email":
            path = make_email(tmp_path / f"{CASE}.eml", SENDER, "Order", EMAIL_BODY)
            self.text, self.lines, self.source = email_text(parse_email(path.read_bytes())), EMAIL_LINES, "body"
            self.tasks = {"intake": EMAIL_INTAKE, "extract": EMAIL_EXTRACTION}
            self.input = {"email_path": str(path)}
        else:
            (tmp_path / "inbox").mkdir()
            path = tmp_path / "inbox" / f"{CASE}.json"
            path.write_text(json.dumps(message()), encoding="utf-8")
            self.text, self.lines, self.source = whatsapp_text({"body": BODY}), LINES, "message"
            self.tasks = {"intake": WHATSAPP_INTAKE, "extract": WHATSAPP_EXTRACTION}
            self.input = {"message_path": str(path)}
        self.path = str(path)

    def record(self, node, answer, text=None):
        return self.write(self.catalog, text or self.text, answer, case_id=CASE, task=self.tasks[node])

    def graph(self, flaky=None, errors=()):
        recordings = self.tmp_path / "recordings.jsonl"
        self.clients = {node: ModelClient("replay", self.catalog, recordings, task=t) for node, t in self.tasks.items()}
        if flaky:
            self.clients[flaky] = Flaky(self.clients[flaky], errors)
        checkpointer = sqlite_checkpointer(self.tmp_path / "checkpoints.db")
        intake, extract = self.clients["intake"], self.clients["extract"]
        if self.name == "email":
            return build_email_order_graph(intake, extract, self.db_path, checkpointer, recovery=True)
        return build_whatsapp_order_graph(
            intake, extract, self.db_path, self.tmp_path / "outbox", checkpointer, recovery=True
        )


def _bad_lines(channel, kind):
    """An invalid extraction answer and the error text the graph re-asks with."""
    first = dict(channel.lines[0])
    if kind == "schema":
        answer = {"lines": [{**first, "quantity": 0}]}
        return answer, schema_error(channel.tasks["extract"], answer)
    if kind == "unknown-sku":
        return {"lines": [{**first, "sku": "GLV-NIT-XXL"}]}, "Extracted line 1: SKU 'GLV-NIT-XXL' is not in the catalog"
    # A source outside the message: an attachment the email does not have, or anything but the WhatsApp message.
    source = "order.pdf" if channel.name == "email" else "body"
    where = "the body or an attachment" if channel.name == "email" else "the message"
    return {"lines": [{**first, "source": source}]}, f"Extracted line 1: source '{source}' is not {where}"


def _failures(db_path):
    conn = db.connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM failures ORDER BY thread_id")]
    finally:
        conn.close()


def _outbox(tmp_path):
    return sorted((tmp_path / "outbox").glob("*.json")) if (tmp_path / "outbox").exists() else []


def _parked_notice(name):
    """Only WhatsApp can receive a reply: a parked thread writes one keyed notice (coordinator decision 2026-10-09)."""
    return [f"parked-{message()['message_id']}.json"] if name == "whatsapp" else []


CHANNELS = ["email", "whatsapp"]
KINDS = ["schema", "unknown-sku", "bad-source"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", CHANNELS)
def test_invalid_then_valid_extraction_completes_the_order_with_two_calls(
    seeded_db, write_recording, tmp_path, no_network, name, kind
):
    channel = Channel(name, seeded_db, write_recording, tmp_path)
    bad, error = _bad_lines(channel, kind)
    channel.record("intake", INTAKE)
    channel.record("extract", bad)
    channel.record("extract", {"lines": channel.lines}, reasked(channel.text, error))
    state = channel.graph().invoke(channel.input, RUN)
    assert channel.clients["extract"].calls == 2
    assert [line["sku"] for line in state["lines"]] == [line["sku"] for line in channel.lines]
    orders, lines, _ = _rows(seeded_db[0])
    assert len(orders) == 1 and state["order_id"] == orders[0]["id"]
    assert [(line["sku"], line["quantity"]) for line in lines] == [
        (line["sku"], line["quantity"]) for line in channel.lines if line["sku"]
    ]
    assert _failures(seeded_db[0]) == []
    assert len(_outbox(tmp_path)) == (1 if name == "whatsapp" else 0)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", CHANNELS)
def test_two_invalid_extractions_park_the_thread_as_needs_review(
    seeded_db, write_recording, tmp_path, no_network, name, kind
):
    channel = Channel(name, seeded_db, write_recording, tmp_path)
    bad, error = _bad_lines(channel, kind)
    channel.record("intake", INTAKE)
    channel.record("extract", bad)
    channel.record("extract", bad, reasked(channel.text, error))
    graph = channel.graph()
    with pytest.raises(NeedsReview):
        graph.invoke(channel.input, RUN)
    assert channel.clients["extract"].calls == 2
    [row] = _failures(seeded_db[0])
    assert (row["thread_id"], row["channel"], row["source"], row["step"], row["status"]) == (
        "thread-1",
        name,
        channel.path,
        "extract",
        "needs_review",
    )
    assert row["error"].startswith("Second invalid model answer: ") and row["created_at"]
    assert _rows(seeded_db[0]) == ([], [], [])
    assert [p.name for p in _outbox(tmp_path)] == _parked_notice(name)
    # The thread stays resumable from its checkpoint, and parking it again keeps one row.
    assert graph.get_state(RUN).next == ("extract",)
    with pytest.raises(NeedsReview):
        graph.invoke(None, RUN)
    assert channel.clients["extract"].calls == 4
    assert [r["thread_id"] for r in _failures(seeded_db[0])] == ["thread-1"]
    assert [p.name for p in _outbox(tmp_path)] == _parked_notice(name)


@pytest.mark.parametrize("name", CHANNELS)
def test_invalid_intake_answer_is_re_asked_once(seeded_db, write_recording, tmp_path, no_network, name):
    channel = Channel(name, seeded_db, write_recording, tmp_path)
    bad = {"reason": "An order."}
    channel.record("intake", bad)
    channel.record("intake", ORDER, reasked(channel.text, schema_error(channel.tasks["intake"], bad)))
    channel.record("extract", {"lines": channel.lines})
    state = channel.graph().invoke(channel.input, RUN)
    assert channel.clients["intake"].calls == 2
    assert state["is_order"] and len(_rows(seeded_db[0])[0]) == 1


@pytest.mark.parametrize("name", CHANNELS)
def test_two_invalid_intake_answers_park_the_thread(seeded_db, write_recording, tmp_path, no_network, name):
    channel = Channel(name, seeded_db, write_recording, tmp_path)
    bad = {"reason": "An order."}
    channel.record("intake", bad)
    channel.record("intake", bad, reasked(channel.text, schema_error(channel.tasks["intake"], bad)))
    with pytest.raises(NeedsReview):
        channel.graph().invoke(channel.input, RUN)
    [row] = _failures(seeded_db[0])
    assert (row["step"], row["status"]) == ("intake", "needs_review")
    assert _rows(seeded_db[0]) == ([], [], [])


def _web_form(seeded_db, write_recording, tmp_path, answers, flaky=None):
    db_path, catalog = seeded_db
    bad = {"sku": "ALC70-250", "note": "extra"}
    error = schema_error(MATCHING, bad).replace(f"case {CASE}", "case WF-CLEAR-1-2")
    recordings = None
    for sentence, answer in zip(["alcohol 70 250ml", reasked("alcohol 70 250ml", error)], answers, strict=False):
        recordings = write_recording(catalog, sentence, bad if answer == "bad" else answer, task=MATCHING)
    client = ModelClient("replay", catalog, recordings, task=MATCHING)
    if flaky:
        client = Flaky(client, flaky)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    return build_web_form_graph(client, db_path, checkpointer, recovery=True), client


def test_web_form_invalid_then_valid_match_completes_the_order(seeded_db, write_recording, tmp_path, no_network):
    graph, client = _web_form(seeded_db, write_recording, tmp_path, ["bad", {"sku": "ALC70-250"}])
    state = graph.invoke({"submission": CLEAR_FORM}, RUN)
    assert client.calls == 2 and state["order_id"] == 1
    assert [line["sku"] for line in _rows(seeded_db[0])[1]] == ["GLV-NIT-M", "ALC70-250"]
    assert _failures(seeded_db[0]) == []


def test_web_form_two_invalid_matches_park_the_thread(seeded_db, write_recording, tmp_path, no_network):
    graph, client = _web_form(seeded_db, write_recording, tmp_path, ["bad", "bad"])
    with pytest.raises(NeedsReview):
        graph.invoke({"submission": CLEAR_FORM}, RUN)
    [row] = _failures(seeded_db[0])
    assert (row["channel"], row["source"], row["step"], row["status"]) == (
        "web_form",
        "WF-CLEAR-1",
        "match",
        "needs_review",
    )
    assert _rows(seeded_db[0]) == ([], [], [])
    assert graph.get_state(RUN).next == ("match",)


NODES = [("email", "intake"), ("email", "extract"), ("whatsapp", "intake"), ("whatsapp", "extract")]


@pytest.mark.parametrize(("name", "node"), NODES, ids=[f"{n}-{s}" for n, s in NODES])
def test_three_transient_errors_park_the_thread_in_failures(
    seeded_db, write_recording, tmp_path, no_network, name, node
):
    channel = Channel(name, seeded_db, write_recording, tmp_path)
    channel.record("intake", INTAKE)
    channel.record("extract", {"lines": channel.lines})
    graph = channel.graph(flaky=node, errors=[connection(), server(), timeout()])
    with pytest.raises(anthropic.APITimeoutError):
        graph.invoke(channel.input, RUN)
    assert channel.clients[node].attempts == 3
    [row] = _failures(seeded_db[0])
    assert (row["thread_id"], row["channel"], row["step"], row["status"]) == ("thread-1", name, node, "needs_review")
    assert row["error"].startswith("APITimeoutError: Request timed out")
    assert _rows(seeded_db[0]) == ([], [], [])
    assert [p.name for p in _outbox(tmp_path)] == _parked_notice(name)
    # Resumable: once the errors stop, the same thread finishes from its checkpoint.
    graph.invoke(None, RUN)
    assert len(_rows(seeded_db[0])[0]) == 1


def test_web_form_three_transient_errors_park_the_thread(seeded_db, write_recording, tmp_path, no_network):
    graph, client = _web_form(seeded_db, write_recording, tmp_path, [{"sku": "ALC70-250"}], [server()] * 3)
    with pytest.raises(anthropic.InternalServerError):
        graph.invoke({"submission": CLEAR_FORM}, RUN)
    [row] = _failures(seeded_db[0])
    assert (row["step"], row["error"]) == ("match", "InternalServerError: status 500")


def test_two_transient_errors_then_success_write_no_failure(seeded_db, write_recording, tmp_path, no_network):
    channel = Channel("whatsapp", seeded_db, write_recording, tmp_path)
    channel.record("intake", INTAKE)
    channel.record("extract", {"lines": channel.lines})
    channel.graph(flaky="extract", errors=[connection(), server()]).invoke(channel.input, RUN)
    assert _failures(seeded_db[0]) == [] and len(_rows(seeded_db[0])[0]) == 1


def test_authentication_error_is_not_parked(seeded_db, write_recording, tmp_path, no_network):
    channel = Channel("email", seeded_db, write_recording, tmp_path)
    channel.record("intake", INTAKE)
    with pytest.raises(anthropic.AuthenticationError):
        channel.graph(flaky="intake", errors=[authentication()]).invoke(channel.input, RUN)
    assert _failures(seeded_db[0]) == []


def test_without_recovery_the_phase_03_graph_raises_the_first_invalid_answer(
    seeded_db, write_recording, tmp_path, no_network
):
    channel = Channel("email", seeded_db, write_recording, tmp_path)
    bad, _ = _bad_lines(channel, "unknown-sku")
    channel.record("intake", INTAKE)
    recordings = channel.record("extract", bad)
    clients = [ModelClient("replay", channel.catalog, recordings, task=t) for t in (EMAIL_INTAKE, EMAIL_EXTRACTION)]
    graph = build_email_order_graph(*clients, channel.db_path)
    with pytest.raises(llm.InvalidExtraction):
        graph.invoke(channel.input)
    assert clients[1].calls == 1 and _failures(seeded_db[0]) == []


def _first_question(seeded_db, write_recording, tmp_path, drafts):
    """The DOUBT_FORM web form with recovery on; `drafts` are the first and re-asked question drafts."""
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    text = doubts_message(doubts)
    try:
        CLARIFICATION_QUESTION.answer.model_validate(drafts[0])
    except ValidationError as error:
        failure = str(InvalidModelOutput(error, "case thread-1-question-1"))
    for sentence, draft in zip([text, reasked(text, failure)], drafts, strict=True):
        recordings = write_recording(catalog, sentence, draft, task=CLARIFICATION_QUESTION)
    clients = ClarificationClients(
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION),
        ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER),
    )
    matcher = ModelClient("replay", catalog, recordings, task=MATCHING)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    graph = build_web_form_graph(matcher, db_path, checkpointer, clarification=clients, recovery=True)
    return graph, clients


def test_invalid_first_question_draft_is_re_asked_once(seeded_db, write_recording, tmp_path, no_network):
    graph, clients = _first_question(seeded_db, write_recording, tmp_path, [{}, {"question": QUESTION_1}])
    state = graph.invoke({"submission": DOUBT_FORM}, RUN)
    assert state["__interrupt__"][0].value["question"] == QUESTION_1
    assert clients.question.calls == 2 and _failures(seeded_db[0]) == []


def test_two_invalid_first_question_drafts_park_the_thread(seeded_db, write_recording, tmp_path, no_network):
    graph, clients = _first_question(seeded_db, write_recording, tmp_path, [{}, {}])
    with pytest.raises(NeedsReview):
        graph.invoke({"submission": DOUBT_FORM}, RUN)
    [row] = _failures(seeded_db[0])
    assert (row["channel"], row["source"], row["step"]) == ("web_form", DOUBT_FORM["submission_id"], "ask")
    assert _rows(seeded_db[0]) == ([], [], [])
    assert graph.get_state(RUN).next == ("clarify",)


def test_failures_rows_park_idempotently_list_with_age_and_resolve_once(seeded_db):
    conn = db.connect(seeded_db[0])
    try:
        assert db.list_failures(conn) == []
        db.park_failure(conn, "t-1", "email", "a.eml", "extract", "first")
        db.park_failure(conn, "t-1", "email", "a.eml", "extract", "second")
        [row] = db.list_failures(conn)
        assert (row["thread_id"], row["error"], row["status"], row["age_minutes"]) == (
            "t-1",
            "second",
            "needs_review",
            0,
        )
        db.resolve_failure(conn, "t-1")
        assert db.list_failures(conn) == [] and db.get_failure(conn, "t-1")["status"] == "resolved"
        with pytest.raises(db.NotPending):
            db.resolve_failure(conn, "t-1")
        with pytest.raises(db.NotPending):
            db.resolve_failure(conn, "t-unknown")
    finally:
        conn.close()
