"""Shared clarification step: doubt rules, candidate search and checks.

An order line raises a doubt when it has no SKU (ambiguous or unknown product)
or when its quantity is doubtful. The rules are deterministic and read only
the lines the channel already produced and the catalog.
"""

from purchase_cycle.quantities import NUMBER_WORDS, supports_quantity
from purchase_cycle.web_form import match_key

MAX_LINE_QUANTITY = 500  # sale units per line
MAX_CANDIDATES = 6
MAX_ROUNDS = 2

AMBIGUOUS = "ambiguous"
UNKNOWN = "unknown"
QUANTITY = "quantity"
EMAIL = "email"

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


def candidate_search(text: str, catalog: list, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Catalog products the text may mean, best first; empty when nothing fits.

    A product is a candidate when its name holds every word of the text that
    is not a figure, a number word or a filler word. Among candidates, those
    sharing more figures with the text come first, and only the products with
    the fewest name words the text does not mention are kept, so a generic text
    returns the plain variants of one family rather than every related product.
    """
    words = _words(text)
    required = {w for w in words if not w.isdigit() and w not in FILLER_WORDS and w not in NUMBER_WORDS}
    if not required:
        return []
    figures = {w for w in words if w.isdigit()}
    scored = []
    for row in catalog:
        name = _words(row["name"])
        if required <= name:
            extra = len({w for w in name - words if not w.isdigit()})
            scored.append((-len(figures & name), extra, row["sku"], row["name"]))
    if not scored:
        return []
    best = min(s[:2] for s in scored)
    return [{"sku": sku, "name": name} for *rank, sku, name in sorted(scored) if tuple(rank) == best][:limit]


def line_text(line: dict) -> str:
    """The line as the customer wrote it: the form product text or the email source text."""
    return line["source_text"] if "source_text" in line else line["product"]


def line_doubts(line: dict, catalog: list, channel: str) -> tuple[list[str], list[dict]]:
    """Doubt types of one channel line, in rule order, and the candidates of an ambiguous product."""
    text = line_text(line)
    types, candidates = [], []
    if line["sku"] is None:
        found = candidate_search(text, catalog)
        if len(found) >= 2:
            types.append(AMBIGUOUS)
            candidates = found
        elif not found:
            types.append(UNKNOWN)
    quantity = line["quantity"]
    sale_unit = next((row["sale_unit"] for row in catalog if row["sku"] == line["sku"]), None)
    if quantity > MAX_LINE_QUANTITY or (channel == EMAIL and not supports_quantity(text, quantity, sale_unit)):
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
    """Every doubtful line answered exactly once, every set SKU in the catalog, every set quantity positive."""
    expected = {doubt["line_id"] for doubt in doubts}
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
            if r.quantity is None or r.quantity <= 0:
                errors.append(f"line {r.line_id}: quantity {r.quantity} is not a positive whole number")
    errors += [f"line {line_id} is not answered" for line_id in sorted(expected - seen)]
    if errors:
        raise InvalidAnswer("Clarification answer rejected: " + "; ".join(errors))
