"""C13 (phase 08): the deterministic `reconciliation_matching` evaluation, its cases, grader, report and gate."""

import copy
from collections import Counter

import pytest

from purchase_cycle import cli, reconcile_match
from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation import reconcile_eval as re_


def test_the_cases_meet_the_coverage_minimums():
    items = re_.load_dataset()
    tags = Counter(tag for item in items for tag in item["tags"])
    assert len(items) >= re_.MINIMUMS["cases"] == 40
    assert len({i["id"] for i in items}) == len(items)
    for tag in re_.TAGS:
        assert tags[tag] >= re_.MINIMUMS[tag] == 3, tag


def test_the_case_files_are_the_versioned_build():
    assert re_.load_dataset() == re_.build_items()


def test_each_case_is_consistent_with_its_tags_and_its_expectation():
    for item in re_.load_dataset():
        assert item["tags"] == re_.derive_tags(item), item["id"]
        expected = item["expected"]
        for name, rec in expected["reconciliations"].items():
            assert (rec["result"] == "matched") == (not rec["discrepancies"]) or rec["result"] != "matched", item["id"]
            assert item["documents"][name]["answer"]["kind"] == "invoice", item["id"]
            assert expected["documents"][name] == ["linked", None], item["id"]
        assert expected["model_calls"] == len({d["text"] for d in item["documents"].values()}), item["id"]


def test_the_matching_module_imports_no_model_client():
    assert not any(name in vars(reconcile_match) for name in ("llm", "anthropic", "ModelClient"))


def _one(tmp_path, item_id="RM-010"):
    item = next(i for i in re_.load_dataset() if i["id"] == item_id)
    re_.write_items([item], tmp_path / "cases")
    return item, tmp_path / "cases"


def test_a_clean_case_passes_with_no_critical_error(tmp_path, capsys, no_network):
    _, folder = _one(tmp_path)
    assert re_.evaluate("replay", "test", dataset_dir=folder) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("case_success "))
    assert row.split()[1] == "100.0%" and "PASS" in row and "[" in row
    row = next(line for line in out.splitlines() if line.startswith("critical_errors "))
    assert row.split()[1] == "0" and row.endswith("PASS")
    assert "by kind: difference_not_reported=0" in out
    assert no_network == []


# One injection per critical error: each mutates what the grader reads, not the module under test.


def _not_reported(final):
    final["recs"][0]["discrepancies"] = []
    final["recs"][0]["result"] = "matched"
    final["shown"][final["recs"][0]["invoice"]] = {"result": "matched", "discrepancies": []}


def _invented(final):
    final["recs"][0]["discrepancies"].append(["GLV-NIT-M", "extra_line", 1.0])


def _wrong_type(final):
    final["recs"][0]["discrepancies"][0][1] = "quantity"


def _two(final):
    final["recs"].append(dict(final["recs"][0]))


def _unlinked(final):
    final["recs"][0]["invoice_status"] = "unlinked"


def _model_call(final):
    final["calls"] += 1


INJECTIONS = {
    "difference_not_reported": _not_reported,
    "difference_invented": _invented,
    "wrong_type": _wrong_type,
    "two_reconciliations": _two,
    "reconciliation_for_unlinked_document": _unlinked,
    "model_call_during_matching": _model_call,
}


def test_every_critical_kind_has_an_injection():
    assert set(INJECTIONS) == set(re_.CRITICAL_KINDS)


@pytest.mark.parametrize("kind", INJECTIONS)
def test_each_injected_critical_error_fails_the_suite_with_exit_one(kind, tmp_path, capsys, monkeypatch, no_network):
    item, folder = _one(tmp_path)
    real = re_._measure

    def measure(*args):
        final = copy.deepcopy(real(*args))
        INJECTIONS[kind](final)
        return final

    monkeypatch.setattr(re_, "_measure", measure)
    assert re_.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert f"{kind}=1" in out
    assert f"  {item['id']}  critical errors" in out
    assert "GATE FAILED: critical_errors" in out


def test_a_model_call_made_inside_the_matching_is_counted(tmp_path, capsys, monkeypatch, no_network):
    item, folder = _one(tmp_path)
    real = reconcile_match.classify

    def classify(*args):
        re_._ACTIVE[-1].calls += 1  # a client call from inside the matching
        return real(*args)

    monkeypatch.setattr(reconcile_match, "classify", classify)
    assert re_.evaluate("replay", "test", dataset_dir=folder) == 1
    assert "model_call_during_matching=" in capsys.readouterr().out


def test_a_wrong_expectation_fails_the_suite_with_exit_one(tmp_path, capsys, no_network):
    item, folder = _one(tmp_path)
    name = next(iter(item["expected"]["reconciliations"]))
    item["expected"]["reconciliations"][name]["discrepancies"][0][2] += 1
    re_.write_items([item], folder)
    assert re_.evaluate("replay", "test", dataset_dir=folder) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: case_success 0.0% below threshold 100.0%" in out
    assert f"  {item['id']}  expected" in out


def test_the_grader_counts_a_swapped_type_once_and_the_rest_as_missed_or_invented():
    assert re_.compare_rows([["A", "price", 1.0]], [["A", "quantity", 1.0]]) == (0, 0, 1)
    assert re_.compare_rows([["A", "price", 1.0], ["A", "quantity", 1.0]], [["A", "price", 1.0]]) == (1, 0, 0)
    assert re_.compare_rows([], [[None, "extra_line", 5.0]]) == (0, 1, 0)


def test_reconciliation_matching_is_a_local_suite():
    assert "reconciliation_matching" in harness.SUITES and harness.ALL_SUITES[-1] == "reconciliation_matching"
    assert "reconciliation_matching" not in harness.UPLOAD_SUITES
    assert harness._suite("reconciliation_matching") is re_


def test_the_suite_passes_through_the_cli(capsys, no_network):
    assert cli.main(["eval", "--suite", "reconciliation_matching"]) == 0
    out = capsys.readouterr().out
    assert f"reconciliation_matching  mode=replay  cases={len(re_.load_dataset())}" in out
    for tag in re_.TAGS:
        assert f"  {tag:<28} 100.0%" in out
    assert no_network == []
