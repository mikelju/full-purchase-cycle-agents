"""Seeded planner of the golden dataset.

The plan fixes category, expected SKU, quantity and trap of every case before
any sentence exists, so labels are decided by construction.
"""

import json
import random
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS

DATASET_VERSION = "1.0"
SEED = 20261006
CASES_PER_CATEGORY = 250
DEV_PER_CATEGORY = (63, 63, 63, 63, 62, 62, 62, 62)

CATEGORIES = (
    "exact_name",
    "synonym",
    "quantity_words",
    "unit_expression",
    "noise",
    "typo",
    "near_miss",
    "out_of_catalog",
)

GENERIC_QUANTITIES = list(range(1, 21)) * 3 + [24, 25, 30, 40, 50, 60, 75, 80, 100, 120, 150, 200]
WORD_QUANTITIES = list(range(1, 21)) * 2 + [
    25,
    30,
    35,
    40,
    45,
    50,
    60,
    70,
    75,
    80,
    90,
    100,
    120,
    150,
    200,
    250,
    300,
    500,
]

SYNONYM_STYLES = ("abbreviation", "everyday_name", "attribute_rewording", "brand_free_description")
NOISE_STYLES = ("greeting_and_signature", "delivery_details", "small_talk", "reference_numbers")
TYPO_STYLES = ("one_misspelled_word", "two_misspelled_words", "missing_letters", "swapped_letters")
UNIT_STYLES = (("items_to_packs", 8), ("dozen", 6), ("half_dozen", 3), ("couple", 2), ("single", 1))

# Products a customer could plausibly ask a medical distributor for that the catalog does not carry.
OUT_OF_CATALOG_ITEMS = (
    "hospital beds",
    "electric wheelchairs",
    "automated external defibrillators",
    "oxygen cylinders",
    "nebuliser machines",
    "X-ray film",
    "ultrasound gel 5 litre",
    "patient lifts",
    "infusion pumps",
    "dental implants",
    "hearing aid batteries",
    "contact lens solution",
    "baby formula",
    "pregnancy tests",
    "COVID antigen tests",
    "nitrile examination gloves, size XXL",
    "60 ml syringes",
    "ethyl alcohol 80%",
    "sterile gauze swabs 15 x 15 cm",
    "Foley catheters 22 Fr",
    "sunscreen SPF 15",
    "sharps containers 15 litres",
    "FFP1 masks",
    "crepe bandages 20 cm",
    "insulin syringes 2 ml",
    "surgical gloves size 9",
    "underpads 90 x 180 cm",
    "stair lifts",
    "massage tables",
    "scrubs uniforms",
)

PACK_SIZE = re.compile(r"\bof (\d+)\b")


def has_number(text: str, number: int) -> bool:
    """True when the number appears in the text as a standalone figure."""
    return re.search(rf"(?<![\d.,]){number}(?![\d]|[.,]\d)", text) is not None


def pack_size(sale_unit: str) -> int | None:
    match = PACK_SIZE.search(sale_unit)
    return int(match.group(1)) if match else None


def _siblings():
    by_family = defaultdict(list)
    for p in PRODUCTS:
        by_family[p.family].append(p)
    return by_family


def _trap(rng, category, product, siblings):
    if category == "synonym":
        return {"style": rng.choice(SYNONYM_STYLES)}, None
    if category == "noise":
        return {"style": rng.choice(NOISE_STYLES), "customer": rng.choice(CUSTOMERS).code}, None
    if category == "typo":
        return {"style": rng.choice(TYPO_STYLES)}, None
    if category == "quantity_words":
        return {"style": "quantity_in_words"}, rng.choice(WORD_QUANTITIES)
    if category == "near_miss":
        others = [s for s in siblings[product.family] if s.sku != product.sku]
        closest = max(others, key=lambda s: (SequenceMatcher(None, s.name, product.name).ratio(), s.sku))
        return {"style": "close_variant", "confusable_with": closest.sku}, None
    if category == "unit_expression":
        styles = [(s, w) for s, w in UNIT_STYLES if s != "items_to_packs" or (pack_size(product.sale_unit) or 0) >= 10]
        style = rng.choices([s for s, _ in styles], weights=[w for _, w in styles])[0]
        quantity = {
            "dozen": rng.choice([12, 24, 36, 48, 60]),
            "half_dozen": 6,
            "couple": 2,
            "single": 1,
        }.get(style) or rng.randint(1, 10)
        trap = {"style": style}
        if style == "items_to_packs":
            trap["items_requested"] = quantity * pack_size(product.sale_unit)
        return trap, quantity
    return {"style": "verbatim_catalog_name"}, None


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    products = sorted(PRODUCTS, key=lambda p: p.sku)
    siblings = _siblings()
    in_catalog = [c for c in CATEGORIES if c != "out_of_catalog"]

    # Balanced product pool: every product appears 5 or 6 times across the in-catalog cases.
    slots = CASES_PER_CATEGORY * len(in_catalog)
    pool = []
    while len(pool) < slots:
        batch = products[:]
        rng.shuffle(batch)
        pool.extend(batch)
    pool = pool[:slots]

    cases = []
    for c_index, category in enumerate(CATEGORIES):
        rows = []
        for i in range(CASES_PER_CATEGORY):
            if category == "out_of_catalog":
                item = OUT_OF_CATALOG_ITEMS[i % len(OUT_OF_CATALOG_ITEMS)]
                trap = {"style": "not_in_catalog", "requested_item": item}
                sku, quantity = None, None
            else:
                product = pool[in_catalog.index(category) * CASES_PER_CATEGORY + i]
                # Quantities that also appear in the product name or sale unit would blur the digit checks.
                for _attempt in range(20):
                    trap, quantity = _trap(rng, category, product, siblings)
                    quantity = quantity or rng.choice(GENERIC_QUANTITIES)
                    if not has_number(f"{product.name} {product.sale_unit}", quantity):
                        break
                sku = product.sku
            rows.append(
                {
                    "category": category,
                    "expected_sku": sku,
                    "expected_quantity": quantity or rng.choice(GENERIC_QUANTITIES),
                    "trap": trap,
                }
            )
        order = list(range(CASES_PER_CATEGORY))
        rng.shuffle(order)
        dev = set(order[: DEV_PER_CATEGORY[c_index]])
        for i, row in enumerate(rows):
            row["split"] = "dev" if i in dev else "test"
        cases.extend(rows)

    for n, case in enumerate(cases, start=1):
        case["id"] = f"OLX-{n:04d}"
        case["dataset_version"] = DATASET_VERSION
    return [
        {k: c[k] for k in ("id", "category", "split", "expected_sku", "expected_quantity", "trap", "dataset_version")}
        for c in cases
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
