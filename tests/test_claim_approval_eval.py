"""C15 (phase 09): the deterministic `claim_approval` evaluation, its scenarios, grader, report and gate."""

import copy
from collections import Counter

import pytest

from purchase_cycle.evaluation import claim_approval_eval as ca


def test_the_scenarios_meet_the_coverage_minimums():
    items = ca.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= ca.MINIMUMS["scenarios"] == 24
    assert len({i["id"] for i in items}) == len(items)
    for tag in ca.TAGS:
        assert tags[tag] >= ca.MINIMUMS[tag] == 2, tag


def test_the_scenario_files_are_the_versioned_build():
    assert ca.load_dataset() == ca.build_items()


def test_each_scenario_is_consistent_with_its_tags_and_the_oracle():
    for item in ca.load_dataset():
        assert item["tags"] == ca.derive_tags(item), item["id"]
        assert {s["op"] for s in item["steps"]} <= set(ca.OPS), item["id"]
        sent = [r for r, c in item["expected"]["claims"].items() if c and c["status"] == "sent"]
        assert len(item["expected"]["outbox"]) == len(sent), item["id"]
        assert item["expected"]["model_calls"] == ca._oracle_calls(item), item["id"]


def _one(tmp_path, item_id="CA-001"):
    item = next(i for i in ca.load_dataset() if i["id"] == item_id)
    ca.write_items([item], tmp_path / "scenarios")
    return item, tmp_path / "scenarios"


def test_a_clean_scenario_passes_with_no_critical_error(tmp_path, capsys, no_network):
    _, folder = _one(tmp_path)
    assert ca.evaluate("replay", "test", dataset_dir=folder) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert no_network == []


def test_the_whole_suite_passes(capsys, no_network):
    assert ca.evaluate("replay", "test") == 0
    out = capsys.readouterr().out
    assert f"scenarios={len(ca.load_dataset())}" in out
    for tag in ca.TAGS:
        assert f"  {tag:<20} 100.0%" in out


# One injection per critical error: each mutates what the grader reads, not the module under test.


def _email_for_unsent(final):
    final["emails"]["CLAIM-INV-0001-SUP-001.eml"] = {"to": "x", "subject": "s", "body": "text"}


def _duplicate(final):
    final["emails"]["CLAIM-INV-0001-SUP-002.eml"] = dict(next(iter(final["emails"].values())))


def _text_differs(final):
    next(iter(final["emails"].values()))["body"] += " and more"


def _without_discrepancies(final):
    final["results"]["INV-0001"] = "matched"


def _status_mismatch(final):
    final["emails"].clear()


def _model_call(final):
    final["calls"] += 1


# (injection, scenario): the rejected scenario CA-015 sends nothing, so an email there is for an unsent claim.
INJECTIONS = {
    "email_for_unsent_claim": (_email_for_unsent, "CA-015"),
    "duplicate_claim_or_email": (_duplicate, "CA-001"),
    "sent_text_differs": (_text_differs, "CA-001"),
    "claim_without_discrepancies": (_without_discrepancies, "CA-001"),
    "status_outbox_mismatch": (_status_mismatch, "CA-001"),
    "model_call_on_rerun": (_model_call, "CA-001"),
}


def test_every_claim_approval_kind_has_an_injection():
    assert set(INJECTIONS) == set(ca.CRITICAL_KINDS)


@pytest.mark.parametrize("kind", INJECTIONS)
def test_each_injected_error_fails_claim_approval_with_exit_one(kind, tmp_path, capsys, monkeypatch, no_network):
    injection, item_id = INJECTIONS[kind]
    item, folder = _one(tmp_path, item_id)
    real_errors = ca._errors

    def errors(it, final, log):
        final = copy.deepcopy(final)
        injection(final)
        return real_errors(it, final, log)

    monkeypatch.setattr(ca, "_errors", errors)
    assert ca.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert f"{kind}=1" in out
    assert f"  {item['id']}  critical errors" in out
    assert "GATE FAILED: critical_errors" in out


def test_a_wrong_expectation_fails_claim_approval_with_exit_one(tmp_path, capsys, no_network):
    item, folder = _one(tmp_path)
    item["expected"]["claims"]["INV-0001"]["status"] = "rejected"
    ca.write_items([item], folder)
    assert ca.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success 0.0% below threshold 100.0%" in out
    assert f"  {item['id']}  expected" in out


def test_a_wrong_exit_code_expectation_fails_the_scenario(tmp_path, capsys, no_network):
    item, folder = _one(tmp_path)
    item["steps"][1]["exit"] = 1
    ca.write_items([item], folder)
    assert ca.evaluate("replay", "test", dataset_dir=folder) == 1
    assert f"  {item['id']}  expected" in capsys.readouterr().out
