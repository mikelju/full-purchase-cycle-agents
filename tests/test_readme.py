"""Phase 13 C5, C13 and C14: the README is short, ordered, linked, generated where it shows figures, and honest about the stack."""

import re
import subprocess
from pathlib import Path

from purchase_cycle import showcase

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCS = [ROOT / "README.md", ROOT / "docs" / "guide.md", ROOT / "docs" / "graphs.md"]
SECTIONS = [
    "How the LLM work is evaluated",
    "Evaluation results",
    "Catching an instruction change",
    "Sample traces",
    "Stack",
    "Architecture",
    "How it is built",
    "Quickstart",
    "Limits",
    "License",
]


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return set(out.stdout.splitlines())


def test_length_and_section_order():
    assert len(README.splitlines()) <= 250
    headings = re.findall(r"^## (.+)$", README, re.M)
    assert headings == SECTIONS
    assert README.index("badge.svg") < README.index("## How the LLM work is evaluated")
    assert "```mermaid" in README.split("## Evaluation results")[0]  # the evaluation loop
    assert "```mermaid" in README.split("## Architecture")[1]  # the orchestrator


def test_relative_links_and_images_resolve_to_versioned_files():
    tracked = _tracked()
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = (doc.parent / target.split("#")[0]).resolve().relative_to(ROOT).as_posix()
            assert path in tracked, f"{doc.name} links {target}, which is not a versioned file"
    for name in ("dashboard", "trace-orchestrator", "trace-module-graph", "trace-model-call"):
        assert f"docs/assets/{name}.png" in README


def test_quickstart_has_at_most_four_commands():
    block = README.split("## Quickstart")[1].split("```")[1]
    commands = [line for line in block.splitlines() if line.strip()]
    assert len(commands) <= 4
    assert commands[-1] == "uv run purchase-cycle cycle-demo"


def test_before_after_table_equals_the_command_output():
    # The results table depends on summaries that `npm run eval` writes, so `results-table --check` ends `npm run check`.
    assert "<!-- results-table:start -->" in README and "<!-- results-table:end -->" in README
    before = showcase.marked_block(README, showcase.BEFORE_AFTER_START, showcase.BEFORE_AFTER_END)
    assert before.strip() == showcase.compare_baselines("636711e").strip()


def test_stack_section_names_each_tool_with_a_file_that_uses_it():
    stack = README.split("## Stack")[1].split("\n## ")[0]
    expected = {
        "LangChain": "ChatAnthropic",
        "LangGraph": "StateGraph",
        "LangSmith": "evaluate",
        "Phoenix": "configure_phoenix_tracing",
    }
    for tool, symbol in expected.items():
        line = next(line for line in stack.splitlines() if line.startswith(f"- {tool}:"))
        files = re.findall(r"`(src/[\w/]+\.py)`", line)
        assert files, f"{tool} names no source file"
        assert any(symbol in (ROOT / f).read_text(encoding="utf-8") for f in files), f"{symbol} is in none of {files}"
    for symbol, name in (("interrupt(", "quote_decision.py"), ("SqliteSaver", "graph.py")):
        assert f"`src/purchase_cycle/{name}`" in stack
        assert symbol in (ROOT / "src" / "purchase_cycle" / name).read_text(encoding="utf-8")
