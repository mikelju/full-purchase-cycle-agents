"""Minimal graph: extract one order line from free text and match it against the catalog."""

import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from purchase_cycle import db
from purchase_cycle.llm import ModelClient


class LineState(TypedDict, total=False):
    sentence: str
    case_id: str | None
    extracted: dict
    product: dict | None
    matched: bool


def build_graph(client: ModelClient, db_path: Path | str, checkpointer=None, interrupt_after=None):
    def extract(state: LineState) -> LineState:
        line = client.extract(state["sentence"], state.get("case_id"))
        return {"extracted": line.model_dump()}

    def match(state: LineState) -> LineState:
        sku = state["extracted"]["sku"]
        if sku is None:
            return {"product": None, "matched": False}
        conn = db.connect(db_path)
        try:
            product = db.get_product(conn, sku)
        finally:
            conn.close()
        return {"product": product, "matched": product is not None}

    builder = StateGraph(LineState)
    builder.add_node("extract", extract)
    builder.add_node("match", match)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "match")
    builder.add_edge("match", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after, name="order_line_extraction")


def sqlite_checkpointer(path: Path | str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))
