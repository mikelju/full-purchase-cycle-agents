"""C8: response facts, the response_drafting task, the fact check and the template fallback."""

import json

import pytest

from purchase_cycle import customer_responses as cr
from purchase_cycle import db, llm
from test_sales import A, B, C, _incidents, _planned

SCENARIOS = {
    "credit_note": ((A, "missing", 2), (B, "damaged", 1)),
    "corrective": ((A, "extra", 3), (C, "extra", 2)),
    "both": ((A, "damaged", 2), (C, "extra", 1)),
}


def _scenario(tmp_path, name="both"):
    conn, _ = _planned(tmp_path, _incidents(*SCENARIOS[name]))
    return conn, cr.response_facts(conn, 1)


def _client(tmp_path, facts_list, bodies):
    path = tmp_path / "response_recordings.jsonl"
    with path.open("w") as fh:
        for facts, body in zip(facts_list, bodies, strict=True):
            key = llm.recording_key(
                llm.build_system_prompt([], llm.RESPONSE_DRAFTING), cr.facts_prompt(facts), llm.RESPONSE_DRAFTING
            )
            answer = body if isinstance(body, dict) else {"body": body}
            fh.write(json.dumps({"key": key, "case_id": "t", "sentence": "", "answer": answer}) + "\n")
    return llm.ModelClient("replay", [], path, task=llm.RESPONSE_DRAFTING)


def test_facts_hold_the_customer_references_incidents_and_documents(tmp_path):
    conn, facts = _scenario(tmp_path)
    assert (facts["invoice"], facts["delivery_note"], facts["customer"]) == (
        "SINV-0001",
        "CDN-0001",
        "Clinica Dental Arenal",
    )
    assert facts["recipient"] == conn.execute("SELECT email FROM customers").fetchone()[0]
    assert [(r["sku"], r["type"], r["units"]) for r in facts["incidents"]] == [(A, "damaged", 2), (C, "extra", 1)]
    assert all(r["product"] for r in facts["incidents"])
    assert [(d["number"], d["kind"]) for d in facts["documents"]] == [
        ("CN-0001", "credit_note"),
        ("CI-0002", "corrective_invoice"),
    ]
    assert facts["documents"][0]["total"] < 0 < facts["documents"][1]["total"]
    assert len(facts["documents"][0]["lines"]) == 1
    assert facts["net_total"] == conn.execute("SELECT net_total FROM customer_invoices").fetchone()[0]


def test_facts_of_an_invoice_without_discrepancies_are_none(tmp_path):
    conn, _ = _planned(tmp_path, _incidents(), lines=((A, 5),))
    assert cr.response_facts(conn, 1) is None
    assert cr.response_facts(conn, 99) is None


def test_the_prompt_holds_the_facts_json_and_no_recipient_or_subject(tmp_path):
    _, facts = _scenario(tmp_path)
    prompt = cr.facts_prompt(facts)
    loaded = json.loads(prompt)
    assert list(loaded) == sorted(loaded)
    assert (loaded["invoice"], loaded["delivery_note"]) == ("SINV-0001", "CDN-0001")
    assert loaded["documents"][0]["total"] == f"{facts['documents'][0]['total']:.2f}"
    assert facts["recipient"] not in prompt
    assert "subject" not in loaded
    assert cr.facts_prompt(facts) == prompt


def test_the_task_is_separate_and_existing_keys_are_unchanged():
    assert llm.RESPONSE_DRAFTING.name == "response_drafting"
    assert llm.RESPONSE_DRAFTING.recordings_path.name == "response_drafting.jsonl"
    assert llm.RESPONSE_DRAFTING.tool_name == "record_response_body"
    assert llm.RESPONSE_DRAFTING.answer.model_config["extra"] == "forbid"
    assert llm.recording_key("s", "x", llm.RESPONSE_DRAFTING) != llm.recording_key("s", "x", llm.CLAIM_DRAFTING)
    assert llm.CLAIM_DRAFTING.name == "claim_drafting"


def test_one_call_per_response_and_recipient_and_subject_come_from_code(tmp_path):
    conn, facts = _scenario(tmp_path)
    client = _client(tmp_path, [facts], [cr.fill_template(facts)])
    draft = cr.draft_response(conn, client, facts)
    assert client.calls == 1
    assert draft["source"] == "model"
    assert draft["recipient"] == facts["recipient"]
    assert draft["subject"] == "Response to your delivery note CDN-0001 on invoice SINV-0001"
    assert db.list_failures(conn) == []


def test_a_missing_recording_raises(tmp_path):
    conn, facts = _scenario(tmp_path)
    client = llm.ModelClient("replay", [], tmp_path / "none.jsonl", task=llm.RESPONSE_DRAFTING)
    with pytest.raises(llm.MissingRecording):
        cr.draft_response(conn, client, facts)


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_template_passes_the_check(tmp_path, name):
    _, facts = _scenario(tmp_path, name)
    body = cr.fill_template(facts)
    assert cr.check_response(body, facts) == []
    assert body.count("\n- ") == len(facts["incidents"])
    assert all(f"{abs(d['total']):.2f} EUR" in body for d in facts["documents"])


def _cn_total(facts):
    return f"{abs(facts['documents'][0]['total']):.2f}"


def test_a_complete_draft_passes(tmp_path):
    _, facts = _scenario(tmp_path)
    assert cr.check_response(cr.fill_template(facts), facts) == []


def _failures(tmp_path):
    _, facts = _scenario(tmp_path)
    total = _cn_total(facts)
    ci_total = f"{facts['documents'][1]['total']:.2f}"
    other = "999.99"
    body = cr.fill_template(facts)
    return facts, [
        (body.replace("SINV-0001", "SINV-0009"), "missing invoice number SINV-0001"),
        (body.replace("CDN-0001", "CDN-0009"), "missing delivery note number CDN-0001"),
        (body.replace(A, "SKU-OTHER"), f"missing incident {A}"),
        (body.replace(", 2 units", ", 3 units"), f"missing units 2 of {A}"),
        (body.replace("CN-0001", "CN-0009"), "missing document CN-0001"),
        (body.replace(f"{total} EUR", f"{other} EUR"), [f"missing total {total} of CN-0001", "invented amount 999.99"]),
        (
            body.replace(f"{ci_total} EUR", f"{other} EUR"),
            [f"missing total {ci_total} of CI-0002", "invented amount 999.99"],
        ),
        (body + f"\nWe will also refund {other} EUR.", "invented amount 999.99"),
        (body + "\nCafé", "body is not ASCII"),
        (body + "\n" + "x" * 3000, "body longer than 3000 characters"),
    ]


@pytest.mark.parametrize("index", range(10))
def test_each_rule_fails_on_its_own(tmp_path, index):
    facts, cases = _failures(tmp_path)
    body, problem = cases[index]
    assert cr.check_response(body, facts) == (problem if isinstance(problem, list) else [problem])


def test_a_total_next_to_another_document_does_not_count(tmp_path):
    _, facts = _scenario(tmp_path)
    cn, ci = facts["documents"]
    body = (
        f"Invoice SINV-0001, delivery note CDN-0001. {A} 2 units, {C} 1 units. "
        f"Credit note CN-0001 has a total of {abs(ci['total']):.2f} EUR. "
        f"Corrective invoice CI-0002 has a total of {abs(cn['total']):.2f} EUR."
    )
    assert abs(cn["total"]) != abs(ci["total"])
    assert cr.check_response(body, facts) == [
        f"missing total {abs(cn['total']):.2f} of CN-0001",
        f"missing total {abs(ci['total']):.2f} of CI-0002",
    ]


def test_units_next_to_another_sku_do_not_count(tmp_path):
    _, facts = _scenario(tmp_path)
    body = cr.fill_template(facts).replace(f"{A} ", "SKU-X ", 1)
    swapped = body + f"\n{A} 1 units"
    assert f"missing units 2 of {A}" in cr.check_response(swapped, facts)


def test_amounts_may_be_written_as_negative_and_numbers_in_names_are_not_amounts(tmp_path):
    _, facts = _scenario(tmp_path)
    negative = cr.fill_template(facts).replace(f"{_cn_total(facts)} EUR", f"-{_cn_total(facts)} EUR")
    assert cr.check_response(negative, facts) == []
    row = facts["incidents"][0]
    renamed = {**facts, "incidents": [{**row, "product": "Pad 2.50 cm"}, *facts["incidents"][1:]]}
    assert cr.check_response(cr.fill_template(renamed), renamed) == []


@pytest.mark.parametrize("failing", ["schema", "fact_check"])
def test_a_failed_draft_stores_the_template_and_one_failures_row(tmp_path, failing):
    conn, facts = _scenario(tmp_path)
    bad = {"body": 5} if failing == "schema" else "Please pay us 999.99 EUR."
    draft = cr.draft_response(conn, _client(tmp_path, [facts], [bad]), facts)
    assert draft["source"] == "template"
    assert cr.check_response(draft["body"], facts) == []
    row = db.get_failure(conn, "response-SINV-0001")
    assert (row["status"], row["step"], row["channel"]) == ("needs_review", "response_drafting", "sales")
    assert len(db.list_failures(conn)) == 1


def test_a_failed_draft_does_not_stop_the_next_one(tmp_path):
    conn, facts = _scenario(tmp_path)
    other = {**facts, "invoice": "SINV-0002"}
    client = _client(tmp_path, [facts, other], ["bad", cr.fill_template(other)])
    first = cr.draft_response(conn, client, facts)
    second = cr.draft_response(conn, client, other)
    assert (first["source"], second["source"]) == ("template", "model")
    assert db.get_failure(conn, "response-SINV-0002") is None


def test_the_facts_plan_the_corrections_once(tmp_path):
    conn, facts = _scenario(tmp_path)
    again = cr.response_facts(conn, 1)
    assert again == facts
    assert conn.execute("SELECT COUNT(*) FROM customer_corrections").fetchone()[0] == 2
