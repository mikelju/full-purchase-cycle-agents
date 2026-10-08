"""C4 and C5 (phase 01) and C4 (phase 02): schema validation of model answers and replay without network."""

import pytest

from conftest import SENTENCE
from purchase_cycle import db
from purchase_cycle.graph import build_graph
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    EXTRACTION,
    MATCHING,
    InvalidModelOutput,
    MissingRecording,
    ModelClient,
    build_system_prompt,
    recording_key,
    tool_definition,
)
from purchase_cycle.web_form import build_web_form_graph


def test_valid_recorded_answer_is_parsed(seeded_db, write_recording, no_network):
    db_path, catalog = seeded_db
    path = write_recording(catalog, SENTENCE, {"sku": "GLV-NIT-M", "quantity": 40})
    line = ModelClient("replay", catalog, path).extract(SENTENCE)
    assert (line.sku, line.quantity) == ("GLV-NIT-M", 40)
    assert no_network == []


@pytest.mark.parametrize(
    ("answer", "field"),
    [
        ({"sku": "GLV-NIT-M", "quantity": -3}, "quantity"),
        ({"sku": "GLV-NIT-M", "quantity": "forty"}, "quantity"),
        ({"quantity": 40}, "sku"),
        ({"sku": "GLV-NIT-M", "quantity": 40, "note": "x"}, "note"),
    ],
)
def test_invalid_answer_is_rejected_before_any_write(seeded_db, write_recording, answer, field):
    db_path, catalog = seeded_db
    path = write_recording(catalog, SENTENCE, answer)
    graph = build_graph(ModelClient("replay", catalog, path), db_path)
    with pytest.raises(InvalidModelOutput) as raised:
        graph.invoke({"sentence": SENTENCE, "case_id": "TEST-1"})
    assert f"field '{field}'" in str(raised.value)
    assert field in raised.value.fields
    conn = db.connect(db_path)
    counts = db.row_counts(conn)
    assert (counts["orders"], counts["order_lines"]) == (0, 0)


@pytest.mark.parametrize(
    ("answer", "field"),
    [({"sku": 7}, "sku"), ({}, "sku"), ({"sku": "ALC70-250", "quantity": 3}, "quantity")],
)
def test_invalid_matching_answer_is_rejected_before_any_write(seeded_db, write_recording, answer, field):
    # The web form subgraph writes orders, so this is where "nothing is written" has teeth.
    db_path, catalog = seeded_db
    path = write_recording(catalog, "alcohol 70 250ml", answer, task=MATCHING)
    graph = build_web_form_graph(ModelClient("replay", catalog, path, task=MATCHING), db_path)
    submission = {
        "submission_id": "WF-TEST",
        "customer_code": "CLI-001",
        "lines": [{"product": "GLV-NIT-M", "quantity": 2}, {"product": "alcohol 70 250ml", "quantity": 1}],
    }
    with pytest.raises(InvalidModelOutput) as raised:
        graph.invoke({"submission": submission})
    assert field in raised.value.fields
    conn = db.connect(db_path)
    counts = db.row_counts(conn)
    assert (counts["orders"], counts["order_lines"]) == (0, 0)


def test_tasks_keep_separate_prompts_and_recordings(seeded_db):
    _, catalog = seeded_db
    extraction = ModelClient("live", catalog)
    matching = ModelClient("live", catalog, task=MATCHING)
    assert extraction.recordings_path.name == "order_line_extraction.jsonl"
    assert matching.recordings_path.name == "web_form_matching.jsonl"
    assert recording_key(extraction.system_prompt, "x", EXTRACTION) != recording_key(
        matching.system_prompt, "x", MATCHING
    )
    assert len(matching.system_prompt) / 4 > 4096


def test_replay_without_recording_fails_offline(seeded_db, tmp_path, no_network):
    _, catalog = seeded_db
    client = ModelClient("replay", catalog, tmp_path / "empty.jsonl")
    with pytest.raises(MissingRecording) as raised:
        client.extract("Two boxes of FFP2 masks", case_id="OLX-9999")
    message = str(raised.value)
    assert "OLX-9999" in message
    assert "--mode record" in message
    assert no_network == []


def test_prompt_carries_cache_breakpoint_and_full_catalog(seeded_db):
    _, catalog = seeded_db
    client = ModelClient("live", catalog)
    assert all(row["sku"] in client.system_prompt for row in catalog)
    # About 4 characters per token: the cached prefix must exceed Haiku 4.5's 4,096-token minimum.
    assert len(client.system_prompt) / 4 > 4096


EMAIL_TEXT = (
    "Subject: Order\n\nBody:\nPlease send 40 boxes of nitrile gloves M\n\nAttachment: extra.txt\n12 x alcohol 70 250 ml"
)


def test_email_intake_answer_is_parsed_in_replay(seeded_db, write_recording, no_network):
    _, catalog = seeded_db
    path = write_recording(catalog, EMAIL_TEXT, {"is_order": True, "reason": "Asks for gloves."}, task=EMAIL_INTAKE)
    decision = ModelClient("replay", catalog, path, task=EMAIL_INTAKE).extract(EMAIL_TEXT, case_id="EML-1")
    assert (decision.is_order, decision.reason) == (True, "Asks for gloves.")
    assert no_network == []


def test_email_extraction_answer_is_parsed_in_replay(seeded_db, write_recording, no_network):
    _, catalog = seeded_db
    lines = [
        {"source": "body", "source_text": "40 boxes of nitrile gloves M", "sku": "GLV-NIT-M", "quantity": 40},
        {"source": "extra.txt", "source_text": "12 x alcohol 70 250 ml", "sku": None, "quantity": 12},
    ]
    path = write_recording(catalog, EMAIL_TEXT, {"lines": lines}, task=EMAIL_EXTRACTION)
    answer = ModelClient("replay", catalog, path, task=EMAIL_EXTRACTION).extract(EMAIL_TEXT, case_id="EML-1")
    assert [line.model_dump() for line in answer.lines] == lines
    assert no_network == []


@pytest.mark.parametrize(
    ("task", "answer", "field"),
    [
        (EMAIL_INTAKE, {"is_order": "yes", "reason": "x"}, "is_order"),
        (EMAIL_INTAKE, {"is_order": True}, "reason"),
        (EMAIL_INTAKE, {"is_order": False, "reason": ""}, "reason"),
        (EMAIL_EXTRACTION, {"lines": [{"source": "body", "source_text": "x", "sku": None}]}, "lines.0.quantity"),
        (
            EMAIL_EXTRACTION,
            {"lines": [{"source": "body", "source_text": "x", "sku": None, "quantity": 0}]},
            "lines.0.quantity",
        ),
        (
            EMAIL_EXTRACTION,
            {"lines": [{"source": "body", "source_text": "x", "sku": None, "quantity": 2.0}]},
            "lines.0.quantity",
        ),
        (EMAIL_EXTRACTION, {"lines": "none"}, "lines"),
        (EMAIL_EXTRACTION, {"lines": [], "note": "x"}, "note"),
    ],
)
def test_invalid_email_answers_are_rejected_by_schema(seeded_db, write_recording, task, answer, field):
    _, catalog = seeded_db
    path = write_recording(catalog, EMAIL_TEXT, answer, task=task)
    with pytest.raises(InvalidModelOutput) as raised:
        ModelClient("replay", catalog, path, task=task).extract(EMAIL_TEXT, case_id="EML-1")
    assert field in raised.value.fields


def test_email_tasks_have_own_recordings_and_cached_catalog_prompt(seeded_db):
    _, catalog = seeded_db
    intake = ModelClient("live", catalog, task=EMAIL_INTAKE)
    extraction = ModelClient("live", catalog, task=EMAIL_EXTRACTION)
    assert intake.recordings_path.name == "email_intake.jsonl"
    assert extraction.recordings_path.name == "email_order_extraction.jsonl"
    keys = {
        recording_key(build_system_prompt(catalog, t), "x", t)
        for t in (EXTRACTION, MATCHING, EMAIL_INTAKE, EMAIL_EXTRACTION)
    }
    assert len(keys) == 4
    for client in (intake, extraction):
        assert all(row["sku"] in client.system_prompt for row in catalog)
        assert len(client.system_prompt) / 4 > 4096
    assert tool_definition(EMAIL_EXTRACTION)["name"] == "record_email_order_lines"
    assert tool_definition(EMAIL_INTAKE)["name"] == "record_email_intake"
    assert (EXTRACTION.max_tokens, MATCHING.max_tokens, EMAIL_INTAKE.max_tokens) == (256, 256, 256)


DOUBTS_TEXT = 'Doubtful lines:\nLine 1 | doubt: ambiguous product | quantity read: 10 | text: "nitrile gloves"'


def test_clarification_question_is_parsed_in_replay(seeded_db, write_recording, no_network):
    _, catalog = seeded_db
    path = write_recording(catalog, DOUBTS_TEXT, {"question": "Which size?"}, task=CLARIFICATION_QUESTION)
    client = ModelClient("replay", catalog, path, task=CLARIFICATION_QUESTION)
    assert client.extract(DOUBTS_TEXT, case_id="CLQ-1").question == "Which size?"
    assert no_network == []


def test_clarification_answer_is_parsed_in_replay(seeded_db, write_recording, no_network):
    _, catalog = seeded_db
    resolutions = [
        {"line_id": 1, "action": "set", "sku": "GLV-NIT-M", "quantity": 10},
        {"line_id": 2, "action": "remove", "sku": None, "quantity": None},
        {"line_id": 3, "action": "unclear", "sku": None, "quantity": None},
    ]
    path = write_recording(catalog, DOUBTS_TEXT, {"resolutions": resolutions}, task=CLARIFICATION_ANSWER)
    answer = ModelClient("replay", catalog, path, task=CLARIFICATION_ANSWER).extract(DOUBTS_TEXT, case_id="CLA-1")
    assert [r.model_dump() for r in answer.resolutions] == resolutions
    assert no_network == []


@pytest.mark.parametrize(
    ("task", "answer", "field"),
    [
        (CLARIFICATION_QUESTION, {"question": ""}, "question"),
        (CLARIFICATION_QUESTION, {}, "question"),
        (CLARIFICATION_QUESTION, {"question": "x", "note": "y"}, "note"),
        (CLARIFICATION_ANSWER, {"resolutions": "none"}, "resolutions"),
        (
            CLARIFICATION_ANSWER,
            {"resolutions": [{"line_id": 1, "action": "keep", "sku": None, "quantity": None}]},
            "resolutions.0.action",
        ),
        (
            CLARIFICATION_ANSWER,
            {"resolutions": [{"line_id": 1, "action": "set", "sku": "GLV-NIT-M", "quantity": 2.5}]},
            "resolutions.0.quantity",
        ),
        (
            CLARIFICATION_ANSWER,
            {"resolutions": [{"line_id": "1", "action": "remove", "sku": None, "quantity": None}]},
            "resolutions.0.line_id",
        ),
        (CLARIFICATION_ANSWER, {"resolutions": [{"line_id": 1, "action": "remove"}]}, "resolutions.0.sku"),
    ],
)
def test_invalid_clarification_answers_are_rejected_by_schema(seeded_db, write_recording, task, answer, field):
    _, catalog = seeded_db
    path = write_recording(catalog, DOUBTS_TEXT, answer, task=task)
    with pytest.raises(InvalidModelOutput) as raised:
        ModelClient("replay", catalog, path, task=task).extract(DOUBTS_TEXT, case_id="CL-1")
    assert field in raised.value.fields


def test_clarification_tasks_have_own_tools_recordings_and_keys(seeded_db):
    _, catalog = seeded_db
    tasks = (EXTRACTION, MATCHING, EMAIL_INTAKE, EMAIL_EXTRACTION, CLARIFICATION_QUESTION, CLARIFICATION_ANSWER)
    assert len({recording_key(build_system_prompt(catalog, t), "x", t) for t in tasks}) == 6
    assert (
        ModelClient("live", catalog, task=CLARIFICATION_QUESTION).recordings_path.name == "clarification_question.jsonl"
    )
    assert ModelClient("live", catalog, task=CLARIFICATION_ANSWER).recordings_path.name == "clarification_answers.jsonl"
    assert tool_definition(CLARIFICATION_QUESTION)["name"] == "record_clarification_question"
    assert tool_definition(CLARIFICATION_ANSWER)["name"] == "record_clarification_resolutions"
