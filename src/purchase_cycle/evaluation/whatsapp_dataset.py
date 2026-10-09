"""Golden dataset of the WhatsApp order evaluation: seeded plan, validation, rendering and build.

The seeded plan fixes, before any text exists, the category, sender, order or
not, and every line with its expected SKU (or out-of-catalog item), expected
quantity in sale units, unit expression and trap, so labels are decided by
construction. The coding agent writes the message texts; this module validates
them and renders deterministic WhatsApp message files.
"""

import random
from collections import Counter, defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.planning import (
    GENERIC_QUANTITIES,
    OUT_OF_CATALOG_ITEMS,
    WORD_QUANTITIES,
    write_jsonl,
)
from purchase_cycle.quantities import pack_size

DATASET_VERSION = "1.0"
SEED = 20261009
MESSAGES_PER_CATEGORY = 32
DEV_PER_CATEGORY = 8  # 25% development, 75% test

CATEGORIES = (
    "short_list",
    "chatty_single_line",
    "one_sentence_lines",
    "words_or_dozens",
    "not_an_order",
)
ORDER_CATEGORIES = CATEGORIES[:-1]
# Line counts cycled over the messages of a category, so the totals do not depend on the seed.
LINE_COUNTS = {
    "short_list": (2, 3, 4, 5, 6, 7),
    "chatty_single_line": (1,),
    "one_sentence_lines": (2, 3, 4),
    "words_or_dozens": (1, 2, 3, 4),
}
# Fixed share of the lines of every order category; the rest are plain lines.
TRAP_SHARES = (("out_of_catalog", 0.10), ("typo", 0.10), ("near_miss", 0.10))
WORD_STYLES = ("quantity_words", "dozen", "half_dozen", "couple")
UNIT_WEIGHTS = {
    "short_list": (("sale_units", 6), ("items_to_packs", 1)),
    "chatty_single_line": (("sale_units", 2), ("quantity_words", 1), ("items_to_packs", 1), ("couple", 1)),
    "one_sentence_lines": (("sale_units", 3), ("quantity_words", 1)),
    "words_or_dozens": (("quantity_words", 3), ("dozen", 2), ("half_dozen", 1), ("couple", 1)),
}
NOT_ORDER_KINDS = ("question", "greeting", "complaint")
SOURCE = "message"  # the only line source of a WhatsApp message
BASE_DATE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)

DATASET_DIR = EVALS_DIR / "datasets" / "whatsapp_order_extraction"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
TEXTS_DIR = DATASET_DIR / "texts"
MESSAGES_DIR = DATASET_DIR / "messages"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}
CUSTOMER_BY_CODE = {c.code: c for c in CUSTOMERS}


# ---------- planning ----------


def _unit(rng, category: str, product) -> tuple[str, int, dict]:
    """Unit expression, expected quantity in sale units and extra trap fields."""
    pack = pack_size(product.sale_unit) if product else None
    styles = [(s, w) for s, w in UNIT_WEIGHTS[category] if s != "items_to_packs" or (pack or 0) >= 10]
    if product is None:  # out-of-catalog lines keep the quantity simple
        styles = [(s, w) for s, w in styles if s in ("sale_units", "quantity_words")] or [("quantity_words", 1)]
    style = rng.choices([s for s, _ in styles], weights=[w for _, w in styles])[0]
    if style == "quantity_words":
        return style, rng.choice(WORD_QUANTITIES), {}
    if style == "items_to_packs":
        quantity = rng.randint(1, 10)
        return style, quantity, {"items_requested": quantity * pack}
    if style == "dozen":
        return style, rng.choice([12, 24, 36, 48]), {}
    if style == "half_dozen":
        return style, 6, {}
    if style == "couple":
        return style, 2, {}
    return style, rng.choice(GENERIC_QUANTITIES), {}


def _closest_sibling(product, siblings) -> str:
    others = [s for s in siblings[product.family] if s.sku != product.sku]
    return max(others, key=lambda s: (SequenceMatcher(None, s.name, product.name).ratio(), s.sku)).sku


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    products = sorted(PRODUCTS, key=lambda p: p.sku)
    siblings = defaultdict(list)
    for p in products:
        siblings[p.family].append(p)
    uses = Counter()
    messages = []
    for category in CATEGORIES:
        group = []
        if category == "not_an_order":
            kinds = [NOT_ORDER_KINDS[i % len(NOT_ORDER_KINDS)] for i in range(MESSAGES_PER_CATEGORY)]
            rng.shuffle(kinds)
            group = [{"category": category, "is_order": False, "kind": kind, "lines": []} for kind in kinds]
        else:
            counts = LINE_COUNTS[category]
            sizes = [counts[i % len(counts)] for i in range(MESSAGES_PER_CATEGORY)]
            rng.shuffle(sizes)
            total = sum(sizes)
            traps = []
            for trap, share in TRAP_SHARES:
                traps += [trap] * round(share * total)
            traps += ["plain"] * (total - len(traps))
            rng.shuffle(traps)
            for size in sizes:
                message_traps, traps = traps[:size], traps[size:]
                in_catalog = sum(t != "out_of_catalog" for t in message_traps)
                chosen = sorted(products, key=lambda p: (uses[p.sku], rng.random()))[:in_catalog]
                items = rng.sample(OUT_OF_CATALOG_ITEMS, size - in_catalog)
                lines = []
                for trap in message_traps:
                    product = None if trap == "out_of_catalog" else chosen.pop()
                    unit_style, quantity, extra = _unit(rng, category, product)
                    line = {
                        "expected_sku": product.sku if product else None,
                        "expected_quantity": quantity,
                        "unit_style": unit_style,
                        "trap": {"style": trap, **extra},
                    }
                    if product:
                        uses[product.sku] += 1
                        line["sale_unit"] = product.sale_unit
                    else:
                        line["trap"]["requested_item"] = items.pop()
                    if trap == "near_miss":
                        line["trap"]["confusable_with"] = _closest_sibling(product, siblings)
                    lines.append(line)
                group.append({"category": category, "is_order": True, "lines": lines})
        rng.shuffle(group)
        for i, message in enumerate(group):
            message["split"] = "dev" if i < DEV_PER_CATEGORY else "test"
        messages += group

    # Messages are numbered in a seeded order so ids do not reveal the category.
    rng.shuffle(messages)
    for number, message in enumerate(messages, start=1):
        customer = rng.choice(CUSTOMERS)
        message["id"] = f"WA-{number:04d}"
        message["dataset_version"] = DATASET_VERSION
        message["customer_code"] = customer.code
        message["sender"] = customer.phone
        message["message_id"] = f"wamid.dataset.{message['id']}"
        message["timestamp"] = str(int(BASE_DATE.timestamp()) + 3600 * 7 * number + 60 * (13 * number % 60))
        message["source"] = SOURCE
        for n, line in enumerate(message["lines"], start=1):
            line["line_id"] = f"{message['id']}-L{n}"
            line["location"] = SOURCE
    return messages


# ---------- commands ----------


def cmd_plan(args) -> int:
    plan = build_plan(args.seed)
    write_jsonl(PLAN_PATH, plan)
    lines = sum(len(m["lines"]) for m in plan)
    catalog = sum(1 for m in plan for line in m["lines"] if line["expected_sku"])
    print(f"Planned {len(plan)} messages with {lines} expected lines ({catalog} in the catalog), seed {args.seed}")
    print(f"  -> {PLAN_PATH}")
    return 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("whatsapp-dataset", help="plan, check and build the WhatsApp order extraction dataset")
    dsub = dataset.add_subparsers(dest="whatsapp_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded message plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_plan)
