from purchase_cycle import showcase


def test_graphs_file_matches_the_compiled_graphs():
    committed = showcase.GRAPHS_FILE.read_text(encoding="utf-8")
    assert committed == showcase.render_graphs(), "docs/graphs.md is stale; run `uv run purchase-cycle diagrams`"


def test_one_diagram_per_graph():
    text = showcase.render_graphs()
    assert text.count("```mermaid") == 13
    assert text.count("## ") == 13
    assert "## Orchestrator" in text


def test_a_hand_edit_is_detected():
    committed = showcase.GRAPHS_FILE.read_text(encoding="utf-8")
    assert committed.replace("orders(orders)", "orders(edited)", 1) != showcase.render_graphs()


def _summary(directory, name, value, status="passed", verdict="PASS"):
    import json

    metric = {"name": "acc", "value": value, "value_text": f"{value}%", "ci_low": 90.0, "ci_high": 100.0}
    data = {
        "suite": name,
        "status": status,
        "case_count": 12,
        "metrics": [metric],
        "gates": [{"metric": "acc", "verdict": verdict}],
    }
    (directory / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_results_table_rows_and_missing_suite(tmp_path):
    from purchase_cycle.evaluation.harness import ALL_SUITES

    _summary(tmp_path, ALL_SUITES[0], 100.0)
    _summary(tmp_path, ALL_SUITES[1], 95.0, status="failed", verdict="FAIL")
    _summary(tmp_path, ALL_SUITES[2], 80.0, verdict="reported")
    rows = showcase.results_table(tmp_path).splitlines()
    assert len(rows) == 2 + len(ALL_SUITES)
    assert rows[2] == f"| {ALL_SUITES[0]} | 12 | acc | 100.0% [90.0, 100.0] | PASS |"
    assert rows[3].endswith("| FAIL |")
    assert rows[4].endswith("| reported |")
    assert rows[5] == f"| {ALL_SUITES[3]} | - | - | - | missing |"


def test_check_exits_non_zero_when_the_readme_table_differs(tmp_path, capsys):
    from purchase_cycle import cli
    from purchase_cycle.evaluation.harness import ALL_SUITES

    for name in ALL_SUITES:
        _summary(tmp_path, name, 100.0)
    readme = tmp_path / "README.md"
    table = showcase.results_table(tmp_path)
    readme.write_text(f"intro\n{showcase.RESULTS_START}\n{table}\n{showcase.RESULTS_END}\nend\n", encoding="utf-8")
    args = ["results-table", "--summaries", str(tmp_path), "--check", str(readme)]
    assert cli.main(args) == 0
    readme.write_text(readme.read_text(encoding="utf-8").replace("100.0%", "99.0%", 1), encoding="utf-8")
    assert cli.main(args) == 1
    assert "differs" in capsys.readouterr().out
    readme.write_text("no markers here\n", encoding="utf-8")
    assert cli.main(args) == 1


def _claims_baseline(passes: list[bool]) -> dict:
    return {"cases": {f"CLM-{i}": {"case_pass_rate": ok} for i, ok in enumerate(passes)}}


def test_compare_pairs_counts_lost_gained_and_p():
    before = {"a": True, "b": True, "c": False, "d": False, "e": True}
    after = {"a": True, "b": False, "c": True, "d": True, "e": True, "extra": True}
    result = showcase.compare_pairs(before, after)
    assert (result["n"], result["before"], result["after"]) == (5, 3, 4)
    assert (result["lost"], result["gained"]) == (1, 2)
    assert abs(result["p"] - 1.0) < 1e-9  # 3 discordant pairs: 2 * P(X <= 1) = 2 * 0.5 -> capped at 1


def test_compare_baselines_from_two_fixtures(tmp_path):
    import json

    old = _claims_baseline([True, True, False, False, False, False, True, True, True, True])
    new = _claims_baseline([True, True, True, True, True, True, True, True, True, True])
    (tmp_path / "claim_drafting.json").write_text(json.dumps(new), encoding="utf-8")
    (tmp_path / "order_scenarios.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    (tmp_path / "unchanged.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    olds = {"claim_drafting": old, "order_scenarios": {"x": 0}, "unchanged": {"x": 1}}
    table = showcase.compare_baselines("OLD", tmp_path, load_old=olds.get)
    assert "| claim_drafting | case_pass_rate | claim | 6/10 (60.0%) | 10/10 (100.0%) | 0 | 4 | 0.12 |" in table
    assert "order_scenarios: baseline changed" in table
    assert "unchanged" not in table


def test_compare_baselines_rejects_an_unknown_revision(capsys):
    import pytest

    with pytest.raises(ValueError, match="unknown git revision"):
        showcase.compare_baselines("no-such-revision-0000")

    class Args:
        old_rev = "no-such-revision-0000"

    assert showcase.cmd_compare_baselines(Args()) == 1
    assert "unknown git revision" in capsys.readouterr().err


def test_compare_baselines_with_no_shared_cases_does_not_divide_by_zero(tmp_path):
    import json

    new = _claims_baseline([True, True])
    (tmp_path / "claim_drafting.json").write_text(json.dumps(new), encoding="utf-8")
    old = {"cases": {"OTHER-1": {"case_pass_rate": True}}}
    table = showcase.compare_baselines("OLD", tmp_path, load_old=lambda name: old)
    assert "| claim_drafting | case_pass_rate | claim | 0/0 (n/a) | 0/0 (n/a) | 0 | 0 |" in table
