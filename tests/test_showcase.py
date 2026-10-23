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
