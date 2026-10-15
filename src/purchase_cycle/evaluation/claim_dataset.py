"""Golden dataset of the `claim_drafting` evaluation: seeded plan, validation, build and second-pass review.

A case is the structured fact set of one claim, not a free text.
The seeded plan fixes the order, the invoice lines and the delivery note lines of every case from real catalog SKUs
and prices; the discrepancy rows are then computed by `reconcile_match.classify` and the amount in dispute by
`claims.amount_in_dispute`, the code the product uses, so every case is a difference phase 08 can produce.
"""

import random
from collections import Counter
from pathlib import Path

from purchase_cycle import claims, reconcile_match, reconciliation
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.config import EVALS_DIR, ROOT
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.suppliers import SUPPLIERS

DATASET_VERSION = "1.0"
SEED = 20261014
CASES_PER_CATEGORY = 8
DEV_PER_CATEGORY = 2  # 25% development, 75% test
CATEGORIES = (
    "price_above",
    "price_below",
    "quantity_above",
    "invoiced_above_delivered",
    "extra_line",
    "unresolved_extra_line",
    "missing_line",
    "price_and_quantity",
    "several_types",
    "decimals_thousands",
)
QUANTITIES = (10, 12, 20, 24, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300, 500)
TYPES = {
    "price_above": {"price"},
    "price_below": {"price"},
    "quantity_above": {"quantity"},
    "invoiced_above_delivered": {"quantity"},
    "extra_line": {"extra_line"},
    "unresolved_extra_line": {"extra_line"},
    "missing_line": {"extra_line", "missing_line"},
    "price_and_quantity": {"price", "quantity"},
    "several_types": {"price", "quantity", "extra_line"},
    "decimals_thousands": {"price"},
}
MAX_TRIES = 50

DATASET_DIR = EVALS_DIR / "datasets" / "claim_drafting"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
REVIEW_PATH = DATASET_DIR / "second_pass_review.jsonl"
BLIND_DIR = ROOT / ".runtime" / "claim_second_pass"  # gitignored input of the blind annotator
REVIEW_DECISIONS = ("agree", "label_fixed", "annotator_wrong")
AMOUNT_TOLERANCE = claims.AMOUNT_TOLERANCE
BLIND_RULES = """You are the second annotator of a claim dataset. For each case you get the purchase order line,
the invoice lines and the delivery note lines (null when there is no delivery note). Work out, without any tool:
- the discrepancy rows of the invoice, one JSON object each with sku, text, type, expected, found and amount;
- the amount in dispute, rounded to cents.
Row rules:
- price: the invoice unit price differs from the order unit price by more than 0.01 EUR. expected is the order price,
  found the invoice price, amount = sum((invoice price - order price) * quantity) over the lines that deviate in the
  same direction as the largest deviation (positive: the invoice charges more). A line with no price: found and amount null.
- quantity: the invoiced quantity of the order SKU differs from the ordered quantity or from the delivered quantity.
  expected is the one that differs more, found the invoiced quantity, amount = (found - expected) * order unit price.
- extra_line: an invoice line whose SKU is not the order SKU (sku null and text set when it did not resolve).
  expected null, found its quantity, amount its line total (unit price times quantity when no total), null without a price.
- missing_line: the order SKU is on no invoice line. expected is the ordered quantity, amount = quantity * order price.
Amount in dispute: the sum of the amounts of price and quantity rows with a positive amount and of extra_line rows
with a non-null amount; negative rows, missing_line rows and null amounts add nothing.
Answer one JSON object per case: {"id": ..., "differences": [...], "amount_in_dispute": ..., "notes": "..."}.
"""

PRODUCTS_SORTED = sorted(PRODUCTS, key=lambda p: p.sku)


# ---------- planning ----------


def _line(sku, quantity, unit_price, text=None) -> dict:
    total = None if unit_price is None else round(quantity * unit_price, 2)
    return {"sku": sku, "quantity": quantity, "unit_price": unit_price, "line_total": total, "product_text": text}


def _other(rng, product):
    return rng.choice([p for p in PRODUCTS_SORTED if p.sku != product.sku])


def _priced_extra(rng, product) -> dict:
    other = _other(rng, product)
    return _line(other.sku, rng.choice((1, 2, 5, 10, 20, 40)), round(other.price_eur * rng.uniform(0.9, 1.2), 2))


def _free_text_extra(rng, product, priced=True) -> dict:
    other = _other(rng, product)
    price = round(other.price_eur * rng.uniform(0.9, 1.2), 2) if priced else None
    return _line(None, rng.choice((1, 2, 5, 10, 20, 40)), price, other.name.lower() + " (supplier ref)")


def _raised(rng, price: float, low: float, high: float) -> float:
    """A price that differs from the order price by more than the matching tolerance."""
    new = round(price * rng.uniform(low, high), 2)
    return new if abs(new - price) > 0.02 else round(price + 0.05, 2)


def _case_inputs(rng, category: str, n: int) -> dict:
    product = rng.choice(PRODUCTS_SORTED)
    quantity = rng.choice(QUANTITIES)
    price = round(product.price_eur, 2)
    order = {"sku": product.sku, "quantity": quantity, "unit_price": price}
    invoice, note = [_line(product.sku, quantity, price)], [_line(product.sku, quantity, price)]
    if category == "price_above":
        invoice = [_line(product.sku, quantity, _raised(rng, price, 1.04, 1.3))]
    elif category == "price_below":
        invoice = [_line(product.sku, quantity, _raised(rng, price, 0.7, 0.95))]
    elif category == "quantity_above":
        more = quantity + rng.choice((2, 5, 10, 20, 50))
        invoice, note = [_line(product.sku, more, price)], [_line(product.sku, more, price)]
    elif category == "invoiced_above_delivered":
        note = [_line(product.sku, quantity - rng.choice((2, 5, 8, 10)), price)]
    elif category == "extra_line":
        invoice.append(_priced_extra(rng, product))
    elif category == "unresolved_extra_line":
        invoice.append(_free_text_extra(rng, product, priced=n % 4 != 3))
    elif category == "missing_line":
        invoice = [_priced_extra(rng, product)]
    elif category == "price_and_quantity":
        more = quantity + rng.choice((2, 5, 10, 20))
        invoice, note = [_line(product.sku, more, _raised(rng, price, 1.05, 1.25))], [_line(product.sku, more, price)]
    elif category == "several_types":
        more = quantity + rng.choice((2, 5, 10))
        invoice = [_line(product.sku, more, _raised(rng, price, 1.05, 1.25)), _priced_extra(rng, product)]
        invoice.append(_free_text_extra(rng, product, priced=n % 2 == 0))
        note = [_line(product.sku, more, price)]
    else:  # decimals_thousands
        price = round(product.price_eur / rng.choice((10, 25, 40)), 4)
        quantity = rng.choice(range(20000, 90000, 500))
        order = {"sku": product.sku, "quantity": quantity, "unit_price": price}
        raised = round(price + rng.choice((0.0113, 0.0237, 0.0451, 0.0689)), 4)
        invoice, note = [_line(product.sku, quantity, raised)], [_line(product.sku, quantity, price)]
    return {"order": order, "invoice_lines": invoice, "note_lines": note}


def facts_of(row: dict) -> dict:
    """The claim facts of a planned case, computed with the product code."""
    result, differences = reconcile_match.classify(row["order"], row["invoice_lines"], row["note_lines"])
    supplier = SUPPLIERS[row["supplier_index"]]
    return {
        "invoice": reconciliation.invoice_reference(row["invoice_id"]),
        "invoice_number": row["invoice_number"],
        "order": reconciliation.order_reference(row["order_id"]),
        "supplier": supplier.name,
        "recipient": supplier.email,
        "differences": differences if result == reconcile_match.DISCREPANCIES else [],
        "amount_in_dispute": claims.amount_in_dispute(differences),
    }


def validate_case(row: dict) -> list[str]:
    """Automatic validation of one built case."""
    errors = []
    result, differences = reconcile_match.classify(row["order"], row["invoice_lines"], row["note_lines"])
    facts = row.get("facts") or facts_of(row)
    if result != reconcile_match.DISCREPANCIES:
        errors.append(f"classify gives {result}, not discrepancies")
    if facts["differences"] != differences:
        errors.append("discrepancy rows differ from classify")
    if facts["amount_in_dispute"] != claims.amount_in_dispute(differences):
        errors.append("amount in dispute differs from the D5 rule")
    if {d["type"] for d in differences} != TYPES[row["category"]]:
        errors.append(f"row types {sorted({d['type'] for d in differences})} do not fit {row['category']}")
    amounts = [d["amount"] for d in differences if d["type"] == "price" and d["amount"] is not None]
    if row["category"] in ("price_above", "decimals_thousands") and not all(a > 0 for a in amounts):
        errors.append("price amount must be positive")
    if row["category"] == "price_below" and not all(a < 0 for a in amounts):
        errors.append("price amount must be negative")
    if row["category"] == "decimals_thousands" and facts["amount_in_dispute"] < 1000:
        errors.append("amount in dispute below 1000")
    template = claims.fill_template(facts)
    errors += [f"template: {p}" for p in claims.check_draft(template, facts)]
    return errors


def _case(rng, category: str, n: int, index: int) -> dict:
    for _ in range(MAX_TRIES):
        row = {
            "category": category,
            "supplier_index": rng.randrange(len(SUPPLIERS)),
            "invoice_id": index,
            "invoice_number": f"F-2026-{rng.randint(1000, 9999)}",
            "order_id": rng.randint(1, 400),
        } | _case_inputs(rng, category, n)
        if not validate_case(row):
            return row
    raise RuntimeError(f"no valid {category} case after {MAX_TRIES} tries")


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    plan = []
    for category in CATEGORIES:
        rows = [_case(rng, category, n, len(plan) + n + 1) for n in range(CASES_PER_CATEGORY)]
        dev = set(rng.sample(range(len(rows)), DEV_PER_CATEGORY))
        plan += [dict(row, split="dev" if n in dev else "test") for n, row in enumerate(rows)]
    for n, row in enumerate(plan, start=1):
        row["id"] = f"CLM-{n:03d}"
    return plan


def build_dataset(plan: list[dict]) -> tuple[list[dict], dict[str, list[str]]]:
    rows, rejected = [], {}
    for planned in plan:
        row = dict(planned, facts=facts_of(planned), dataset_version=DATASET_VERSION)
        errors = validate_case(row)
        if errors:
            rejected[planned["id"]] = errors
        else:
            rows.append(row)
    return rows, rejected


def load_dataset(path: Path | None = None) -> list[dict]:
    return read_jsonl(path or DATASET_PATH)


def model_text(row: dict) -> str:
    """The prompt the model sees for a case, as the product builds it."""
    return claims.facts_prompt(row["facts"])


# ---------- second-pass review ----------


def _key(d: dict) -> tuple:
    return (d.get("sku") or d.get("text") or "", d["type"])


def review_disagreements(row: dict, read: dict) -> list[dict]:
    """Differences between the labelled facts of a case and a blind annotator's reading of it."""
    found = []
    label = {_key(d): d for d in row["facts"]["differences"]}
    seen = {_key(d): d for d in read.get("differences", [])}
    for key in sorted(label.keys() | seen.keys()):
        if key not in seen or key not in label:
            found.append({"field": f"row {key[0]} {key[1]}", "label": key in label, "read": key in seen})
            continue
        a, b = label[key]["amount"], seen[key].get("amount")
        if (a is None) != (b is None) or (a is not None and abs(a - b) > AMOUNT_TOLERANCE):
            found.append({"field": f"amount {key[0]} {key[1]}", "label": a, "read": b})
    label_total, read_total = row["facts"]["amount_in_dispute"], read.get("amount_in_dispute")
    if read_total is None or abs(label_total - read_total) > AMOUNT_TOLERANCE:
        found.append({"field": "amount_in_dispute", "label": label_total, "read": read_total})
    return found


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
                "read": {k: read.get(k) for k in ("differences", "amount_in_dispute")},
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
    print(f"Planned {len(plan)} cases with seed {args.seed} -> {PLAN_PATH}")
    return 0


def cmd_build(args) -> int:
    plan = read_jsonl(PLAN_PATH)
    rows, rejected = build_dataset(plan)
    for case_id, errors in rejected.items():
        print(f"{case_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} of {len(plan)} cases rejected")
        return 1
    write_jsonl(DATASET_PATH, rows)
    counts, splits = Counter(r["category"] for r in rows), Counter(r["split"] for r in rows)
    print(f"Built {len(rows)} cases -> {DATASET_PATH}")
    print("  categories: " + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    print(f"  split: dev {splits['dev']}, test {splits['test']}")
    print("  amount in dispute: " + ", ".join(f"{k} {v}" for k, v in sorted(_dispute_kinds(rows).items())))
    return 0


def _dispute_kinds(rows: list[dict]) -> Counter:
    return Counter("zero" if r["facts"]["amount_in_dispute"] == 0 else "positive" for r in rows)


def cmd_blind(args) -> int:
    rows = [
        {"id": r["id"], "order": r["order"], "invoice_lines": r["invoice_lines"], "note_lines": r["note_lines"]}
        for r in load_dataset()
    ]
    BLIND_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(BLIND_DIR / "cases.jsonl", rows)
    (BLIND_DIR / "rules.txt").write_text(BLIND_RULES, encoding="utf-8", newline="\n")
    print(f"Wrote {len(rows)} blind cases and the label rules -> {BLIND_DIR}")
    return 0


def cmd_review(args) -> int:
    readings = [row for path in args.annotations for row in read_jsonl(Path(path))]
    dataset = load_dataset()
    missing = [r["id"] for r in dataset if r["id"] not in {x["id"] for x in readings}]
    if missing:
        print(f"{len(missing)} cases not annotated: {', '.join(missing)}")
        return 1
    previous = read_jsonl(REVIEW_PATH) if REVIEW_PATH.exists() else []
    records = build_review(dataset, readings, previous)
    write_jsonl(REVIEW_PATH, records)
    counts = Counter(r["decision"] for r in records)
    print(f"Reviewed {len(records)} cases -> {REVIEW_PATH}")
    print("  " + ", ".join(f"{d} {counts[d]}" for d in (*REVIEW_DECISIONS, "pending")))
    return 1 if counts["pending"] else 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("claim-dataset", help="plan, build and review the claim drafting dataset")
    dsub = dataset.add_subparsers(dest="claim_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded case plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_plan)
    dsub.add_parser("build", help="validate the plan and write the dataset").set_defaults(handler=cmd_build)
    dsub.add_parser("blind", help="write the cases and the label rules for the blind annotator").set_defaults(
        handler=cmd_blind
    )
    review = dsub.add_parser("review", help="compare a blind annotator's readings with the labels")
    review.add_argument("annotations", nargs="+", help="JSONL files with one blind reading per case")
    review.set_defaults(handler=cmd_review)
