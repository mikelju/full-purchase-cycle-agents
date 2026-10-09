"""C13, C15, C23 (phase 05): the deterministic `failure_recovery` evaluation, its scenarios, report and gate."""

import json

import pytest

from purchase_cycle import cli, faults
from purchase_cycle.evaluation import harness, recovery_eval


def test_versioned_scenarios_meet_their_minimums_and_match_their_builder():
    items = recovery_eval.load_dataset()
    assert len(items) >= 20
    assert items == recovery_eval.build_items()
    assert len({i["id"] for i in items}) == len(items)
    assert {i["category"] for i in items} == set(recovery_eval.CATEGORIES)
    crashes = {(i["channel"], i["crash_at"]) for i in items if i["category"] == "crash_and_resume"}
    points = (faults.AFTER_CHANNEL_STEPS, faults.AFTER_STORE_COMMIT, faults.IN_REPLY)
    assert crashes == {(c, p) for c in ("web_form", "email", "whatsapp") for p in points}
    assert all(i["expected"]["orders"] <= 1 for i in items)


def test_eval_command_runs_every_scenario_offline_and_passes_the_gate(capsys, no_network):
    assert cli.main(["eval", "--suite", "failure_recovery"]) == 0
    assert no_network == []
    out = capsys.readouterr().out
    n = len(recovery_eval.load_dataset())
    assert f"failure_recovery  mode=replay  scenarios={n}" in out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert row.split()[:2] == ["scenario_success", "100.0%"]
    for cell in (f" {n} ", " scenario ", ">=100.0%", " met ", "PASS"):
        assert cell in row, cell
    assert "per category:" in out
    assert "failures: 0" in out


def test_a_wrong_expectation_fails_the_gate_with_exit_one(tmp_path, capsys, no_network):
    items = recovery_eval.load_dataset()
    duplicate = next(i for i in items if i["category"] == "redelivery")
    items = [{**duplicate, "expected": {**duplicate["expected"], "orders": 2}}]
    path = tmp_path / "dataset.jsonl"
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    assert recovery_eval.evaluate("replay", "test", dataset_path=path) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: scenario_success" in out
    assert f"  {duplicate['id']}  expected" in out


def test_a_wrong_phase_outcome_or_count_is_a_scenario_failure():
    expected = {"phases": ["crash", "stored"], "orders": 1, "lines": 1, "sources": 1, "needs_review": 0,
                "resolved": 0, "outbox_files": 0}  # fmt: skip
    item = {"expected": expected}
    assert recovery_eval.grade(item, dict(expected))["correct"]
    assert not recovery_eval.grade(item, {**expected, "phases": ["crash", "parked"]})["correct"]
    assert not recovery_eval.grade(item, {**expected, "orders": 2})["correct"]


def test_the_suite_runs_locally_only():
    assert "failure_recovery" in harness.SUITES
    assert "failure_recovery" in harness.ALL_SUITES
    assert "failure_recovery" not in harness.UPLOAD_SUITES
    with pytest.raises(SystemExit):
        cli.main(["eval-upload", "--suite", "failure_recovery"])
