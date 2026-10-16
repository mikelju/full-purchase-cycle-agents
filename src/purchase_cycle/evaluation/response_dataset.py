"""Golden dataset of the `response_drafting` evaluation: seeded plan, validation, build and second-pass review.

A case is the structured fact set of one customer response, not a free text.
The seeded plan fixes the delivered lines and the signed note incidents of every case from real catalog SKUs and
prices; the discrepancy rows are then computed by the generalised `reconcile_match.classify` and the correcting
documents by the rule of `sales.correction_lines`, the code the product uses, so every case is a response the
sales step can produce.
"""

import random
from collections import Counter
from pathlib import Path

from purchase_cycle import customer_responses, reconcile_match, sales
from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR, ROOT
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl

DATASET_VERSION = "1.0"
SEED = 20261016
CASES_PER_CATEGORY = 8
DEV_PER_CATEGORY = 2  # 25% development, 75% test
CATEGORIES = (
    "missing_only",
    "damaged_only",
    "extra_delivered_sku",
    "extra_sku_not_on_note",
    "missing_and_damaged_line",
    "several_types",
    "credit_note_several_lines",
    "corrective_invoice_several_lines",
    "both_documents",
    "decimals_thousands",
)
# The correcting documents every category must give, by kind, with the minimum number of lines of its first document.
KINDS = {
    "missing_only": ("credit_note",),
    "damaged_only": ("credit_note",),
    "extra_delivered_sku": ("corrective_invoice",),
    "extra_sku_not_on_note": ("corrective_invoice",),
    "missing_and_damaged_line": ("credit_note",),
    "several_types": ("credit_note", "corrective_invoice"),
    "credit_note_several_lines": ("credit_note",),
    "corrective_invoice_several_lines": ("corrective_invoice",),
    "both_documents": ("credit_note", "corrective_invoice"),
    "decimals_thousands": ("credit_note",),
}
MIN_LINES = {"credit_note_several_lines": 3, "corrective_invoice_several_lines": 3}
MAX_TRIES = 50
PREFIX = {"credit_note": "CN", "corrective_invoice": "CI"}

DATASET_DIR = EVALS_DIR / "datasets" / "response_drafting"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
REVIEW_PATH = DATASET_DIR / "second_pass_review.jsonl"
BLIND_DIR = ROOT / ".runtime" / "response_second_pass"  # gitignored input of the blind annotator
REVIEW_DECISIONS = ("agree", "label_fixed", "annotator_wrong")
AMOUNT_TOLERANCE = 0.005
BLIND_RULES = """You are the second annotator of a customer response dataset. For each case you get the delivered lines
of a delivery note (sku, units delivered, unit price) and the incidents the customer signed (sku, type, units).
Work out, without any tool, the correcting documents of the response:
- Units accepted per SKU = delivered - missing - damaged + extra; an extra SKU not on the note starts at 0.
- Units invoiced per SKU = delivered (the invoice has the delivery note lines at the unit price).
- For each SKU with accepted != invoiced: units = invoiced - accepted. units > 0 goes to the credit note with
  quantity -units; units < 0 goes to the corrective invoice with quantity -units (a positive quantity).
- line total = quantity * unit price, rounded to cents; document total = sum of its line totals, rounded to cents.
- A kind with no line gives no document. Credit note first, then corrective invoice.
- net_total of the invoice = sum of delivered units * unit price per line, rounded to cents.
Answer one JSON object per case: {"id": ..., "net_total": ..., "documents": [{"kind": ..., "total": ...,
"lines": [{"sku": ..., "quantity": ..., "line_total": ...}]}], "notes": "..."}.
"""

PRODUCTS_SORTED = sorted(PRODUCTS, key=lambda p: p.sku)
THREE_DECIMALS = [p for p in PRODUCTS_SORTED if len(str(p.price_eur).split(".")[-1]) >= 3]
CHEAP = [p for p in PRODUCTS_SORTED if p.price_eur <= 3]


# ---------- planning ----------


def _pick(rng, count: int, pool=PRODUCTS_SORTED) -> list:
    return rng.sample(pool, count)


def _units(rng, delivered: int, low: int = 1) -> int:
    return rng.randint(low, max(low, delivered - 1))


def _plan_lines(rng, category: str, n: int) -> tuple[list[dict], list[dict]]:
    """The delivered lines and the incidents of one case."""
    if category == "decimals_thousands":
        products = [rng.choice(THREE_DECIMALS)] + _pick(rng, 1, CHEAP)
        products = products if products[0].sku != products[1].sku else [products[0]]
    else:
        products = _pick(
            rng,
            {"several_types": 3, "credit_note_several_lines": 3, "corrective_invoice_several_lines": 3}.get(
                category, 1 + n % 3
            ),
        )
    big = category == "decimals_thousands"
    delivered = [
        {
            "sku": p.sku,
            "quantity": rng.choice(range(2000, 9000, 250)) if big else rng.choice((5, 10, 12, 20, 24, 40, 60, 100)),
        }
        for p in products
    ]
    incident = []

    def add(sku, kind, units):
        incident.append({"sku": sku, "type": kind, "units": units})

    first, qty = delivered[0]["sku"], delivered[0]["quantity"]
    if category == "missing_only":
        add(first, "missing", _units(rng, qty))
    elif category == "damaged_only":
        add(first, "damaged", _units(rng, qty))
    elif category == "extra_delivered_sku":
        add(first, "extra", rng.randint(1, 10))
    elif category == "extra_sku_not_on_note":
        other = rng.choice([p for p in PRODUCTS_SORTED if p.sku not in {d["sku"] for d in delivered}])
        add(other.sku, "extra", rng.randint(1, 10))
    elif category == "missing_and_damaged_line":
        add(first, "missing", _units(rng, qty // 2, 1))
        add(first, "damaged", _units(rng, qty // 2, 1))
    elif category == "several_types":
        add(delivered[0]["sku"], "missing", _units(rng, delivered[0]["quantity"]))
        add(delivered[1]["sku"], "damaged", _units(rng, delivered[1]["quantity"]))
        add(delivered[2]["sku"], "extra", rng.randint(1, 10))
    elif category == "credit_note_several_lines":
        for d in delivered:
            add(d["sku"], rng.choice(("missing", "damaged")), _units(rng, d["quantity"]))
    elif category == "corrective_invoice_several_lines":
        for d in delivered:
            add(d["sku"], "extra", rng.randint(1, 10))
    elif category == "both_documents":
        add(first, rng.choice(("missing", "damaged")), _units(rng, qty))
        other = rng.choice([p for p in PRODUCTS_SORTED if p.sku not in {d["sku"] for d in delivered}])
        add(other.sku, "extra", rng.randint(1, 10))
    else:  # decimals_thousands
        add(first, rng.choice(("missing", "damaged")), rng.randint(300, 900))
        if len(delivered) > 1:
            add(delivered[1]["sku"], rng.choice(("missing", "damaged")), rng.randint(300, 900))
    return delivered, incident


def _price(sku: str) -> float:
    return next(round(p.price_eur, 2) for p in PRODUCTS_SORTED if p.sku == sku)  # as `db.seed` stores it


def _name(sku: str) -> str:
    return next(p.name for p in PRODUCTS_SORTED if p.sku == sku)


def correction_lines(row: dict, rows: list[dict]) -> dict[str, list[tuple[str, int, float, float]]]:
    """The rule of `sales.correction_lines` over the quantity rows of a case, priced at the catalog price."""
    out: dict[str, list[tuple[str, int, float, float]]] = {}
    for r in rows:
        if r["type"] != reconcile_match.QUANTITY:
            continue
        units = round(r["found"] - r["expected"])
        if units == 0:
            continue
        price = _price(r["sku"])
        kind = "credit_note" if units > 0 else "corrective_invoice"
        out.setdefault(kind, []).append((r["sku"], -units, price, round(-units * price, 2)))
    return out


def _accepted(row: dict) -> dict[str, int]:
    """Units accepted per SKU, as `sales.accepted`: delivered minus missing and damaged plus extra."""
    units = {d["sku"]: d["quantity"] for d in row["delivered"]}
    for i in row["incidents"]:
        units[i["sku"]] = units.get(i["sku"], 0) + (i["units"] if i["type"] == "extra" else -i["units"])
    return units


def classified(row: dict) -> tuple[str, list[dict]]:
    """The result and rows of the generalised `classify` for the invoice of a case, as `sales._reconcile_one`."""
    order = {
        "lines": [
            {"sku": d["sku"], "quantity": d["quantity"], "unit_price": _price(d["sku"])} for d in row["delivered"]
        ]
    }
    invoice = [
        {
            "sku": d["sku"],
            "quantity": d["quantity"],
            "unit_price": _price(d["sku"]),
            "line_total": round(d["quantity"] * _price(d["sku"]), 2),
        }
        for d in row["delivered"]
    ]
    note = [
        {"sku": sku, "quantity": q, "unit_price": _price(sku), "line_total": None} for sku, q in _accepted(row).items()
    ]
    return reconcile_match.classify(order, invoice, note)


def facts_of(row: dict) -> dict:
    """The response facts of a planned case, computed with the product rules."""
    _, rows = classified(row)
    customer = CUSTOMERS[row["customer_index"]]
    documents, next_id = [], row["first_document_id"]
    for kind, lines in correction_lines(row, rows).items():
        documents.append(
            {
                "number": sales._number(PREFIX[kind], next_id),
                "kind": kind,
                "total": round(sum(x[3] for x in lines), 2),
                "lines": [dict(zip(("sku", "quantity", "unit_price", "line_total"), x, strict=True)) for x in lines],
            }
        )
        next_id += 1
    return {
        "customer": customer.name,
        "recipient": customer.email,
        "invoice": sales._number("SINV", row["invoice_id"]),
        "delivery_note": sales._number("CDN", row["invoice_id"]),
        "net_total": round(sum(round(d["quantity"] * _price(d["sku"]), 2) for d in row["delivered"]), 2),
        "incidents": [{**i, "product": _name(i["sku"])} for i in row["incidents"]],
        "documents": [
            documents[k] for k in sorted(range(len(documents)), key=lambda k: documents[k]["kind"] != "credit_note")
        ],
    }


def validate_case(row: dict) -> list[str]:
    """Automatic validation of one built case."""
    errors = []
    result, rows = classified(row)
    facts = row.get("facts") or facts_of(row)
    if result != reconcile_match.DISCREPANCIES:
        errors.append(f"classify gives {result}, not discrepancies")
    if any(r["type"] != reconcile_match.QUANTITY for r in rows):
        errors.append("a non-quantity difference row")
    delivered = {d["sku"]: d["quantity"] for d in row["delivered"]}
    lost = Counter()
    for i in row["incidents"]:
        if i["units"] < 1:
            errors.append(f"incident of {i['sku']} has no units")
        if i["type"] != "extra":
            lost[i["sku"]] += i["units"]
    for sku, units in lost.items():
        if units >= delivered.get(sku, 0):
            errors.append(f"{sku}: {units} missing or damaged units of {delivered.get(sku, 0)} delivered")
    if facts != facts_of(row):
        errors.append("facts differ from the classify and document rule")
    kinds = tuple(d["kind"] for d in facts["documents"])
    if kinds != KINDS[row["category"]]:
        errors.append(f"documents {kinds} do not fit {row['category']}")
    for doc in facts["documents"]:
        if doc["total"] != round(sum(x["line_total"] for x in doc["lines"]), 2):
            errors.append(f"{doc['number']} total differs from its lines")
        if (doc["total"] < 0) != (doc["kind"] == "credit_note") or doc["total"] == 0:
            errors.append(f"{doc['number']} total sign does not fit its kind")
    if facts["documents"] and len(facts["documents"][0]["lines"]) < MIN_LINES.get(row["category"], 1):
        errors.append("too few document lines for the category")
    if row["category"] == "extra_sku_not_on_note" and facts["incidents"][0]["sku"] in delivered:
        errors.append("the extra SKU is on the delivery note")
    if row["category"] == "missing_and_damaged_line" and {i["type"] for i in row["incidents"]} != {
        "missing",
        "damaged",
    }:
        errors.append("needs a missing and a damaged incident")
    if row["category"] == "decimals_thousands" and not (
        facts["net_total"] >= 1000 and any(abs(d["total"]) >= 100 for d in facts["documents"])
    ):
        errors.append("amounts too small for the thousands category")
    template = customer_responses.fill_template(facts)
    errors += [f"template: {p}" for p in customer_responses.check_response(template, facts)]
    return errors


def _case(rng, category: str, n: int, index: int, first_document_id: int) -> dict:
    for _ in range(MAX_TRIES):
        delivered, incidents = _plan_lines(rng, category, n)
        row = {
            "category": category,
            "customer_index": rng.randrange(len(CUSTOMERS)),
            "invoice_id": index,
            "first_document_id": first_document_id,
            "delivered": delivered,
            "incidents": incidents,
        }
        if not validate_case(row):
            return row
    raise RuntimeError(f"no valid {category} case after {MAX_TRIES} tries")


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    plan = []
    for category in CATEGORIES:
        rows = []
        for n in range(CASES_PER_CATEGORY):
            rows.append(_case(rng, category, n, len(plan) + len(rows) + 1, 2 * (len(plan) + len(rows)) + 1))
        dev = set(rng.sample(range(len(rows)), DEV_PER_CATEGORY))
        plan += [dict(row, split="dev" if n in dev else "test") for n, row in enumerate(rows)]
    for n, row in enumerate(plan, start=1):
        row["id"] = f"RSP-{n:03d}"
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
    return customer_responses.facts_prompt(row["facts"])


# ---------- second-pass review ----------


def review_disagreements(row: dict, read: dict) -> list[dict]:
    """Differences between the labelled documents of a case and a blind annotator's reading of it."""
    found = []
    label = {d["kind"]: d for d in row["facts"]["documents"]}
    seen = {d["kind"]: d for d in read.get("documents", [])}
    for kind in sorted(label.keys() | seen.keys()):
        if kind not in seen or kind not in label:
            found.append({"field": f"document {kind}", "label": kind in label, "read": kind in seen})
            continue
        a, b = label[kind]["total"], seen[kind].get("total")
        if b is None or abs(a - b) > AMOUNT_TOLERANCE:
            found.append({"field": f"total {kind}", "label": a, "read": b})
        mine = sorted((x["sku"], x["quantity"]) for x in label[kind]["lines"])
        theirs = sorted((x.get("sku"), x.get("quantity")) for x in seen[kind].get("lines", []))
        if mine != theirs:
            found.append({"field": f"lines {kind}", "label": mine, "read": theirs})
    net, read_net = row["facts"]["net_total"], read.get("net_total")
    if read_net is None or abs(net - read_net) > AMOUNT_TOLERANCE:
        found.append({"field": "net_total", "label": net, "read": read_net})
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
                "read": {k: read.get(k) for k in ("documents", "net_total")},
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
    kinds = Counter(d["kind"] for r in rows for d in r["facts"]["documents"])
    print("  documents: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    return 0


def cmd_blind(args) -> int:
    rows = [
        {
            "id": r["id"],
            "delivered": [{**d, "unit_price": _price(d["sku"])} for d in r["delivered"]],
            "incidents": r["incidents"],
        }
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
    dataset = sub.add_parser("response-dataset", help="plan, build and review the response drafting dataset")
    dsub = dataset.add_subparsers(dest="response_dataset_command", required=True)
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
