"""Shared clarification step: doubt rules, candidate search and checks.

An order line raises a doubt when its text fits two or more catalog products
(ambiguous, even with a SKU), when it has no SKU and fits none (unknown), or
when its quantity is doubtful. The rules are deterministic and read only
the lines the channel already produced and the catalog.
"""

import re
from pathlib import Path
from typing import NamedTuple, TypedDict

from anthropic import APIError
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from purchase_cycle import db, llm
from purchase_cycle.llm import InvalidModelOutput, MissingRecording, ModelClient
from purchase_cycle.quantities import NUMBER_WORDS, supports_quantity
from purchase_cycle.web_form import match_key

MAX_LINE_QUANTITY = 500  # sale units per line
MAX_CANDIDATES = 6
MAX_ROUNDS = 2

AMBIGUOUS = "ambiguous"
UNKNOWN = "unknown"
QUANTITY = "quantity"
EMAIL = "email"
WHATSAPP = "whatsapp"
FREE_TEXT_CHANNELS = (EMAIL, WHATSAPP)  # channels whose quantity is checked against the line text

# Words that carry no product meaning in a line text; quantities and pack words included.
FILLER_WORDS = frozenset(
    "a an and any are as at be can could do for from i in is it me need of on or our over per please pls "
    "some send that the them these this to us want we with would you x "
    "box bag bottle carton case container dozen half pack packet pair piece pc pcs roll tube unit couple".split()
)


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("es") and word[-3] in "sx":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_singular(w) for w in match_key(text).split()}


# A figure is a whole or decimal number standing alone, so "7.5" is one figure, not 7 and 5.
FIGURE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\w|\.\d)")


def _figures(text: str) -> set[str]:
    return set(FIGURE.findall(text))


def candidate_search(text: str, catalog: list, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Catalog products the text may mean, best first; empty when nothing fits.

    A product is a candidate when its name holds every word of the text that
    is not a figure, a number word or a filler word. Among candidates, those
    sharing more figures with the text come first, and only the products with
    the fewest name words the text does not mention are kept, so a generic text
    returns the plain variants of one family rather than every related product.
    When the text shares a figure with them, a product whose name holds fewer
    figures the text does not state wins a remaining tie, so "size 8" picks
    size 8 over 8.5, while a size no sibling has keeps every sibling.
    """
    words = _words(text)
    required = {w for w in words if not w.isdigit() and w not in FILLER_WORDS and w not in NUMBER_WORDS}
    if not required:
        return []
    figures = _figures(text)
    scored = []
    for row in catalog:
        name = _words(row["name"])
        if required <= name:
            extra = len({w for w in name - words if not w.isdigit()})
            name_figures = _figures(row["name"])
            matched = len(figures & name_figures)
            unstated = len(name_figures - figures) if matched else 0
            scored.append((-matched, extra, unstated, row["sku"], row["name"]))
    if not scored:
        return []
    best = min(s[:3] for s in scored)
    return [{"sku": sku, "name": name} for *rank, sku, name in sorted(scored) if tuple(rank) == best][:limit]


def line_text(line: dict) -> str:
    """The line as the customer wrote it: the form product text or the email source text."""
    return line["source_text"] if "source_text" in line else line["product"]


def line_doubts(line: dict, catalog: list, channel: str) -> tuple[list[str], list[dict]]:
    """Doubt types of one channel line, in rule order, and the candidates of an ambiguous product."""
    text = line_text(line)
    types, candidates = [], []
    # Every line is searched, so a SKU given to a generic text still raises an ambiguous doubt.
    found = candidate_search(text, catalog)
    if len(found) >= 2:
        types.append(AMBIGUOUS)
        candidates = found
    elif not found and line["sku"] is None:
        types.append(UNKNOWN)
    quantity = line["quantity"]
    sale_unit = next((row["sale_unit"] for row in catalog if row["sku"] == line["sku"]), None)
    if quantity > MAX_LINE_QUANTITY or (
        channel in FREE_TEXT_CHANNELS and not supports_quantity(text, quantity, sale_unit)
    ):
        types.append(QUANTITY)
    return types, candidates


def detect(lines: list[dict], catalog: list, channel: str) -> list[dict]:
    """One doubt record per doubtful line; `line_id` is the line position, starting at 1."""
    doubts = []
    for line_id, line in enumerate(lines, start=1):
        types, candidates = line_doubts(line, catalog, channel)
        if types:
            doubts.append(
                {
                    "line_id": line_id,
                    "text": line_text(line),
                    "quantity": line["quantity"],
                    "sku": line["sku"],
                    "types": types,
                    "candidates": candidates,
                }
            )
    return doubts


DOUBT_LABELS = {AMBIGUOUS: "ambiguous product", UNKNOWN: "unknown product", QUANTITY: "doubtful quantity"}


class InvalidQuestion(ValueError):
    """The drafted question misses a doubtful line or a candidate; nothing is paused or written."""


class InvalidAnswer(ValueError):
    """The interpreted answer breaks a check; nothing is written."""


def doubts_message(doubts: list[dict]) -> str:
    """Fixed layout of the doubtful lines for the model, free of thread ids and timestamps."""
    out = ["Doubtful lines:"]
    for doubt in doubts:
        kinds = ", ".join(DOUBT_LABELS[t] for t in doubt["types"])
        current = f" | current SKU: {doubt['sku']}" if doubt["sku"] else ""
        out.append(
            f"Line {doubt['line_id']} | doubt: {kinds} | quantity read: {doubt['quantity']}{current}"
            f' | text: "{doubt["text"]}"'
        )
        out += [f"  candidate: {c['sku']} | {c['name']}" for c in doubt["candidates"]]
    return "\n".join(out)


def answer_message(doubts: list[dict], question: str, answer: str) -> str:
    """User message of the interpretation call: the doubts, the question sent and the customer answer."""
    return f"{doubts_message(doubts)}\n\nQuestion sent to the customer:\n{question}\n\nCustomer answer:\n{answer}"


# Typographic symbols the model writes, replaced by plain ASCII in the question the customer sees.
PLAIN_SYMBOLS = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def plain_question(question: str) -> str:
    """The question as printed, stored and sent: dashes, curly quotes and ellipses as plain ASCII."""
    return question.translate(PLAIN_SYMBOLS)


def check_question(question: str, doubts: list[dict]) -> None:
    """Every doubtful line's text and every candidate name must appear in the question."""
    key = f" {match_key(question)} "
    missing = []
    for doubt in doubts:
        if f" {match_key(doubt['text'])} " not in key:
            missing.append(f'line {doubt["line_id"]} text "{doubt["text"]}"')
        missing += [
            f'line {doubt["line_id"]} candidate "{c["name"]}"'
            for c in doubt["candidates"]
            if f" {match_key(c['name'])} " not in key
        ]
    if missing:
        raise InvalidQuestion("Clarification question rejected: it does not name " + "; ".join(missing))


def check_resolutions(resolutions: list, doubts: list[dict], catalog: list) -> None:
    """Every doubtful line answered exactly once, every set SKU in the catalog, every set quantity positive.

    The answer meets the order controls too: a set quantity is at most MAX_LINE_QUANTITY and,
    on an ambiguous line, the set SKU is one of the candidates offered for that line.
    """
    expected = {doubt["line_id"] for doubt in doubts}
    offered = {d["line_id"]: {c["sku"] for c in d["candidates"]} for d in doubts if AMBIGUOUS in d["types"]}
    known_skus = {row["sku"] for row in catalog}
    errors = []
    seen = set()
    for r in resolutions:
        if r.line_id not in expected:
            errors.append(f"line {r.line_id} is not a doubtful line")
        elif r.line_id in seen:
            errors.append(f"line {r.line_id} is answered more than once")
        seen.add(r.line_id)
        if r.action == "set":
            if r.sku not in known_skus:
                errors.append(f"line {r.line_id}: SKU '{r.sku}' is not in the catalog")
            elif r.line_id in offered and r.sku not in offered[r.line_id]:
                errors.append(f"line {r.line_id}: SKU '{r.sku}' is not one of the offered candidates")
            if r.quantity is None or r.quantity <= 0:
                errors.append(f"line {r.line_id}: quantity {r.quantity} is not a positive whole number")
            elif r.quantity > MAX_LINE_QUANTITY:
                errors.append(f"line {r.line_id}: quantity is above the limit of {MAX_LINE_QUANTITY}")
    errors += [f"line {line_id} is not answered" for line_id in sorted(expected - seen)]
    if errors:
        raise InvalidAnswer("Clarification answer rejected: " + "; ".join(errors))


# Failures of one model step that leave the thread waiting; any other error is a defect and stops the run.
STEP_FAILURES = (MissingRecording, InvalidModelOutput, InvalidQuestion, InvalidAnswer, APIError)


class ClarificationClients(NamedTuple):
    """Model clients of the clarify step: one drafts the question, one reads the answer."""

    question: ModelClient
    answer: ModelClient


class ClarificationState(TypedDict, total=False):
    lines: list[dict]
    customer: dict
    doubts: list[dict]
    question: str
    round: int
    answer: str
    closed: bool
    resolutions: list[dict]
    removed: list[dict]
    unresolved: list[dict]
    clarification: str | None
    rejected: str | None
    before_answer: dict


def settle(lines: list[dict], resolutions: list[dict], open_doubts: list[dict]) -> tuple[list, list, list]:
    """Lines to store with the resolutions applied, removed lines and unresolved lines, in line order."""
    latest = {r["line_id"]: r for r in resolutions}
    open_ids = {d["line_id"] for d in open_doubts}
    kept, removed, unresolved = [], [], []
    for line_id, line in enumerate(lines, start=1):
        resolution = latest.get(line_id)
        listed = {"text": line_text(line), "quantity": line["quantity"]}
        if line_id in open_ids:
            unresolved.append(listed)
        elif resolution and resolution["action"] == "remove":
            removed.append(listed)
        elif resolution and resolution["action"] == "set":
            kept.append({**line, "sku": resolution["sku"], "quantity": resolution["quantity"]})
        else:
            kept.append(line)
    return kept, removed, unresolved


def build_clarification_graph(clients: ClarificationClients, db_path: Path | str, channel: str):
    """Shared subgraph: detect -> ask -> wait (pause) -> interpret -> detect, at most MAX_ROUNDS questions.

    It leaves through `detect` when no doubt is open, the rounds are spent or the
    thread was closed; then `lines` holds only the lines to store, and `removed`
    and `unresolved` list the others with the text the customer wrote.
    """
    conn = db.connect(db_path)
    try:
        catalog = db.catalog_rows(conn)
    finally:
        conn.close()
    retry = llm.MODEL_RETRY

    def retry_left(error: Exception, runtime: Runtime) -> bool:
        # A transient error before the last attempt goes to the retry policy; the last one takes the fallback.
        return retry.retry_on(error) and runtime.execution_info.node_attempt < retry.max_attempts

    def detect_step(state: ClarificationState) -> ClarificationState:
        resolutions = state.get("resolutions", [])
        settled = {r["line_id"] for r in resolutions if r["action"] != "unclear"}
        doubts = [d for d in detect(state["lines"], catalog, channel) if d["line_id"] not in settled]
        rounds = state.get("round", 0)
        if doubts and rounds < MAX_ROUNDS and not state.get("closed"):
            return {"doubts": doubts}
        kept, removed, unresolved = settle(state["lines"], resolutions, doubts)
        status = None if not rounds else "closed" if state.get("closed") else "answered"
        return {
            "doubts": doubts,
            "lines": kept,
            "removed": removed,
            "unresolved": unresolved,
            "resolutions": resolutions,
            "clarification": status,
        }

    def ask(state: ClarificationState, config: RunnableConfig, runtime: Runtime) -> ClarificationState:
        thread_id = config["configurable"]["thread_id"]
        rounds = state.get("round", 0) + 1
        try:
            drafted = clients.question.extract(
                doubts_message(state["doubts"]), case_id=f"{thread_id}-question-{rounds}"
            )
            check_question(drafted.question, state["doubts"])
        except STEP_FAILURES as error:
            if rounds == 1 or retry_left(error, runtime):
                raise  # a retry, or round 1 with nothing paused yet, so the run stops with nothing written
            # The answer that led here is undone and the previous question waits again, so the thread
            # stays answerable and closable instead of resting on a question never sent.
            return {
                **state.get("before_answer", {}),
                "rejected": f"Clarification answer not applied, the next question could not be drafted: {error}",
            }
        conn = db.connect(db_path)
        try:
            db.save_clarification(
                conn, thread_id, channel, state["customer"]["code"], plain_question(drafted.question), rounds
            )
        finally:
            conn.close()
        return {"question": drafted.question, "round": rounds}

    def wait(state: ClarificationState) -> ClarificationState:
        # Resumed with {"answer": text} or {"close": True}; the question was drafted before the pause.
        # The state keeps the drafted question as the model wrote it, so the interpretation message and its
        # recordings stay the same; the customer sees the plain version.
        # `rejected` holds why the previous answer failed its checks; the same question waits again.
        received = interrupt(
            {
                "question": plain_question(state["question"]),
                "round": state["round"],
                "doubts": state["doubts"],
                "rejected": state.get("rejected"),
            }
        )
        if received.get("close"):
            return {"closed": True, "rejected": None}
        return {"answer": received["answer"], "rejected": None}

    def interpret(state: ClarificationState, config: RunnableConfig, runtime: Runtime) -> ClarificationState:
        if state.get("closed"):
            return {}
        thread_id = config["configurable"]["thread_id"]
        message = answer_message(state["doubts"], state["question"], state["answer"])
        try:
            read = clients.answer.extract(message, case_id=f"{thread_id}-answer-{state['round']}")
            check_resolutions(read.resolutions, state["doubts"], catalog)
        except STEP_FAILURES as error:
            if retry_left(error, runtime):
                raise
            # A rejected answer, an invalid model output, a missing recording or a model error writes nothing;
            # the run stops paused on the same question, so the thread stays answerable and closable.
            reason = str(error) if isinstance(error, InvalidAnswer) else f"Clarification answer not read: {error}"
            return {"rejected": reason}
        resolutions = state.get("resolutions", [])
        return {
            "resolutions": resolutions + [r.model_dump() for r in read.resolutions],
            "before_answer": {"doubts": state["doubts"], "resolutions": resolutions},
        }

    builder = StateGraph(ClarificationState)
    builder.add_node("detect", detect_step)
    builder.add_node("ask", ask, retry_policy=retry)
    builder.add_node("wait", wait)
    builder.add_node("interpret", interpret, retry_policy=retry)
    builder.add_edge(START, "detect")
    builder.add_conditional_edges("detect", lambda s: "ask" if "unresolved" not in s else END, ["ask", END])
    builder.add_edge("ask", "wait")
    builder.add_edge("wait", "interpret")
    builder.add_conditional_edges("interpret", lambda s: "wait" if s.get("rejected") else "detect", ["wait", "detect"])
    return builder.compile(name="clarification")
