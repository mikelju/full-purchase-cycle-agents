"""C4 and C5 (phase 01) and C4 (phase 02): schema validation of model answers and replay without network."""

import pytest

from conftest import SENTENCE
from purchase_cycle import db
from purchase_cycle.graph import build_graph
from purchase_cycle.llm import EXTRACTION, MATCHING, InvalidModelOutput, MissingRecording, ModelClient, recording_key
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
