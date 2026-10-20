import copy
import json
import re

import pytest

from purchase_cycle.evaluation import harness
from purchase_cycle.evaluation import purchase_cycle_eval as pc

ITEM_ID = "PC-004"


def test_the_dataset_has_the_minimum_scenarios_and_tags():
    items = pc.load_dataset()
    assert len(items) >= pc.MINIMUMS["scenarios"]
    counts = pc.coverage(items)
    for tag in pc.TAGS:
        assert counts[tag] >= pc.MINIMUMS[tag], tag


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    item = next(i for i in pc.load_dataset() if i["id"] == ITEM_ID)
    measured = pc.measure(item, tmp_path_factory.mktemp("base"))
    return item, measured


def _again(measured):
    return next(i for i, p in enumerate(measured["passes"]) if p["kind"] in ("repeat", "resume"))


def _repeat(measured):
    return next(i for i, p in enumerate(measured["passes"]) if p["kind"] == "repeat")


def _decision(m):
    m["passes"][0]["decided"] = [0, 1]


def _duplicate(m):
    m["passes"][_repeat(m)]["changed"] = True


def _order(m):
    p = next(p for p in m["passes"] if p["stages"])
    p["stages"] = list(reversed(p["stages"]))


def _model_call(m):
    m["passes"][_again(m)]["calls"] = 1


def _final(m):
    m["cycle"]["rows"] = [["altered"]]


INJECTIONS = {
    "decision_taken": _decision,
    "duplicate": _duplicate,
    "stage_out_of_order": _order,
    "model_call": _model_call,
    "final_state_differs": _final,
}


def _folder(tmp_path, item):
    pc.write_items([item], tmp_path / "cases")
    return tmp_path / "cases"


def _stubbed(monkeypatch, baseline, mutate=None):
    _, measured = baseline

    def measure(item, tmp):
        rows = copy.deepcopy(measured)
        if mutate:
            mutate(rows)
        return rows

    monkeypatch.setattr(pc, "measure", measure)


def test_every_critical_kind_has_an_injection():
    assert set(INJECTIONS) == set(pc.CRITICAL_KINDS)


def test_the_real_scenario_is_correct_with_no_critical_error(baseline):
    item, measured = baseline
    result = pc.grade(item, measured)
    assert result["correct"] is True and sum(result["critical"].values()) == 0


def test_a_clean_run_exits_zero_and_reports_the_interval(tmp_path, capsys, monkeypatch, baseline):
    _stubbed(monkeypatch, baseline)
    assert pc.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("scenario_success "))
    assert "100.0%" in row and "[" in row and "PASS" in row


@pytest.mark.parametrize("kind", INJECTIONS)
def test_each_injected_critical_error_fails_the_suite_with_exit_one(kind, tmp_path, capsys, monkeypatch, baseline):
    _stubbed(monkeypatch, baseline, INJECTIONS[kind])
    assert pc.evaluate("replay", "test", dataset_dir=_folder(tmp_path, baseline[0])) == 1
    out = capsys.readouterr().out
    assert re.search(rf"{kind}=[1-9]", out)
    assert f"  {ITEM_ID}  critical errors" in out
    assert "GATE FAILED: critical_errors" in out


def test_purchase_cycle_is_a_local_suite_registered_last():
    assert "purchase_cycle" in harness.SUITES and harness.ALL_SUITES[-1] == "purchase_cycle"
    assert "purchase_cycle" not in harness.UPLOAD_SUITES


def _item(item_id):
    return next(i for i in pc.load_dataset() if i["id"] == item_id)


def test_stages_that_really_run_out_of_order_are_caught(tmp_path, monkeypatch):
    from purchase_cycle import cycle

    build, real = cycle.build_cycle_graph, cycle.STAGES

    def swapped(*args, **kwargs):  # the graph is wired with receipts before reconcile
        monkeypatch.setattr(cycle, "STAGES", ("orders", "quotes", "receipts", "reconcile", "sales"))
        try:
            return build(*args, **kwargs)
        finally:
            monkeypatch.setattr(cycle, "STAGES", real)

    monkeypatch.setattr(cycle, "build_cycle_graph", swapped)
    item = _item("PC-013")
    result = pc.grade(item, pc.measure(item, tmp_path))
    assert result["critical"]["stage_out_of_order"] >= 1


def test_a_stage_that_never_ran_in_a_finished_pass_is_caught(baseline):
    _, measured = baseline
    rows = copy.deepcopy(measured)
    full = next(p for p in rows["passes"] if p["kind"] == "pass")
    full["stages"] = full["stages"][:-1]
    assert pc._errors(baseline[0], rows)["stage_out_of_order"] >= 1


def test_a_duplicate_after_a_resumption_is_a_duplicate_not_a_different_final_state(tmp_path, monkeypatch):
    item = _item("PC-002")
    real = pc._cycle_pass

    stopped = []

    def duplicating(root, stage, calls, process):
        out = real(root, stage, calls, process)
        if stage:
            stopped.append(stage)
        elif stopped:  # the pass that resumes a stopped one leaves an extra file behind
            (root / "outbox").mkdir(exist_ok=True)
            (root / "outbox" / "extra.txt").write_text("duplicate", encoding="utf-8")
            stopped.clear()
        return out

    monkeypatch.setattr(pc, "_cycle_pass", duplicating)
    errors = pc.grade(item, pc.measure(item, tmp_path))["critical"]
    assert errors["duplicate"] >= 1 and errors["final_state_differs"] == 0


def test_a_failed_decision_step_is_not_a_model_call(tmp_path):
    item = copy.deepcopy(_item("PC-006"))
    step = next(s for s in item["steps"] if s["do"] == "decide")
    step["ref"] = 99  # a reference that does not exist
    result = pc.grade(item, pc.measure(item, tmp_path))
    assert result["critical"]["model_call"] == 0 and result["correct"] is False


def test_the_dataset_files_equal_the_scenarios_in_code():
    assert pc.load_dataset() == [json.loads(json.dumps(i)) for i in pc.build_items()]


def test_the_tags_follow_the_wording_of_the_spec():
    items = {i["id"]: i for i in pc.build_items()}

    def tagged(tag):
        return {k for k, i in items.items() if tag in i["tags"]}

    assert tagged("rejected") == {"PC-010", "PC-011"}  # a rejected quote or claim that leaves the order waiting
    assert tagged("short_delivery") == {"PC-007", "PC-011", "PC-016"}  # and a new quote request starts
    assert tagged("restart") == {"PC-002", "PC-012"}  # a cycle pass run by a new process
