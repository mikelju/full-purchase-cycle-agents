"""Golden datasets of phase 04: `clarification_detection` and `clarification_answers`.

The seeded planners fix every label before any text exists; the coding agent
writes the texts later, and `check` and `build` validate them with the runtime
doubt rules, the phase 02 text rules and the phase 03 email renderer.
"""

import random
from collections import Counter
from datetime import timedelta
from email.utils import format_datetime
from functools import cache
from itertools import combinations

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.clarification import AMBIGUOUS, FILLER_WORDS, QUANTITY, UNKNOWN, _words, candidate_search
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_dataset import BASE_DATE, LAYOUTS, _unit
from purchase_cycle.evaluation.planning import OUT_OF_CATALOG_ITEMS, write_jsonl
from purchase_cycle.quantities import NUMBER_WORDS

DATASET_VERSION = "1.0"
DETECTION_SEED = 20261009
ANSWERS_SEED = 20261010
WEB_FORM, EMAIL = "web_form", "email"
CHANNELS = (WEB_FORM, EMAIL)
DEV_SHARE = 0.25

# Detection: per channel, 100 orders, 32 of them with no doubt, and 35 doubtful lines per doubt type.
ORDERS_PER_CHANNEL = 100
NO_DOUBT_ORDERS = 32
DOUBT_KINDS = {
    WEB_FORM: (("ambiguous", 35), ("unknown", 35), ("over_ceiling", 35)),
    EMAIL: (("ambiguous", 35), ("unknown", 35), ("over_ceiling", 15), ("unsupported", 20)),
}
EXPECTED_DOUBTS = {
    "clear": [],
    "ambiguous": [AMBIGUOUS],
    "unknown": [UNKNOWN],
    "over_ceiling": [QUANTITY],
    "unsupported": [QUANTITY],
}
NO_DOUBT_SIZES = (1, 2, 2, 3, 3, 4, 5)
EXTRA_CLEAR_LINES = (0, 1, 1, 2, 2, 3)
OVER_CEILING_QUANTITIES = (501, 600, 750, 800, 900, 1000, 1200, 1500, 2000, 2500)
MAX_CONVERSATIONAL_LINES = 4  # phase 03 limit of conversational bodies

# Answers: six categories of 35 cases.
ANSWER_CATEGORIES = (
    "pick_variant",
    "pick_description",
    "give_quantity",
    "remove_line",
    "several_lines",
    "still_unclear",
)
CASES_PER_CATEGORY = 35
UNCLEAR_STYLES = ("unclear", "off_topic")

DETECTION_DIR = EVALS_DIR / "datasets" / "clarification_detection"
ANSWERS_DIR = EVALS_DIR / "datasets" / "clarification_answers"

CATALOG = [{"sku": p.sku, "name": p.name, "sale_unit": p.sale_unit} for p in PRODUCTS]
PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}
CUSTOMER_BY_CODE = {c.code: c for c in CUSTOMERS}


@cache
def _ambiguity_sets() -> tuple:
    """Candidate sets a short generic text reaches: one or two words of a catalog name giving 2 to 6 products.

    Each set is listed once, with the first words that reach it as a hint for the writer.
    """
    found = {}
    for product in sorted(PRODUCTS, key=lambda p: p.sku):
        words = sorted(w for w in _words(product.name) if w.isalpha() and len(w) > 1)
        words = [w for w in words if w not in FILLER_WORDS and w not in NUMBER_WORDS]
        for size in (1, 2):
            for combo in combinations(words, size):
                skus = tuple(c["sku"] for c in candidate_search(" ".join(combo), CATALOG, limit=len(CATALOG)))
                if 2 <= len(skus) <= 6:
                    found.setdefault(skus, " ".join(combo))
    return tuple(sorted(found.items()))


def ambiguity_sets() -> list[dict]:
    return [{"candidates": list(skus), "hint": hint} for skus, hint in _ambiguity_sets()]


def _least_used(rng, items, uses: Counter, key):
    choice = min(items, key=lambda item: (uses[key(item)], rng.random()))
    uses[key(choice)] += 1
    return choice


class _Picker:
    """Seeded choice of products, candidate sets and out-of-catalog items, spreading their use."""

    def __init__(self, rng):
        self.rng = rng
        self.uses = Counter()
        self.products = sorted(PRODUCTS, key=lambda p: p.sku)
        self.sets = ambiguity_sets()

    def product(self):
        return _least_used(self.rng, self.products, self.uses, lambda p: p.sku)

    def ambiguity(self) -> dict:
        return _least_used(self.rng, self.sets, self.uses, lambda s: "set:" + "|".join(s["candidates"]))

    def item(self) -> str:
        return _least_used(self.rng, OUT_OF_CATALOG_ITEMS, self.uses, lambda i: "item:" + i)


def _detection_line(rng, picker: _Picker, kind: str, channel: str) -> dict:
    """One planned line: its kind, expected doubts and what the writer must write."""
    line = {"kind": kind, "expected_doubts": list(EXPECTED_DOUBTS[kind]), "expected_sku": None, "candidates": []}
    if kind == "ambiguous":
        chosen = picker.ambiguity()
        line.update(candidates=chosen["candidates"], hint=chosen["hint"])
    elif kind == "unknown":
        line["requested_item"] = picker.item()
    else:
        line["expected_sku"] = picker.product().sku
    product = PRODUCT_BY_SKU.get(line["expected_sku"])
    if kind == "over_ceiling":
        unit, quantity, extra = "sale_units", rng.choice(OVER_CEILING_QUANTITIES), {}
    elif kind == "unsupported":
        unit, quantity, extra = "unstated", None, {}
    elif channel == EMAIL and kind in ("clear", "unknown"):
        unit, quantity, extra = _unit(rng, "written", product)
    else:
        unit, quantity, extra = "sale_units", rng.randint(1, 50), {}
    line["quantity"] = quantity
    if channel == EMAIL:
        line["unit_style"] = unit
        line.update(extra)
    return line


def _split(rng, groups: list[list[dict]]) -> None:
    """Mark about DEV_SHARE of every stratum as dev, after a seeded shuffle."""
    for group in groups:
        rng.shuffle(group)
        dev = round(DEV_SHARE * len(group))
        for i, item in enumerate(group):
            item["split"] = "dev" if i < dev else "test"


def build_detection_plan(seed: int = DETECTION_SEED) -> list[dict]:
    rng = random.Random(seed)
    picker = _Picker(rng)
    strata = []
    for channel in CHANNELS:
        kinds = [kind for kind, count in DOUBT_KINDS[channel] for _ in range(count)]
        rng.shuffle(kinds)
        doubt_orders = ORDERS_PER_CHANNEL - NO_DOUBT_ORDERS
        per_order = [[kind] for kind in kinds[:doubt_orders]]
        for i, kind in enumerate(kinds[doubt_orders:]):
            per_order[i].append(kind)
        clear, doubtful = [], []
        for _ in range(NO_DOUBT_ORDERS):
            clear.append(["clear"] * rng.choice(NO_DOUBT_SIZES))
        for order_kinds in per_order:
            doubtful.append(order_kinds + ["clear"] * rng.choice(EXTRA_CLEAR_LINES))
        for group in (clear, doubtful):
            orders = []
            for order_kinds in group:
                rng.shuffle(order_kinds)
                lines = [_detection_line(rng, picker, kind, channel) for kind in order_kinds]
                orders.append({"channel": channel, "has_doubt": group is doubtful, "lines": lines})
            strata.append(orders)
    _split(rng, strata)

    # Orders are numbered in a seeded order so ids reveal neither the channel nor the doubts.
    orders = [order for group in strata for order in group]
    rng.shuffle(orders)
    for number, order in enumerate(orders, start=1):
        customer = rng.choice(CUSTOMERS)
        order["id"] = f"CLD-{number:04d}"
        order["dataset_version"] = DATASET_VERSION
        order["customer_code"] = customer.code
        for n, line in enumerate(order["lines"], start=1):
            line["line_id"] = f"{order['id']}-L{n}"
        if order["channel"] == EMAIL:
            fits = len(order["lines"]) <= MAX_CONVERSATIONAL_LINES
            category = rng.choice(("body_list", "body_conversational")) if fits else "body_list"
            order.update(
                category=category,
                layout=rng.choice(LAYOUTS[category]),
                is_order=True,
                sender=customer.email,
                source="body",
                attachment=None,
                date=format_datetime(BASE_DATE + timedelta(hours=5 * number, minutes=17 * number % 60)),
            )
            for line in order["lines"]:
                line["location"] = "body"
    return orders


def _answer_doubt(rng, picker: _Picker, kind: str, action: str, channel: str) -> dict:
    """One doubtful line of an answer case with the resolution the answer must lead to."""
    doubt = _detection_line(rng, picker, kind, channel)
    del doubt["expected_doubts"]
    doubt["types"] = list(EXPECTED_DOUBTS[kind])
    if doubt["quantity"] is None:  # what the extractor read from a text stating no number
        doubt["quantity"] = 1
    if action == "set" and kind == "ambiguous":
        expected = {"action": "set", "sku": rng.choice(doubt["candidates"]), "quantity": doubt["quantity"]}
    elif action == "set":
        expected = {"action": "set", "sku": doubt["expected_sku"], "quantity": rng.randint(1, 120)}
    else:
        expected = {"action": action, "sku": None, "quantity": None}
    doubt["expected"] = expected
    return doubt


def _quantity_kind(rng, channel: str) -> str:
    return "over_ceiling" if channel == WEB_FORM else rng.choice(("over_ceiling", "unsupported"))


def _answer_doubts(rng, picker: _Picker, category: str, channel: str) -> tuple[list[dict], dict]:
    if category in ("pick_variant", "pick_description"):
        return [_answer_doubt(rng, picker, "ambiguous", "set", channel)], {}
    if category == "give_quantity":
        return [_answer_doubt(rng, picker, _quantity_kind(rng, channel), "set", channel)], {}
    if category == "remove_line":
        kind = rng.choice(("ambiguous", "unknown", _quantity_kind(rng, channel)))
        return [_answer_doubt(rng, picker, kind, "remove", channel)], {}
    if category == "several_lines":
        options = [("ambiguous", "set"), ("unknown", "remove"), ("quantity", "set"), ("ambiguous", "remove")]
        chosen = rng.sample(options, rng.choice((2, 2, 3)))
        doubts = [
            _answer_doubt(rng, picker, _quantity_kind(rng, channel) if kind == "quantity" else kind, action, channel)
            for kind, action in chosen
        ]
        return doubts, {}
    kinds = rng.sample(("ambiguous", "unknown", _quantity_kind(rng, channel)), rng.choice((1, 1, 2)))
    return [_answer_doubt(rng, picker, kind, "unclear", channel) for kind in kinds], {
        "answer_style": rng.choice(UNCLEAR_STYLES)
    }


def build_answers_plan(seed: int = ANSWERS_SEED) -> list[dict]:
    rng = random.Random(seed)
    picker = _Picker(rng)
    strata = []
    for category in ANSWER_CATEGORIES:
        group = []
        for i in range(CASES_PER_CATEGORY):
            channel = CHANNELS[i % 2]
            doubts, extra = _answer_doubts(rng, picker, category, channel)
            # Doubtful lines sit at seeded positions of an order of up to five lines.
            positions = sorted(rng.sample(range(1, 6), len(doubts)))
            for position, doubt in zip(positions, doubts, strict=True):
                doubt["line_id"] = position
            group.append({"category": category, "channel": channel, "doubts": doubts, **extra})
        strata.append(group)
    _split(rng, strata)
    cases = [case for group in strata for case in group]
    rng.shuffle(cases)
    for number, case in enumerate(cases, start=1):
        case["id"] = f"CLA-{number:04d}"
        case["dataset_version"] = DATASET_VERSION
        case["customer_code"] = rng.choice(CUSTOMERS).code
    return cases


PLANNERS = {"detection": (build_detection_plan, DETECTION_SEED, DETECTION_DIR)}
PLANNERS["answers"] = (build_answers_plan, ANSWERS_SEED, ANSWERS_DIR)


def cmd_plan(args) -> int:
    planner, default_seed, directory = PLANNERS[args.dataset]
    seed = default_seed if args.seed is None else args.seed
    plan = planner(seed)
    path = directory / "plan.jsonl"
    write_jsonl(path, plan)
    splits = Counter(item["split"] for item in plan)
    print(f"Planned {len(plan)} items ({splits['dev']} dev, {splits['test']} test) with seed {seed} -> {path}")
    return 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("clarification-dataset", help="plan, check and build the phase 04 datasets")
    dsub = dataset.add_subparsers(dest="clarification_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded plan of one dataset")
    plan.add_argument("--dataset", choices=sorted(PLANNERS), required=True)
    plan.add_argument("--seed", type=int, default=None)
    plan.set_defaults(handler=cmd_plan)
