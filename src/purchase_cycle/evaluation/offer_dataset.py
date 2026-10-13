"""Golden dataset of the `offer_extraction` evaluation: seeded plan, validation, build and second-pass review.

The seeded plan fixes, before any text exists, the request (real catalog SKU and quantity), the category and the
expected fields of every supplier reply, so the labels are decided by construction.
The coding agent writes the reply texts; this module checks that each text states what its label needs,
and joins plan and texts into the dataset.
The expected usability, reason and net price come from `quote_decision.assess`, the rule the product uses.
"""

import random
import re
from collections import Counter
from pathlib import Path

from purchase_cycle import llm, quote_decision
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.config import EVALS_DIR, ROOT
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.quantities import pack_size

DATASET_VERSION = "1.0"
SEED = 20261013
REPLIES_PER_CATEGORY = 12
DEV_PER_CATEGORY = 3  # 25% development, 75% test

CATEGORIES = (
    "plain_full",
    "price_basis",
    "partial_quantity",
    "decline",
    "missing_price",
    "other_currency",
    "vat",
    "lead_time",
    "price_tiers",
    "chatty",
)
QUANTITIES = (10, 12, 20, 24, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300, 500)
CALENDAR_DAYS = (2, 3, 4, 5, 6, 7, 10, 14)
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 10: "ten"}
VAT_RATES = (4, 10, 21)
FOREIGN_CURRENCIES = ("USD",) * 4 + ("GBP",) * 3 + ("CHF",) * 3 + ("SEK",) * 2
CURRENCY_WORDS = {
    "EUR": ("eur", "euro"),
    "USD": ("usd", "dollar"),
    "GBP": ("gbp", "pound"),
    "CHF": ("chf", "franc"),
    "SEK": ("sek", "krona", "kronor"),
}
PRICE_PATTERN = re.compile(r"\d+[.,]\d{2}\b")
MIN_CHARS, MAX_CHARS = 40, 1200
GRADED = ("offers", "quantity", "unit_price", "currency", "vat_included", "vat_rate", "lead_time_days", "working_days")
PRICE_TOLERANCE = 0.005  # half a cent

DATASET_DIR = EVALS_DIR / "datasets" / "offer_extraction"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
TEXTS_DIR = DATASET_DIR / "texts"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
REVIEW_PATH = DATASET_DIR / "second_pass_review.jsonl"
BLIND_DIR = ROOT / ".runtime" / "offer_second_pass"  # gitignored input of the blind annotator
REVIEW_DECISIONS = ("agree", "label_fixed", "annotator_wrong")

PRODUCTS_SORTED = sorted(PRODUCTS, key=lambda p: p.sku)


# ---------- planning ----------


def _price(rng, product) -> float:
    return max(0.5, round(product.price_eur * rng.uniform(0.85, 1.2), 2))


def _lead(rng, variant: str | None = None) -> tuple[int | None, bool | None, list[str]]:
    """Lead time in days, whether it counts working days, and the phrase the text must contain."""
    if variant in (None, "days"):
        days = rng.choice(CALENDAR_DAYS)
        return days, False, [f"{days} days"]
    if variant == "weeks_digits":
        weeks = rng.choice((1, 2, 3, 4, 6))
        return 7 * weeks, False, [f"{weeks} week" + ("s" if weeks > 1 else "")]
    if variant == "weeks_words":
        weeks = rng.choice((2, 3, 4, 6))
        return 7 * weeks, False, [f"{WORDS[weeks]} weeks"]
    if variant == "working_digits":
        days = rng.choice((3, 5, 7, 10))
        return days, True, [f"{days} working days"]
    if variant == "working_words":
        days = rng.choice((3, 4, 5, 7))
        return days, True, [f"{WORDS[days]} business days"]
    return None, None, []


def _fields(offers=True, quantity=None, unit_price=None, currency=None, vat=(False, None), lead=(None, None)):
    expected = {"offers": offers, "quantity": quantity, "unit_price": unit_price, "currency": currency}
    if unit_price is not None or currency is not None:
        expected |= {"vat_included": vat[0], "vat_rate": vat[1]}
    expected["lead_time_days"] = lead[0]
    if lead[0] is not None:
        expected["working_days"] = lead[1]
    return expected


def _reply(category, variant, product, requested, expected, must_contain, distractors=()) -> dict:
    full = dict.fromkeys(GRADED) | expected
    usable, reason, net = quote_decision.assess(full)
    return {
        "category": category,
        "variant": variant,
        "sku": product.sku,
        "product": product.name,
        "sale_unit": product.sale_unit,
        "requested_quantity": requested,
        "expected": expected,
        "expected_usable": usable,
        "expected_reason": reason,
        "expected_net_price": net,
        "must_contain": list(must_contain),
        "distractors": list(distractors),
    }


def _plain(rng, category, variant, product, requested, lead_variant=None, quantity=None) -> dict:
    price = _price(rng, product)
    lead = _lead(rng, lead_variant)
    expected = _fields(True, quantity or requested, price, "EUR", lead=lead[:2])
    return _reply(category, variant, product, requested, expected, [f"{price:.2f}", *lead[2]])


def _category_replies(rng, category: str) -> list[dict]:
    rows = []
    for n in range(REPLIES_PER_CATEGORY):
        requested = rng.choice(QUANTITIES)
        product = rng.choice(PRODUCTS_SORTED)
        if category == "plain_full":
            rows.append(_plain(rng, category, "full", product, requested))
        elif category == "price_basis":
            product = rng.choice([p for p in PRODUCTS_SORTED if (pack_size(p.sale_unit) or 0) >= 10])
            unit = _price(rng, product)
            lead = _lead(rng)
            if n < 6:
                pack = pack_size(product.sale_unit)
                piece = round(unit / pack, 3)
                unit = round(piece * pack, 2)
                expected = _fields(True, requested, unit, "EUR", lead=lead[:2])
                rows.append(_reply(category, "per_piece", product, requested, expected, [f"{piece:.3f}", *lead[2]]))
            else:
                size = rng.choice((5, 10, 12, 20))
                expected = _fields(True, requested, unit, "EUR", lead=lead[:2])
                facts = [f"{unit * size:.2f}", "case", *lead[2]]
                rows.append(_reply(category, "per_case", product, requested, expected, facts))
        elif category == "partial_quantity":
            requested = rng.choice([q for q in QUANTITIES if q >= 20])
            offered = max(1, round(requested * rng.choice((0.3, 0.4, 0.5, 0.6, 0.75))))
            row = _plain(rng, category, "partial", product, requested, quantity=offered)
            row["must_contain"].append(str(offered))
            rows.append(row)
        elif category == "decline":
            kind = ("out_of_stock", "discontinued", "not_supplied", "capacity")[n % 4]
            rows.append(_reply(category, kind, product, requested, _fields(False), []))
        elif category == "missing_price":
            lead = _lead(rng) if n % 2 else (None, None, [])
            kind = ("to_follow", "call_us", "catalog_attached", "needs_review")[n % 4]
            expected = _fields(True, requested, None, None, lead=lead[:2])
            rows.append(_reply(category, kind, product, requested, expected, lead[2]))
        elif category == "other_currency":
            currency = FOREIGN_CURRENCIES[n]
            price = round(_price(rng, product) * (11 if currency == "SEK" else 1), 2)
            lead = _lead(rng)
            expected = _fields(True, requested, price, currency, lead=lead[:2])
            rows.append(_reply(category, currency, product, requested, expected, [f"{price:.2f}", currency, *lead[2]]))
        elif category == "vat":
            price, lead, rate = _price(rng, product), _lead(rng), rng.choice(VAT_RATES)
            kind = "included_rate" if n < 5 else "excluded_rate" if n < 9 else "included_no_rate"
            vat = (kind != "excluded_rate", None if kind == "included_no_rate" else rate)
            expected = _fields(True, requested, price, "EUR", vat=vat, lead=lead[:2])
            facts = [f"{price:.2f}", "VAT", *lead[2]] + ([] if vat[1] is None else [str(rate)])
            rows.append(_reply(category, kind, product, requested, expected, facts))
        elif category == "lead_time":
            kinds = ("weeks_digits",) * 3 + ("weeks_words",) * 2 + ("working_digits",) * 3
            kind = (*kinds, "working_words", "working_words", "none", "none")[n]
            rows.append(_plain(rng, category, kind, product, requested, lead_variant=kind))
        elif category == "price_tiers":
            first, second = rng.choice(((50, 200), (100, 500), (25, 100)))
            tier = n % 3
            quantity = (
                rng.randint(first // 2, first - 1),
                rng.randint(first, second - 1),
                second + rng.randint(0, 100),
            )[tier]
            base = _price(rng, product)
            prices = (base, round(base * 0.94, 2), round(base * 0.88, 2))
            lead = _lead(rng)
            expected = _fields(True, quantity, prices[tier], "EUR", lead=lead[:2])
            facts = [*(f"{p:.2f}" for p in prices), str(first), str(second), *lead[2]]
            rows.append(_reply(category, f"tier_{tier + 1}", product, quantity, expected, facts))
        else:  # chatty
            row = _plain(rng, category, "chatty", product, requested)
            options = (
                f"+34 91{rng.randint(2, 9)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
                f"ORD-{rng.randint(10000, 99999)}",
                f"2026-{rng.randint(8, 10):02d}-{rng.randint(10, 28)}",
                f"INV-{rng.randint(1000, 9999)}",
                f"{rng.randint(10, 52):d}000 {rng.choice(('Madrid', 'Bilbao', 'Valencia', 'Sevilla'))}",
            )
            row["distractors"] = rng.sample(options, 3)
            rows.append(row)
    return rows


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    plan = []
    for category in CATEGORIES:
        rows = _category_replies(rng, category)
        dev = set(rng.sample(range(len(rows)), DEV_PER_CATEGORY))
        plan += [dict(row, split="dev" if n in dev else "test") for n, row in enumerate(rows)]
    for n, row in enumerate(plan, start=1):
        row["id"] = f"OFF-{n:03d}"
    return plan


# ---------- validation and build ----------


def validate_reply(planned: dict, text: dict) -> list[str]:
    errors = []
    body = text.get("body")
    if text.get("id") != planned["id"]:
        errors.append(f"text id {text.get('id')!r} does not match the plan id")
    if not isinstance(body, str):
        return [*errors, "no body"]
    if not body.isascii():
        errors.append("body is not ASCII")
    if not MIN_CHARS <= len(body) <= MAX_CHARS:
        errors.append(f"body has {len(body)} characters, expected {MIN_CHARS} to {MAX_CHARS}")
    low = body.lower()
    for fact in planned["must_contain"]:
        if fact.lower() not in low:
            errors.append(f"body must contain {fact!r}")
    for item in planned["distractors"]:
        if item not in body:
            errors.append(f"body must contain the unrelated number {item!r}")
    expected = planned["expected"]
    if expected.get("unit_price") is not None and not any(w in low for w in CURRENCY_WORDS[expected["currency"]]):
        errors.append(f"body does not name the currency {expected['currency']}")
    if planned["category"] in ("decline", "missing_price") and PRICE_PATTERN.search(body):
        errors.append("body states a price although the label has none")
    if planned["variant"] == "none" and re.search(r"\bdays?\b|\bweeks?\b", low):
        errors.append("body states a lead time although the label has none")
    return errors


def load_texts(directory: Path | None = None) -> dict[str, dict]:
    texts = {}
    for path in sorted((directory or TEXTS_DIR).glob("*.jsonl")):
        for row in read_jsonl(path):
            texts[row["id"]] = row
    return texts


def build_dataset(plan: list[dict], texts: dict[str, dict]) -> tuple[list[dict], dict[str, list[str]]]:
    rows, rejected, seen = [], {}, {}
    for planned in plan:
        text = texts.get(planned["id"]) or {}
        errors = validate_reply(planned, text)
        if not errors:
            key = re.sub(r"\W+", " ", text["body"].lower()).strip()
            if key in seen:
                errors.append(f"duplicates {seen[key]}")
            seen.setdefault(key, planned["id"])
        if errors:
            rejected[planned["id"]] = errors
            continue
        rows.append(dict(planned, body=text["body"], dataset_version=DATASET_VERSION))
    return rows, rejected


def load_dataset(path: Path | None = None) -> list[dict]:
    return read_jsonl(path or DATASET_PATH)


def request_for(row: dict) -> dict:
    """The request the model is told about; the numeric part of the id stands for the request id."""
    return {
        "id": int(row["id"].split("-")[1]),
        "sku": row["sku"],
        "product": row["product"],
        "sale_unit": row["sale_unit"],
        "quantity": row["requested_quantity"],
    }


def model_text(row: dict) -> str:
    return quote_decision.model_text(request_for(row), row["body"])


# ---------- second-pass review ----------


def _same(field: str, label, read) -> bool:
    if field in ("unit_price", "vat_rate") and label is not None and read is not None:
        return abs(label - read) <= PRICE_TOLERANCE
    return label == read


def review_disagreements(row: dict, read: dict) -> list[dict]:
    """Differences between the labelled fields of a reply and a blind annotator's reading of it."""
    return [
        {"field": field, "label": label, "read": read.get(field)}
        for field, label in row["expected"].items()
        if not _same(field, label, read.get(field))
    ]


def build_review(dataset: list[dict], readings: list[dict], previous: list[dict]) -> list[dict]:
    by_id, kept = {r["id"]: r for r in readings}, {r["id"]: r for r in previous}
    records = []
    for row in dataset:
        read = by_id[row["id"]]
        found = review_disagreements(row, read)
        old = kept.get(row["id"], {})
        records.append(
            {
                "id": row["id"],
                "read": {k: read.get(k) for k in GRADED},
                "notes": read.get("notes", ""),
                "disagreements": found,
                "decision": old.get("decision", "pending") if found else "agree",
                "justification": old.get("justification", "") if found else "",
            }
        )
    return records


# ---------- commands ----------


def cmd_plan(args) -> int:
    plan = build_plan(args.seed)
    write_jsonl(PLAN_PATH, plan)
    usable = sum(r["expected_usable"] for r in plan)
    print(f"Planned {len(plan)} replies with seed {args.seed} ({usable} usable) -> {PLAN_PATH}")
    return 0


def cmd_check(args) -> int:
    plan = {r["id"]: r for r in read_jsonl(PLAN_PATH)}
    batch = read_jsonl(Path(args.batch))
    unknown = [str(row.get("id")) for row in batch if row.get("id") not in plan]
    if unknown:
        print(f"ids not in the plan: {', '.join(unknown)}")
        return 1
    failed = 0
    for row in batch:
        errors = validate_reply(plan[row["id"]], row)
        if errors:
            failed += 1
            print(f"{row['id']}: {'; '.join(errors)}")
    print(f"{len(batch)} replies checked, {failed} rejected")
    return 1 if failed else 0


def cmd_build(args) -> int:
    plan = read_jsonl(PLAN_PATH)
    rows, rejected = build_dataset(plan, load_texts())
    for reply_id, errors in rejected.items():
        print(f"{reply_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} of {len(plan)} replies rejected; rewrite their texts and build again")
        return 1
    write_jsonl(DATASET_PATH, rows)
    counts = Counter(r["category"] for r in rows)
    splits = Counter(r["split"] for r in rows)
    reasons = Counter(r["expected_reason"] or "usable" for r in rows)
    print(f"Built {len(rows)} replies -> {DATASET_PATH}")
    print("  categories: " + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    print(f"  split: dev {splits['dev']}, test {splits['test']}")
    print("  expected usability: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    return 0


def cmd_blind(args) -> int:
    rows = [{"id": r["id"], "text": model_text(r)} for r in load_dataset()]
    BLIND_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(BLIND_DIR / "replies.jsonl", rows)
    (BLIND_DIR / "rules.txt").write_text(llm.OFFER_EXTRACTION_INSTRUCTIONS, encoding="utf-8", newline="\n")
    print(f"Wrote {len(rows)} blind replies and the label rules -> {BLIND_DIR}")
    return 0


def cmd_review(args) -> int:
    readings = [row for path in args.annotations for row in read_jsonl(Path(path))]
    dataset = load_dataset()
    read_ids = {x["id"] for x in readings}
    missing = [r["id"] for r in dataset if r["id"] not in read_ids]
    if missing:
        print(f"{len(missing)} replies not annotated: {', '.join(missing)}")
        return 1
    previous = read_jsonl(REVIEW_PATH) if REVIEW_PATH.exists() else []
    records = build_review(dataset, readings, previous)
    write_jsonl(REVIEW_PATH, records)
    counts = Counter(r["decision"] for r in records)
    print(f"Reviewed {len(records)} replies -> {REVIEW_PATH}")
    print("  " + ", ".join(f"{d} {counts[d]}" for d in (*REVIEW_DECISIONS, "pending")))
    return 1 if counts["pending"] else 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("offer-dataset", help="plan, check and build the offer extraction dataset")
    dsub = dataset.add_subparsers(dest="offer_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded reply plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_plan)
    check = dsub.add_parser("check", help="validate a batch of written replies against the plan")
    check.add_argument("batch", help="JSONL file with one written reply per row")
    check.set_defaults(handler=cmd_check)
    dsub.add_parser("build", help="validate the texts and write the dataset").set_defaults(handler=cmd_build)
    dsub.add_parser("blind", help="write the replies and the label rules for the blind annotator").set_defaults(
        handler=cmd_blind
    )
    review = dsub.add_parser("review", help="compare a blind annotator's readings with the labels")
    review.add_argument("annotations", nargs="+", help="JSONL files with one blind reading per reply")
    review.set_defaults(handler=cmd_review)
