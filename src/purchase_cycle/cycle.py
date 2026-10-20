"""Phase 12: the `purchase_cycle` graph, one pass over the whole purchase cycle.

Each node is a stage that calls the same module functions as the module commands, with the same database,
checkpoint file and folders. Stages hand off only through the database and the folders; the graph state holds
per-stage counts. The orchestrator takes no decision: a module thread that reaches a human decision stays paused.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from purchase_cycle import (
    claims,
    config,
    customer_responses,
    db,
    quote_decision,
    quotes,
    receipts,
    reconciliation,
    router,
    sales,
)
from purchase_cycle.clarification import ClarificationClients
from purchase_cycle.email_order import CHANNEL as EMAIL
from purchase_cycle.email_order import build_email_order_graph
from purchase_cycle.graph import sqlite_checkpointer
from purchase_cycle.llm import (
    CLAIM_DRAFTING,
    CLARIFICATION_ANSWER,
    CLARIFICATION_QUESTION,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    OFFER_EXTRACTION,
    RESPONSE_DRAFTING,
    SUPPLIER_DOCUMENT_EXTRACTION,
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
)
from purchase_cycle.web_form import CHANNEL as WEB_FORM
from purchase_cycle.web_form import build_web_form_graph
from purchase_cycle.whatsapp_order import CHANNEL as WHATSAPP
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph

STAGES = ("orders", "quotes", "reconcile", "receipts", "sales")
THREAD_PREFIX = "cycle-run-"

# A model client factory: `client(task, catalog)` returns an object with `extract`, one per model task.
ClientFactory = Callable[..., object]


@dataclass(frozen=True)
class Folders:
    """The folders of a cycle pass; the optional ones default to the folders the module commands use."""

    orders: Path  # inbox of web form, email and WhatsApp items
    replies: Path  # supplier quote replies (.eml)
    documents: Path  # supplier invoices and delivery notes
    signed_notes: Path  # signed customer delivery notes
    outbox: Path = config.OUTBOX_DIR
    supplier_outbox: Path = config.SUPPLIER_OUTBOX_DIR
    delivery_notes: Path = config.CUSTOMER_DELIVERY_NOTES_DIR
    invoices: Path = config.CUSTOMER_INVOICES_DIR

    def __post_init__(self):
        for field in fields(self):
            object.__setattr__(self, field.name, Path(getattr(self, field.name)))


def _merge(old: dict | None, new: dict) -> dict:
    return {**(old or {}), **new}


class CycleState(TypedDict, total=False):
    counts: Annotated[dict, _merge]


def run_number(thread_id: str) -> int:
    return int(thread_id.removeprefix(THREAD_PREFIX))


def run_id(thread_id: str) -> str:
    """The run id in the order thread ids of a pass; the same on a resumption of that pass."""
    return f"c{run_number(thread_id):04d}"


def _order_graphs(db_path, checkpointer, client: ClientFactory, outbox: Path) -> dict:
    """The three channel graphs with the clarify step, built as `cli.cmd_route` builds them (recovery on)."""
    conn = db.connect(db_path)
    try:
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()

    def clarification() -> ClarificationClients:
        return ClarificationClients(client(CLARIFICATION_QUESTION, catalog), client(CLARIFICATION_ANSWER, catalog))

    web = build_web_form_graph(
        client(MATCHING, catalog), db_path, checkpointer, clarification=clarification(), recovery=True
    )
    email = build_email_order_graph(
        client(EMAIL_INTAKE, catalog),
        client(EMAIL_EXTRACTION, catalog),
        db_path,
        checkpointer,
        clarification=clarification(),
        recovery=True,
    )
    whatsapp = build_whatsapp_order_graph(
        client(WHATSAPP_INTAKE, catalog),
        client(WHATSAPP_EXTRACTION, catalog),
        db_path,
        outbox,
        checkpointer,
        clarification=clarification(),
    )
    return {WEB_FORM: web, EMAIL: email, WHATSAPP: whatsapp}


def _seen_web_form(checkpointer, graph) -> Callable[[Path], bool]:
    """True for a web form file whose submission id an earlier thread already ran, so a new pass runs it no more.

    A web form file has no message id for the router to spot a re-delivery by; the thread of the first run holds
    the submission, whether it is paused, stored or failed.
    """
    threads = {
        c.config["configurable"]["thread_id"]
        for c in checkpointer.list(None)
        if c.config["configurable"]["thread_id"].startswith(f"{WEB_FORM}-")
    }
    seen = set()
    for thread in threads:
        submission = graph.get_state({"configurable": {"thread_id": thread}}).values.get("submission") or {}
        submission_id = submission.get("submission_id")
        if isinstance(submission_id, str) and submission_id:  # a null, list or object id never matches a file
            seen.add(submission_id)

    def skip(path: Path) -> bool:
        if path.suffix.lower() != ".json":
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and isinstance(data.get("submission_id"), str) and data["submission_id"] in seen

    return skip


def build_cycle_graph(db_path, checkpointer, folders: Folders, client: ClientFactory, interrupt_after=None):
    """The `purchase_cycle` graph: orders -> quotes -> reconcile -> receipts -> sales, over the shared database."""

    def orders(state: CycleState, config: RunnableConfig) -> CycleState:
        folder = folders.orders
        if not folder.is_dir() or not any(p.is_file() for p in folder.iterdir()):
            return {"counts": {"orders": {"items": 0, "stored": 0, "paused": 0, "failed": 0}}}
        graphs = _order_graphs(db_path, checkpointer, client, folders.outbox)
        thread_id = config["configurable"]["thread_id"]
        skip = _seen_web_form(checkpointer, graphs[WEB_FORM])
        results = router.run_inbox(folder, graphs, db_path, run_id(thread_id), folders.outbox, skip)
        states = [r["state"] or {} for r in results]
        conn = db.connect(db_path)
        try:
            parked = {row[0] for row in conn.execute("SELECT thread_id FROM failures WHERE status = 'needs_review'")}
        finally:
            conn.close()
        counts = {
            "items": len(results),
            "stored": sum(1 for s in states if s.get("order_id") is not None),
            "paused": sum(1 for s in states if s.get("__interrupt__")),
            "failed": sum(
                1
                for r in results
                if r["error"] is not None
                or r["route"].kind == router.REJECTED
                or r["thread_id"] in parked
                or ((r["state"] or {}).get("errors") and (r["state"] or {}).get("order_id") is None)
            ),
        }
        return {"counts": {"orders": counts}}

    def quotes_stage(state: CycleState) -> CycleState:
        started = quotes.scan(db_path, checkpointer, folders.supplier_outbox)
        replies = quotes.receive(db_path, checkpointer, folders.replies)
        waiting = quotes.tick(db_path, checkpointer, folders.supplier_outbox)
        decided = quote_decision.decide(db_path, checkpointer, client(OFFER_EXTRACTION, []))
        counts = {
            "started": len(started),
            "accepted": sum(1 for r in replies if r["accepted"]),
            "waiting": len(waiting),
            "decided": len(decided["decided"]),
            "no_offers": len(decided["no_offers"]),
        }
        return {"counts": {"quotes": counts}}

    def reconcile(state: CycleState) -> CycleState:
        documents = []
        if folders.documents.is_dir():
            documents = reconciliation.scan(
                db_path, checkpointer, folders.documents, client(SUPPLIER_DOCUMENT_EXTRACTION, [])
            )
        drafted = claims.draft_claims(db_path, checkpointer, client(CLAIM_DRAFTING, []))
        counts = {"documents": len(documents), "new": sum(1 for d in documents if d["new"]), "claims": len(drafted)}
        return {"counts": {"reconcile": counts}}

    def receipts_stage(state: CycleState) -> CycleState:
        result = receipts.run_receipts(db_path, folders.delivery_notes, checkpointer)
        counts = {
            "received": len(result.get("receipts", [])),
            "served": len(result.get("served", [])),
            "issued": len(result.get("issued", [])),
        }
        return {"counts": {"receipts": counts}}

    def sales_stage(state: CycleState) -> CycleState:
        result = sales.run_sales(db_path, folders.invoices, folders.signed_notes, checkpointer)
        drafted = customer_responses.draft_responses(db_path, checkpointer, client(RESPONSE_DRAFTING, []))
        counts = {
            "invoiced": len(result.get("invoiced", [])),
            "signed_notes": len(result.get("linked", [])),
            "reconciled": len(result.get("reconciled", [])),
            "corrections": len(result.get("corrections", [])),
            "responses": len(drafted),
        }
        return {"counts": {"sales": counts}}

    nodes = {
        "orders": orders,
        "quotes": quotes_stage,
        "reconcile": reconcile,
        "receipts": receipts_stage,
        "sales": sales_stage,
    }
    builder = StateGraph(CycleState)
    for name in STAGES:
        builder.add_node(name, nodes[name])
    builder.add_edge(START, STAGES[0])
    for before, after in zip(STAGES, STAGES[1:], strict=False):
        builder.add_edge(before, after)
    builder.add_edge(STAGES[-1], END)
    return builder.compile(name="purchase_cycle", checkpointer=checkpointer, interrupt_after=interrupt_after)


def _last_run(checkpointer) -> int:
    numbers = [
        run_number(c.config["configurable"]["thread_id"])
        for c in checkpointer.list(None)
        if c.config["configurable"]["thread_id"].startswith(THREAD_PREFIX)
    ]
    return max(numbers, default=0)


def pending_decisions(db_path) -> list[dict]:
    """Every decision a person has still to take, read from the database, with the commands that take it.

    The orchestrator only lists them: each one is a module thread paused in the checkpoint file.
    """
    conn = db.connect(db_path)
    try:
        clarifications = db.pending_clarifications(conn)
        requests = conn.execute(
            "SELECT id FROM quote_requests WHERE status = ? ORDER BY id", (quote_decision.AWAITING,)
        )
        claim_invoices = conn.execute(
            "SELECT r.invoice_id FROM claims c JOIN reconciliations r ON r.id = c.reconciliation_id "
            "WHERE c.status = ? ORDER BY c.id",
            (claims.AWAITING,),
        )
        response_invoices = conn.execute(
            "SELECT r.invoice_id FROM customer_responses p JOIN sales_reconciliations r ON r.id = p.reconciliation_id "
            "WHERE p.status = ? ORDER BY p.id",
            (customer_responses.AWAITING,),
        )
        pending = [
            {
                "kind": "clarification",
                "reference": row["thread_id"],
                "thread_id": row["thread_id"],
                "commands": [
                    f"purchase-cycle clarify answer {row['thread_id']} --text ...",
                    f"purchase-cycle clarify close {row['thread_id']}",
                ],
            }
            for row in clarifications
        ]
        for (request_id,) in requests:
            ref = quotes.reference(request_id)
            pending.append(
                {
                    "kind": "quote_approval",
                    "reference": ref,
                    "thread_id": quote_decision.decision_thread_id(request_id),
                    "commands": [f"purchase-cycle quotes approve {ref}", f"purchase-cycle quotes reject {ref}"],
                }
            )
        for (invoice_id,) in claim_invoices:
            ref = reconciliation.invoice_reference(invoice_id)
            pending.append(
                {
                    "kind": "claim",
                    "reference": ref,
                    "thread_id": claims.claim_thread_id(invoice_id),
                    "commands": [f"purchase-cycle reconcile approve {ref}", f"purchase-cycle reconcile reject {ref}"],
                }
            )
        for (invoice_id,) in response_invoices:
            ref = f"SINV-{invoice_id:04d}"
            pending.append(
                {
                    "kind": "customer_response",
                    "reference": ref,
                    "thread_id": customer_responses.response_thread_id(conn, invoice_id),
                    "commands": [f"purchase-cycle sales approve {ref}", f"purchase-cycle sales reject {ref}"],
                }
            )
        return pending
    finally:
        conn.close()


def run_cycle(db_path, checkpoint_path, folders: Folders, client: ClientFactory, interrupt_after=None) -> dict:
    """Run one pass of the cycle, or resume the last one when it stopped before its end.

    Returns the thread id, whether the pass is a resumption, the per-stage counts and the pending decisions.
    """
    conn = db.connect(db_path)
    try:
        db.seed(conn)
    finally:
        conn.close()
    checkpointer = sqlite_checkpointer(checkpoint_path)
    try:
        graph = build_cycle_graph(db_path, checkpointer, folders, client, interrupt_after)
        last = _last_run(checkpointer)
        thread = {"configurable": {"thread_id": f"{THREAD_PREFIX}{last:04d}"}}
        resumed = bool(last and graph.get_state(thread).next)
        if resumed:
            graph.invoke(None, thread)
        else:
            thread = {"configurable": {"thread_id": f"{THREAD_PREFIX}{last + 1:04d}"}}
            graph.invoke({}, thread)
        values = graph.get_state(thread).values
        return {
            "thread_id": thread["configurable"]["thread_id"],
            "resumed": resumed,
            "counts": values.get("counts", {}),
            "pending": pending_decisions(db_path),
        }
    finally:
        checkpointer.conn.close()
