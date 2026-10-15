"""Side-agnostic review step: pause a plain message for a person, who approves, edits or rejects it."""

from typing import TypedDict

from langgraph.types import interrupt

APPROVE, EDIT, REJECT = "approve", "edit", "reject"


class ReviewState(TypedDict, total=False):
    message: dict  # to, subject, body
    decision: str
    final_body: str | None


def review(state: ReviewState) -> ReviewState:
    """Pause on the message; resumed with {"action": "approve" | "edit" | "reject", "body": text for edit}."""
    answer = interrupt({"message": state["message"]})
    action = answer["action"]
    if action == APPROVE:
        final = state["message"]["body"]
    elif action == EDIT:
        final = answer["body"]
    elif action == REJECT:
        final = None
    else:
        raise ValueError(f"unknown review action {action!r}")
    return {"decision": action, "final_body": final}
