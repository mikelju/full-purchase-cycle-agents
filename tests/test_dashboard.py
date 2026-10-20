import json
import re

from purchase_cycle.dashboard import build_html, write_dashboard
from purchase_cycle.evaluation.harness import ALL_SUITES


def _summary(name, status="passed"):
    return {
        "suite": name,
        "status": status,
        "exit_code": 0,
        "mode": "replay",
        "split": None,
        "model": None,
        "counts": {"scenarios": 24},
        "case_count": 24,
        "metrics": [
            {
                "name": "scenario_success",
                "value": 87.5,
                "value_text": "87.5%",
                "ci_low": 69.0,
                "ci_high": 95.7,
                "n": 24,
                "unit": "scenario",
                "target": ">=90.0%",
                "target_met": "not_met",
                "note": "<b>note</b>",
            }
        ],
        "gates": [{"metric": "scenario_success", "threshold": 90.0, "threshold_text": "90.0%", "verdict": "FAIL"}],
        "gate_failures": ["scenario_success"],
        "critical_errors": {"total": 0.0, "by_kind": {"wrong_quantity": 3}},
        "baseline_date": "2026-01-02",
        "regression": None,
        "failed_cases": 3,
        "categories": {
            "tag": [
                {
                    "label": "all_replied",
                    "counts": {"n": 6},
                    "figures": [{"name": "all_replied", "value": 61.5, "low": 12.3, "high": 99.1, "n": 6}],
                    "raw": [],
                }
            ]
        },
        "report": "",
    }


def _fixtures(tmp_path):
    for name in ALL_SUITES[2:]:
        (tmp_path / f"{name}.json").write_text(json.dumps(_summary(name)), encoding="utf-8")
    crashed = _summary(ALL_SUITES[1], "failed")
    crashed.update(error="boom <script>", exit_code=None, metrics=[], gates=[], categories={})
    (tmp_path / f"{ALL_SUITES[1]}.json").write_text(json.dumps(crashed), encoding="utf-8")
    # ALL_SUITES[0] has no file: missing


def test_one_section_per_suite_with_values(tmp_path):
    _fixtures(tmp_path)
    page = build_html(tmp_path)
    assert len(ALL_SUITES) == 21
    assert len(re.findall(r"<section id=", page)) == 21
    for name in ALL_SUITES:
        assert f'<section id="{name}">' in page
    expected = (
        "87.5%",
        "69.0",
        "95.7",
        "&gt;=90.0%",
        "90.0%",
        "FAIL",
        "2026-01-02",
        "wrong_quantity=3",
        "61.5",
        "12.3",
        "99.1",
        "all_replied",
    )
    for value in expected:
        assert value in page


def test_missing_and_failed_shown_and_escaped(tmp_path):
    _fixtures(tmp_path)
    page = build_html(tmp_path)
    assert "1 missing" in page and "No summary file" in page
    assert "boom &lt;script&gt;" in page
    assert "<script" not in page and "<b>note" not in page and "&lt;b&gt;note" in page


def test_a_summary_that_is_valid_json_but_not_an_object_is_shown_as_failed(tmp_path):
    (tmp_path / f"{ALL_SUITES[0]}.json").write_text("[]", encoding="utf-8")
    (tmp_path / f"{ALL_SUITES[1]}.json").write_text('"text"', encoding="utf-8")
    page = build_html(tmp_path)
    assert "2 failed" in page and page.count("expected an object") == 2


def test_no_external_resources(tmp_path):
    _fixtures(tmp_path)
    page = build_html(tmp_path)
    assert "http" not in page
    assert "<link" not in page and "src=" not in page and "@import" not in page and "url(" not in page
    assert "<svg" in page


def test_write_dashboard(tmp_path):
    _fixtures(tmp_path)
    out = write_dashboard(tmp_path, tmp_path / "out" / "d.html")
    assert out.read_text(encoding="utf-8") == build_html(tmp_path)


def test_css_wraps_at_narrow_widths(tmp_path):
    _fixtures(tmp_path)
    page = build_html(tmp_path)
    assert "flex-wrap:wrap" in page and "overflow-wrap:anywhere" in page and "overflow-x:auto" in page
