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
from pathlib import Path

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.clarification import (
    AMBIGUOUS,
    FILLER_WORDS,
    QUANTITY,
    UNKNOWN,
    InvalidQuestion,
    _words,
    candidate_search,
    check_question,
    line_doubts,
)
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.email_dataset import (
    BASE_DATE,
    LAYOUTS,
    _unit,
    check_rendered,
    email_key,
    load_texts,
    render_email,
    validate_email,
)
from purchase_cycle.evaluation.planning import OUT_OF_CATALOG_ITEMS, read_jsonl, write_jsonl
from purchase_cycle.evaluation.web_form_dataset import MAX_CHARS as MAX_FORM_CHARS
from purchase_cycle.quantities import NUMBER_WORDS, stated_numbers, supports_quantity
from purchase_cycle.web_form import match_key

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
MAX_ANSWER_CHARS = 600
MAX_QUESTION_CHARS = 1500

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


# ---------- validation ----------


def _skus(found: list[dict]) -> list[str]:
    return [c["sku"] for c in found]


def validate_line_text(line: dict, text: str, channel: str) -> list[str]:
    """Reasons one written doubtful or clear line breaks its plan, judged by the runtime doubt rules."""
    lid, kind, sku = line["line_id"], line["kind"], line["expected_sku"]
    text = (text or "").strip()
    if not text:
        return [f"{lid}: planned line text is missing"]
    if "\n" in text or "\t" in text:
        return [f"{lid}: line text must be one line"]
    if channel == WEB_FORM and len(text) > MAX_FORM_CHARS:
        return [f"{lid}: line text longer than {MAX_FORM_CHARS} characters"]
    errors = []
    if sku is not None and _skus(candidate_search(text, CATALOG, limit=len(CATALOG))) != [sku]:
        errors.append(f"{lid}: text does not name exactly {sku}")
    if kind == "unsupported" and stated_numbers(text):
        errors.append(f"{lid}: text states a number, so the quantity is supported")
    # The line as an ideal matcher or extractor returns it: the planned SKU, or none for a product doubt.
    ideal = {"product" if channel == WEB_FORM else "source_text": text, "sku": sku, "quantity": line["quantity"] or 1}
    types, candidates = line_doubts(ideal, CATALOG, channel)
    if types != EXPECTED_DOUBTS[kind]:
        errors.append(f"{lid}: the rules raise {types or 'no doubt'}, planned {EXPECTED_DOUBTS[kind] or 'no doubt'}")
    elif _skus(candidates) != line["candidates"]:
        errors.append(f"{lid}: candidates {_skus(candidates)} are not the planned {line['candidates']}")
    return errors


def _phase03_view(order: dict) -> dict:
    """The order as a phase 03 email plan, so its renderer and email checks apply unchanged."""
    lines = []
    for line in order["lines"]:
        trap = {"style": "plain" if line["expected_sku"] else "out_of_catalog"}
        if "items_requested" in line:
            trap["items_requested"] = line["items_requested"]
        lines.append(
            {
                "line_id": line["line_id"],
                "expected_sku": line["expected_sku"],
                "expected_quantity": line["quantity"] or 1,
                "unit_style": line["unit_style"],
                "trap": trap,
                "location": line["location"],
            }
        )
    return dict(order, lines=lines)


def validate_order(order: dict, text: dict) -> list[str]:
    """Reasons a written detection order is rejected before rendering; empty when it passes."""
    written = text.get("lines") or {}
    errors = []
    for line in order["lines"]:
        errors += validate_line_text(line, written.get(line["line_id"], ""), order["channel"])
    extra = sorted(set(written) - {line["line_id"] for line in order["lines"]})
    if extra:
        errors.append(f"written lines not in the plan: {', '.join(extra)}")
    if order["channel"] == EMAIL and not errors:
        errors += validate_email(_phase03_view(order), text)
    return errors


def order_key(order: dict, text: dict) -> str:
    if order["channel"] == EMAIL:
        return email_key(_phase03_view(order), text)
    return match_key(" ".join(text["lines"][line["line_id"]] for line in order["lines"]))


def build_detection(plan: list[dict], texts: dict[str, dict]) -> tuple[list[dict], dict[str, bytes], dict[str, list]]:
    """Validate every planned order and render the emails; return rows, `.eml` files and rejected ids."""
    rows, files, rejected, seen = [], {}, {}, {}
    for order in plan:
        text = texts.get(order["id"]) or {}
        errors = validate_order(order, text)
        if not errors:
            key = order_key(order, text)
            if key in seen:
                errors.append(f"duplicates {seen[key]} after normalisation")
            seen.setdefault(key, order["id"])
        if not errors and order["channel"] == EMAIL:
            data = render_email(_phase03_view(order), text)
            errors = check_rendered(_phase03_view(order), text, data)
            files[order["id"]] = data
        if errors:
            rejected[order["id"]] = errors
            continue
        lines = [dict(line, text=text["lines"][line["line_id"]].strip()) for line in order["lines"]]
        row = dict(order, lines=lines)
        if order["channel"] == EMAIL:
            row.update(subject=text["subject"], body=text["body"], file=f"emails/{order['id']}.eml")
        else:
            row["submission"] = {
                "submission_id": order["id"],
                "customer_code": order["customer_code"],
                "lines": [{"product": line["text"], "quantity": line["quantity"]} for line in lines],
            }
        rows.append(row)
    return rows, files, rejected


def mentioned_skus(text: str) -> set[str]:
    """Catalog products a text names by SKU code or full catalog name."""
    key = f" {match_key(text)} "
    return {p.sku for p in PRODUCTS if f" {match_key(p.sku)} " in key or f" {match_key(p.name)} " in key}


def runtime_doubts(case: dict, written: dict) -> list[dict]:
    """The doubt records `detect` would hand to the question and answer prompts."""
    return [
        {
            "line_id": doubt["line_id"],
            "text": written[str(doubt["line_id"])].strip(),
            "quantity": doubt["quantity"],
            "sku": doubt["expected_sku"],
            "types": doubt["types"],
            "candidates": [{"sku": sku, "name": PRODUCT_BY_SKU[sku].name} for sku in doubt["candidates"]],
        }
        for doubt in case["doubts"]
    ]


def validate_case(case: dict, text: dict) -> list[str]:
    """Reasons a written answer case is rejected; empty when it passes."""
    written = text.get("lines") or {}
    errors = []
    for doubt in case["doubts"]:
        line = dict(doubt, line_id=f"line {doubt['line_id']}")
        errors += validate_line_text(line, written.get(str(doubt["line_id"]), ""), case["channel"])
    question, answer = (text.get("question") or "").strip(), (text.get("answer") or "").strip()
    if not question or len(question) > MAX_QUESTION_CHARS:
        errors.append(f"question must have 1 to {MAX_QUESTION_CHARS} characters")
    if not answer or len(answer) > MAX_ANSWER_CHARS:
        errors.append(f"answer must have 1 to {MAX_ANSWER_CHARS} characters")
    if errors:
        return errors
    try:
        check_question(question, runtime_doubts(case, written))
    except InvalidQuestion as error:
        errors.append(str(error))
    named = mentioned_skus(answer)
    # One answer covers every doubtful line, so it may name any line's candidates or planned product.
    allowed = {sku for d in case["doubts"] for sku in [*d["candidates"], d["expected_sku"]] if sku}
    for doubt in case["doubts"]:
        expected, lid = doubt["expected"], doubt["line_id"]
        if doubt["kind"] == "ambiguous" and expected["action"] == "set":
            outside = sorted(named - allowed)
            if outside:
                errors.append(f"line {lid}: answer names {', '.join(outside)} outside the candidates")
            if case["category"] == "pick_description" and expected["sku"] in named:
                errors.append(f"line {lid}: pick_description answer names the chosen product")
        elif expected["action"] == "set":
            sale_unit = PRODUCT_BY_SKU[expected["sku"]].sale_unit
            if not supports_quantity(answer, expected["quantity"], sale_unit):
                errors.append(f"line {lid}: answer does not state the quantity {expected['quantity']}")
        elif expected["action"] == "unclear" and named & set(doubt["candidates"]):
            errors.append(f"line {lid}: still_unclear answer names a candidate")
    return errors


def build_answers(plan: list[dict], texts: dict[str, dict]) -> tuple[list[dict], dict, dict[str, list]]:
    """Validate every planned case; return rows, no files and rejected ids with reasons."""
    rows, rejected, seen = [], {}, {}
    for case in plan:
        text = texts.get(case["id"]) or {}
        errors = validate_case(case, text)
        if not errors:
            key = match_key(text["answer"])
            if key in seen:
                errors.append(f"answer duplicates {seen[key]} after normalisation")
            seen.setdefault(key, case["id"])
        if errors:
            rejected[case["id"]] = errors
            continue
        written = text["lines"]
        doubts = [dict(d, text=written[str(d["line_id"])].strip()) for d in case["doubts"]]
        rows.append(dict(case, doubts=doubts, question=text["question"].strip(), answer=text["answer"].strip()))
    return rows, {}, rejected


def build_files(name: str, directory: Path) -> dict[str, list]:
    """Build one dataset from `plan.jsonl` and `texts/` in its folder; write it only when nothing is rejected."""
    plan = read_jsonl(directory / "plan.jsonl")
    rows, files, rejected = BUILDERS[name](plan, load_texts(directory / "texts"))
    if rejected:
        return rejected
    write_jsonl(directory / "dataset.jsonl", rows)
    if files:
        (directory / "emails").mkdir(parents=True, exist_ok=True)
        for item_id, data in files.items():
            (directory / "emails" / f"{item_id}.eml").write_bytes(data)
    return {}


BUILDERS = {"detection": build_detection, "answers": build_answers}


def cmd_check(args) -> int:
    _, _, directory = PLANNERS[args.dataset]
    plan = read_jsonl(directory / "plan.jsonl")
    batch = read_jsonl(Path(args.batch))
    ids = {row["id"] for row in batch}
    _, _, rejected = BUILDERS[args.dataset]([item for item in plan if item["id"] in ids], {r["id"]: r for r in batch})
    unknown = sorted(ids - {item["id"] for item in plan})
    for item_id in unknown:
        print(f"{item_id}: not in the plan")
    for item_id, errors in rejected.items():
        print(f"{item_id}: {'; '.join(errors)}")
    print(f"{len(batch)} items checked, {len(rejected) + len(unknown)} rejected")
    return 1 if rejected or unknown else 0


def cmd_build(args) -> int:
    _, _, directory = PLANNERS[args.dataset]
    rejected = build_files(args.dataset, directory)
    for item_id, errors in rejected.items():
        print(f"{item_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} items rejected; rewrite their texts and build again")
        return 1
    print(f"Built {len(read_jsonl(directory / 'dataset.jsonl'))} items -> {directory / 'dataset.jsonl'}")
    return 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("clarification-dataset", help="plan, check and build the phase 04 datasets")
    dsub = dataset.add_subparsers(dest="clarification_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded plan of one dataset")
    plan.add_argument("--dataset", choices=sorted(PLANNERS), required=True)
    plan.add_argument("--seed", type=int, default=None)
    plan.set_defaults(handler=cmd_plan)
    check = dsub.add_parser("check", help="validate a batch of written texts against the plan")
    check.add_argument("--dataset", choices=sorted(PLANNERS), required=True)
    check.add_argument("batch")
    check.set_defaults(handler=cmd_check)
    build = dsub.add_parser("build", help="validate every text and write the dataset")
    build.add_argument("--dataset", choices=sorted(PLANNERS), required=True)
    build.set_defaults(handler=cmd_build)
