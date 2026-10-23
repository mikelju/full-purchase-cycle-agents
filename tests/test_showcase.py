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
