"""C11: the side-agnostic review node."""

import ast
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from purchase_cycle import review

MESSAGE = {"to": "a@b.c", "subject": "Hello", "body": "Draft text"}


def _graph():
    builder = StateGraph(review.ReviewState)
    builder.add_node("review", review.review)
    builder.add_edge(START, "review")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.mark.parametrize(
    ("answer", "decision", "final"),
    [
        ({"action": "approve"}, "approve", "Draft text"),
        ({"action": "edit", "body": "Edited text"}, "edit", "Edited text"),
        ({"action": "reject"}, "reject", None),
    ],
)
def test_each_answer_returns_the_decision_and_final_body(answer, decision, final):
    graph, run = _graph(), {"configurable": {"thread_id": "t"}}
    graph.invoke({"message": MESSAGE}, run)
    assert graph.get_state(run).next == ("review",)
    assert graph.get_state(run).tasks[0].interrupts[0].value == {"message": MESSAGE}
    result = graph.invoke(Command(resume=answer), run)
    assert (result["decision"], result["final_body"]) == (decision, final)


def test_the_module_imports_nothing_from_the_business_side():
    tree = ast.parse(Path(review.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert {name.split(".")[0] for name in imported} <= {"typing", "langgraph"}
