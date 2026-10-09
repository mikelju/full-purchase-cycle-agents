"""C7 (phase 05): model-calling nodes retry transient API errors and stop on the rest."""

import json

import anthropic
import httpx
import pytest
from langgraph.types import Command

from conftest import SENTENCE, make_email
from purchase_cycle import llm
from purchase_cycle.clarification import ClarificationClients, detect
from purchase_cycle.email_order import build_email_order_graph, parse_email
from purchase_cycle.email_order import model_text as email_text
from purchase_cycle.graph import build_graph, sqlite_checkpointer
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    EXTRACTION,
    MATCHING,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
    ModelClient,
)
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph
from purchase_cycle.whatsapp_order import model_text as whatsapp_text
from test_clarification_graph import (
    ANSWER_1,
    CLEAR_FORM,
    DOUBT_FORM,
    EMAIL_BODY,
    EMAIL_LINES,
    QUESTION_1,
    QUESTION_2,
    RESOLUTIONS_1,
    RUN,
    SENDER,
    _form_lines,
    _record_form,
    _record_round,
    _rows,
)
from test_whatsapp_order import BODY, LINES, ORDER, message

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(cls, code):
    return cls(f"status {code}", response=httpx.Response(code, request=REQUEST), body=None)


def connection():
    return anthropic.APIConnectionError(request=REQUEST)


def timeout():
    return anthropic.APITimeoutError(request=REQUEST)


def rate_limit():
    return _status(anthropic.RateLimitError, 429)


def server():
    return _status(anthropic.InternalServerError, 500)


def overloaded():
    return _status(anthropic.APIStatusError, 529)


def authentication():
    return _status(anthropic.AuthenticationError, 401)


def bad_request():
    return _status(anthropic.BadRequestError, 400)


class Flaky:
    """Fault-injecting test double: after `skip` good calls it raises the given errors, then answers again."""

    def __init__(self, client, errors, skip=0):
        self.client, self.errors, self.skip, self.attempts = client, list(errors), skip, 0

    def extract(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts > self.skip and self.errors:
            raise self.errors.pop(0)
        return self.client.extract(*args, **kwargs)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    zero = llm.MODEL_RETRY._replace(initial_interval=0, max_interval=0, jitter=False)
    monkeypatch.setattr(llm, "MODEL_RETRY", zero)


def test_retry_policy_accepts_only_transient_api_errors():
    assert llm.MODEL_RETRY.max_attempts == 3
    for error in (connection(), timeout(), rate_limit(), server(), overloaded()):
        assert llm.MODEL_RETRY.retry_on(error), error
    for error in (authentication(), bad_request(), _status(anthropic.NotFoundError, 404), ValueError("x")):
        assert not llm.MODEL_RETRY.retry_on(error), error


def test_model_client_leaves_retries_to_the_graph(monkeypatch, seeded_db):
    import langchain_anthropic

    built = {}

    class FakeChat:
        def __init__(self, **kwargs):
            built.update(kwargs)

        def bind_tools(self, *args, **kwargs):
            return self

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", FakeChat)
    ModelClient("live", seeded_db[1])._model()
    assert built["max_retries"] == 0


def _phase_01(seeded_db, write_recording, tmp_path, node, errors):
    db_path, catalog = seeded_db
    recordings = write_recording(catalog, SENTENCE, {"sku": "GLV-NIT-M", "quantity": 40})
    flaky = Flaky(ModelClient("replay", catalog, recordings, task=EXTRACTION), errors)
    graph = build_graph(flaky, db_path, sqlite_checkpointer(tmp_path / "checkpoints.db"))
    return graph, {"sentence": SENTENCE, "case_id": "TEST-1"}, flaky


def _web_form(seeded_db, write_recording, tmp_path, node, errors):
    db_path, catalog = seeded_db
    recordings = _record_form(catalog, write_recording)
    flaky = Flaky(ModelClient("replay", catalog, recordings, task=MATCHING), errors)
    graph = build_web_form_graph(flaky, db_path, sqlite_checkpointer(tmp_path / "checkpoints.db"))
    return graph, {"submission": CLEAR_FORM}, flaky


def _email(seeded_db, write_recording, tmp_path, node, errors):
    db_path, catalog = seeded_db
    path = make_email(tmp_path / "EML-RETRY-1.eml", SENDER, "Order", EMAIL_BODY)
    text = email_text(parse_email(path.read_bytes()))
    write_recording(catalog, text, {"is_order": True, "reason": "An order."}, task=EMAIL_INTAKE)
    recordings = write_recording(catalog, text, {"lines": EMAIL_LINES}, task=EMAIL_EXTRACTION)
    clients = {
        "intake": ModelClient("replay", catalog, recordings, task=EMAIL_INTAKE),
        "extract": ModelClient("replay", catalog, recordings, task=EMAIL_EXTRACTION),
    }
    flaky = clients[node] = Flaky(clients[node], errors)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    graph = build_email_order_graph(clients["intake"], clients["extract"], db_path, checkpointer)
    return graph, {"email_path": str(path)}, flaky


def _whatsapp(seeded_db, write_recording, tmp_path, node, errors):
    db_path, catalog = seeded_db
    (tmp_path / "inbox").mkdir()
    path = tmp_path / "inbox" / "WA-RETRY-1.json"
    path.write_text(json.dumps(message()), encoding="utf-8")
    text = whatsapp_text({"body": BODY})
    write_recording(catalog, text, ORDER, task=WHATSAPP_INTAKE)
    recordings = write_recording(catalog, text, {"lines": LINES}, task=WHATSAPP_EXTRACTION)
    clients = {
        "intake": ModelClient("replay", catalog, recordings, task=WHATSAPP_INTAKE),
        "extract": ModelClient("replay", catalog, recordings, task=WHATSAPP_EXTRACTION),
    }
    flaky = clients[node] = Flaky(clients[node], errors)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    graph = build_whatsapp_order_graph(
        clients["intake"], clients["extract"], db_path, tmp_path / "outbox", checkpointer
    )
    return graph, {"message_path": str(path)}, flaky


NODES = [
    (_phase_01, "extract"),
    (_web_form, "match"),
    (_email, "intake"),
    (_email, "extract"),
    (_whatsapp, "intake"),
    (_whatsapp, "extract"),
]
NODE_IDS = [
    "phase_01-extract",
    "web_form-match",
    "email-intake",
    "email-extract",
    "whatsapp-intake",
    "whatsapp-extract",
]


def _done(state, db_path, tmp_path) -> bool:
    """The run reached its end: the phase 01 line is matched, or one order is stored (and the WhatsApp reply sent)."""
    if "sentence" in state:
        return state["matched"]
    orders = _rows(db_path)[0]
    outbox = list((tmp_path / "outbox").glob("reply-*.json"))
    return len(orders) == 1 and ("message_path" not in state or len(outbox) == 1)


@pytest.mark.parametrize(("setup", "node"), NODES, ids=NODE_IDS)
def test_two_transient_errors_then_success_completes_the_run(
    seeded_db, write_recording, tmp_path, no_network, setup, node
):
    graph, graph_input, flaky = setup(seeded_db, write_recording, tmp_path, node, [connection(), rate_limit()])
    state = graph.invoke(graph_input, RUN)
    assert flaky.attempts == 3
    assert _done(state, seeded_db[0], tmp_path)


@pytest.mark.parametrize(("setup", "node"), NODES, ids=NODE_IDS)
def test_three_transient_errors_park_the_thread_with_nothing_stored(
    seeded_db, write_recording, tmp_path, no_network, setup, node
):
    errors = [server(), timeout(), overloaded()]
    graph, graph_input, flaky = setup(seeded_db, write_recording, tmp_path, node, errors)
    with pytest.raises(anthropic.APIStatusError):
        graph.invoke(graph_input, RUN)
    assert flaky.attempts == 3
    assert _rows(seeded_db[0]) == ([], [], [])
    # Only WhatsApp can receive a reply: its parked thread writes one notice (coordinator decision 2026-10-09).
    notices = [f"parked-{message()['message_id']}.json"] if "message_path" in graph_input else []
    assert [p.name for p in (tmp_path / "outbox").glob("*.json")] == notices
    assert graph.get_state(RUN).next == (node,)
    # The checkpoint stays resumable: once the errors stop, the same thread finishes.
    state = graph.invoke(None, RUN)
    assert flaky.attempts == 4
    assert _done({**graph_input, **state}, seeded_db[0], tmp_path)


@pytest.mark.parametrize(("setup", "node"), NODES, ids=NODE_IDS)
@pytest.mark.parametrize("error", [authentication, bad_request])
def test_authentication_and_bad_request_errors_are_attempted_once(
    seeded_db, write_recording, tmp_path, no_network, setup, node, error
):
    graph, graph_input, flaky = setup(seeded_db, write_recording, tmp_path, node, [error()])
    with pytest.raises(type(error())):
        graph.invoke(graph_input, RUN)
    assert flaky.attempts == 1
    assert _rows(seeded_db[0]) == ([], [], [])


def _clarify(seeded_db, write_recording, tmp_path, question_errors=(), answer_errors=(), skip=0):
    db_path, catalog = seeded_db
    _record_form(catalog, write_recording)
    doubts = detect(_form_lines(catalog), catalog, "web_form")
    _record_round(catalog, write_recording, doubts, QUESTION_1, ANSWER_1, RESOLUTIONS_1)
    recordings = _record_round(catalog, write_recording, doubts[2:], QUESTION_2)
    clients = ClarificationClients(
        Flaky(ModelClient("replay", catalog, recordings, task=CLARIFICATION_QUESTION), question_errors, skip),
        Flaky(ModelClient("replay", catalog, recordings, task=CLARIFICATION_ANSWER), answer_errors),
    )
    matcher = ModelClient("replay", catalog, recordings, task=MATCHING)
    checkpointer = sqlite_checkpointer(tmp_path / "checkpoints.db")
    return build_web_form_graph(matcher, db_path, checkpointer, clarification=clients), clients


def _question(state):
    return state["__interrupt__"][0].value


def test_first_question_is_retried_then_success(seeded_db, write_recording, tmp_path, no_network):
    graph, clients = _clarify(seeded_db, write_recording, tmp_path, [connection(), server()])
    assert _question(graph.invoke({"submission": DOUBT_FORM}, RUN))["question"] == QUESTION_1
    assert clients.question.attempts == 3


def test_first_question_parks_after_three_transient_errors(seeded_db, write_recording, tmp_path, no_network):
    graph, clients = _clarify(seeded_db, write_recording, tmp_path, [connection(), server(), rate_limit()])
    with pytest.raises(anthropic.RateLimitError):
        graph.invoke({"submission": DOUBT_FORM}, RUN)
    assert clients.question.attempts == 3
    assert _rows(seeded_db[0]) == ([], [], [])
    assert graph.get_state(RUN).next == ("clarify",)
    assert _question(graph.invoke(None, RUN))["question"] == QUESTION_1


def test_answer_reading_is_retried_then_success(seeded_db, write_recording, tmp_path, no_network):
    graph, clients = _clarify(seeded_db, write_recording, tmp_path, answer_errors=[timeout(), overloaded()])
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    assert _question(graph.invoke(Command(resume={"answer": ANSWER_1}), RUN))["question"] == QUESTION_2
    assert clients.answer.attempts == 3


@pytest.mark.parametrize(
    ("errors", "attempts"),
    [([connection(), server(), rate_limit()], 3), ([authentication()], 1)],
    ids=["transient", "auth"],
)
def test_answer_reading_keeps_the_phase_04_fallback_on_the_last_attempt(
    seeded_db, write_recording, tmp_path, no_network, errors, attempts
):
    graph, clients = _clarify(seeded_db, write_recording, tmp_path, answer_errors=errors)
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    waiting = _question(graph.invoke(Command(resume={"answer": ANSWER_1}), RUN))
    assert clients.answer.attempts == attempts
    assert waiting["rejected"].startswith("Clarification answer not read:")
    assert (waiting["question"], waiting["round"]) == (QUESTION_1, 1)
    # Still answerable: the next delivery of the same answer is read.
    assert _question(graph.invoke(Command(resume={"answer": ANSWER_1}), RUN))["question"] == QUESTION_2


@pytest.mark.parametrize(
    ("errors", "attempts", "question"),
    [
        ([connection(), server()], 3, QUESTION_2),
        ([connection(), server(), rate_limit()], 3, QUESTION_1),
        ([authentication()], 1, QUESTION_1),
    ],
    ids=["retried", "transient-fallback", "auth-fallback"],
)
def test_next_question_is_retried_and_keeps_the_phase_04_fallback(
    seeded_db, write_recording, tmp_path, no_network, errors, attempts, question
):
    graph, clients = _clarify(seeded_db, write_recording, tmp_path, question_errors=errors, skip=1)
    graph.invoke({"submission": DOUBT_FORM}, RUN)
    waiting = _question(graph.invoke(Command(resume={"answer": ANSWER_1}), RUN))
    assert clients.question.attempts == 1 + attempts
    assert waiting["question"] == question
    if question == QUESTION_1:
        assert "the next question could not be drafted" in waiting["rejected"]
