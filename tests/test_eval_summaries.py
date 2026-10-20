import argparse
import json
import re
import subprocess
from pathlib import Path

import pytest

from purchase_cycle.evaluation import harness

REPORT = """quote_wait  mode=replay  scenarios=24  (deterministic: scripted timelines, no model call, no split, no baseline)
metric                    value   95% CI          n     unit            target   target met  threshold  gate
scenario_success          100.0%  [86.2, 100.0]   24    scenario        >=100.0% met         100.0%     PASS  failed scenarios  0 of 24, Wilson upper bound 13.8%
critical_errors           0       -               24    scenario        =0       met         0          PASS
  by kind: second_open_request=0  wrong_quantity=1
regression vs baseline 2026-10-10: no significant drop (scenario_success lost 0 gained 0 p=1.00)
per tag:
  all_replied      100.0% [61.0, 100.0] n=6
  some_replied     95.5% [72.2, 99.0] n=10
failures: 0
"""

DETAILED = """email_order_extraction  mode=replay  split=test  emails=234  expected_lines=586  model=claude-haiku-4-5
metric                    value   95% CI          n     unit            target   target met  threshold  gate
field_sku                 98.6%   [97.3, 99.3]    586   expected_line   none     n/a         none       reported
per category:
  body_list            emails=39
    intake_accuracy 97.4% [86.8, 99.5] n=39   line_recall 99.1% [95.0, 99.8] n=109
    field_quantity n/a   email_exact_match 97.4% [86.8, 99.5] n=39
  exact_name       n=187  product 100.0% [98.0, 100.0]   quantity 99.5% [97.0, 99.9]
failures: 0
GATE FAILED: field_sku 98.6% below threshold 99.0%
"""


class Stub:
    def __init__(self, report, code, error=None):
        self.report, self.code, self.error = report, code, error

    def evaluate(self, mode, split, set_baseline=False, workers=1):
        print(self.report, end="")
        if self.error:
            raise self.error
        return self.code


def run(monkeypatch, tmp_path, suite, stub):
    out = tmp_path / "summaries"
    monkeypatch.setattr(harness, "SUMMARIES_DIR", out)
    monkeypatch.setattr(harness, "_suite", lambda name: stub)
    args = argparse.Namespace(suite=suite, mode="replay", split="test", set_baseline=False, workers=1)
    return harness.cmd_eval(args), out


def test_summary_values_equal_the_printed_figures(monkeypatch, tmp_path, capsys):
    code, out = run(monkeypatch, tmp_path, "quote_wait", Stub(REPORT, 0))
    assert code == 0 and capsys.readouterr().out == REPORT
    s = json.loads((out / "quote_wait.json").read_text(encoding="utf-8"))
    assert s["suite"] == "quote_wait" and s["status"] == "passed" and s["exit_code"] == 0
    assert s["case_count"] == 24 and s["counts"] == {"scenarios": 24}
    assert s["baseline_date"] == "2026-10-10" and s["failed_cases"] == 0
    first = s["metrics"][0]
    assert (first["name"], first["value"], first["ci_low"], first["ci_high"], first["n"]) == (
        "scenario_success",
        100.0,
        86.2,
        100.0,
        24,
    )
    assert first["target"] == ">=100.0%" and first["note"].startswith("failed scenarios")
    assert s["gates"][0] == {
        "metric": "scenario_success",
        "threshold": 100.0,
        "threshold_text": "100.0%",
        "verdict": "PASS",
    }
    assert s["critical_errors"] == {"total": 0.0, "by_kind": {"second_open_request": 0, "wrong_quantity": 1}}
    tags = s["categories"]["tag"]
    assert [t["label"] for t in tags] == ["all_replied", "some_replied"]
    assert tags[1]["figures"] == [{"name": "some_replied", "value": 95.5, "low": 72.2, "high": 99.0, "n": 10}]


def test_nested_categories_and_gate_failures(monkeypatch, tmp_path):
    code, out = run(monkeypatch, tmp_path, "email_order_extraction", Stub(DETAILED, 1))
    s = json.loads((out / "email_order_extraction.json").read_text(encoding="utf-8"))
    assert code == 1 and s["status"] == "failed" and s["exit_code"] == 1
    assert s["counts"] == {"emails": 234, "expected_lines": 586} and s["model"] == "claude-haiku-4-5"
    assert s["gates"][0]["verdict"] == "reported" and s["gates"][0]["threshold"] is None
    assert s["gate_failures"] == ["field_sku 98.6% below threshold 99.0%"]
    body, simple = s["categories"]["category"]
    assert body["counts"] == {"emails": 39}
    assert [f["name"] for f in body["figures"]] == ["intake_accuracy", "line_recall", "email_exact_match"]
    assert simple["label"] == "exact_name" and simple["counts"] == {"n": 187}
    assert [f["value"] for f in simple["figures"]] == [100.0, 99.5]


def test_missing_suite_is_recorded_as_missing(monkeypatch, tmp_path):
    code, out = run(monkeypatch, tmp_path, "quote_wait", Stub("", 2))
    s = json.loads((out / "quote_wait.json").read_text(encoding="utf-8"))
    assert code == 2 and s["status"] == "missing" and s["metrics"] == []


def test_crashed_suite_is_recorded_as_failed_and_still_raises(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError):
        run(monkeypatch, tmp_path, "quote_wait", Stub("partial\n", 0, RuntimeError("boom")))
    s = json.loads((tmp_path / "summaries" / "quote_wait.json").read_text(encoding="utf-8"))
    assert s["status"] == "failed" and s["error"] == "RuntimeError: boom" and s["report"] == "partial\n"


def test_writer_does_not_change_report_or_exit_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(harness, "_suite", lambda name: Stub(REPORT, 1))
    args = argparse.Namespace(suite="quote_wait", mode="replay", split="test", set_baseline=False, workers=1)
    monkeypatch.setattr(harness, "SUMMARIES_DIR", tmp_path / "ok")
    with_writer = (harness.cmd_eval(args), capsys.readouterr().out)
    # A folder that cannot be created: the writer warns on stderr only.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(harness, "SUMMARIES_DIR", blocker / "sub")
    failing_writer = harness.cmd_eval(args)
    captured = capsys.readouterr()
    assert with_writer == (1, REPORT) and (failing_writer, captured.out) == (1, REPORT)
    assert "not written" in captured.err


def test_real_suite_summary_equals_its_printed_report(monkeypatch, tmp_path, capsys):
    from purchase_cycle.evaluation import routing_eval

    monkeypatch.setattr(harness, "SUMMARIES_DIR", tmp_path / "real")
    args = argparse.Namespace(suite="channel_routing", mode="replay", split="test", set_baseline=False, workers=1)
    code = harness.cmd_eval(args)
    printed = capsys.readouterr().out
    s = json.loads((tmp_path / "real" / "channel_routing.json").read_text(encoding="utf-8"))
    assert routing_eval and code == 0 and s["report"] == printed and s["status"] == "passed"
    m = s["metrics"][0]
    assert re.search(
        rf"{m['name']}\s+{re.escape(m['value_text'])}\s+\[{m['ci_low']}, {m['ci_high']}\]\s+{m['n']}\s", printed
    )
    assert len(s["categories"]["route"]) == 6 and s["case_count"] == 47


def test_summaries_folder_is_git_ignored():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(["git", "check-ignore", "-q", "data/eval_summaries/x.json"], cwd=root, check=False)
    assert result.returncode == 0


def test_a_report_row_the_writer_cannot_read_warns_and_never_changes_the_exit_code(monkeypatch, tmp_path, capsys):
    odd = REPORT.replace("scenario_success          100.0%", "scenario success          100.0 %", 1)
    code, out = run(monkeypatch, tmp_path, "quote_wait", Stub(odd, 0))
    captured = capsys.readouterr()
    assert code == 0 and captured.out == odd
    assert "unreadable" in captured.err and "not understood" in captured.err
    s = json.loads((out / "quote_wait.json").read_text(encoding="utf-8"))
    assert s["status"] == "failed" and s["exit_code"] == 0 and "not understood" in s["error"]


def test_a_unit_of_two_words_is_read_as_one_metric_row(monkeypatch, tmp_path):
    row = "discrepancy_coverage        100.0%  [95.9, 100.0]   90    discrepancy row   none     n/a         none       reported\n"
    report = REPORT.replace("failures: 0\n", row + "failures: 0\n")
    _, out = run(monkeypatch, tmp_path, "quote_wait", Stub(report, 0))
    s = json.loads((out / "quote_wait.json").read_text(encoding="utf-8"))
    assert [m["unit"] for m in s["metrics"]][-1] == "discrepancy row" and len(s["gates"]) == 3
