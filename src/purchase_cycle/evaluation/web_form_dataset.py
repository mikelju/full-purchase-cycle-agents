"""Golden dataset of the web form matching evaluation: plan, validation, build and owner audit.

The seeded plan fixes the category, expected SKU, trap, quantity, customer and
submission of every line before any text exists, so labels are decided by
construction. Exact name and SKU texts are written by this script; the other
categories are written by the coding agent and checked here.
"""

import csv
import random
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.planning import OUT_OF_CATALOG_ITEMS, read_jsonl, write_jsonl
from purchase_cycle.evaluation.stats import wilson_interval
from purchase_cycle.web_form import catalog_index, match_key

DATASET_VERSION = "1.0"
SEED = 20261007
LINES_PER_CATEGORY = 104
DEV_PER_CATEGORY = 26
SUBMISSION_SIZES = (1, 2, 2, 3, 3, 4, 5)
MAX_CHARS = 120

CATEGORIES = ("exact_name", "sku_typed", "short_name", "typo", "near_miss", "out_of_catalog")
SCRIPT_CATEGORIES = ("exact_name", "sku_typed")
WRITTEN_CATEGORIES = tuple(c for c in CATEGORIES if c not in SCRIPT_CATEGORIES)
NAME_STYLES = ("as_written", "lower_case", "upper_case", "extra_spaces", "no_punctuation")
SKU_STYLES = ("upper_case", "lower_case", "spaces_for_hyphens", "lower_with_spaces")
SHORT_STYLES = ("shortened_name", "everyday_name", "reordered_words")
TYPO_STYLES = ("one_misspelled_word", "two_misspelled_words", "missing_letters", "swapped_letters")

DATASET_DIR = EVALS_DIR / "datasets" / "web_form_matching"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
TEXTS_DIR = DATASET_DIR / "texts"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
REVIEW_PATH = DATASET_DIR / "second_pass_review.jsonl"
AUDIT_PATH = EVALS_DIR / "audit" / f"web_form_matching-audit-v{DATASET_VERSION}.csv"
AUDIT_SEED = 60
AUDIT_SIZE = 60
MAX_WRONG = 1
AUDIT_COLUMNS = (
    "id",
    "category",
    "product_text",
    "expected_product",
    "expected_sku",
    "sale_unit",
    "verdict",
    "comment",
)
VERDICTS = ("ok", "wrong")

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}
CATALOG_KEYS = catalog_index([{"sku": p.sku, "name": p.name} for p in PRODUCTS])


def script_text(category: str, style: str, sku: str) -> str:
    """Product text of the categories written by the script."""
    if category == "sku_typed":
        return {
            "upper_case": sku,
            "lower_case": sku.lower(),
            "spaces_for_hyphens": sku.replace("-", " "),
            "lower_with_spaces": sku.lower().replace("-", " "),
        }[style]
    name = PRODUCT_BY_SKU[sku].name
    return {
        "as_written": name,
        "lower_case": name.lower(),
        "upper_case": name.upper(),
        "extra_spaces": name.replace(" ", "  ").replace(",", " ,"),
        "no_punctuation": " ".join(re.sub(r"[^\w\s]", " ", name).split()),
    }[style]


def _trap(rng, category: str, product, siblings) -> dict:
    if category == "exact_name":
        return {"style": rng.choice(NAME_STYLES)}
    if category == "sku_typed":
        return {"style": rng.choice(SKU_STYLES)}
    if category == "short_name":
        return {"style": rng.choice(SHORT_STYLES)}
    if category == "typo":
        return {"style": rng.choice(TYPO_STYLES)}
    others = [s for s in siblings[product.family] if s.sku != product.sku]
    closest = max(others, key=lambda s: (SequenceMatcher(None, s.name, product.name).ratio(), s.sku))
    return {"style": "close_variant", "confusable_with": closest.sku}


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    products = sorted(PRODUCTS, key=lambda p: p.sku)
    siblings = defaultdict(list)
    for p in products:
        siblings[p.family].append(p)

    # Every product appears once or twice across the in-catalog categories, at most once per category.
    uses = Counter()
    rows = {"dev": [], "test": []}
    for category in CATEGORIES:
        lines = []
        if category == "out_of_catalog":
            for i in range(LINES_PER_CATEGORY):
                item = OUT_OF_CATALOG_ITEMS[i % len(OUT_OF_CATALOG_ITEMS)]
                lines.append(
                    {
                        "category": category,
                        "expected_sku": None,
                        "trap": {"style": "not_in_catalog", "requested_item": item},
                    }
                )
        else:
            ranked = sorted(products, key=lambda p: (uses[p.sku], rng.random()))[:LINES_PER_CATEGORY]
            for product in ranked:
                uses[product.sku] += 1
                trap = _trap(rng, category, product, siblings)
                line = {"category": category, "expected_sku": product.sku, "trap": trap}
                if category in SCRIPT_CATEGORIES:
                    line["product_text"] = script_text(category, trap["style"], product.sku)
                lines.append(line)
        rng.shuffle(lines)
        for i, line in enumerate(lines):
            line["quantity"] = rng.randint(1, 50)
            rows["dev" if i < DEV_PER_CATEGORY else "test"].append(
                dict(line, split="dev" if i < DEV_PER_CATEGORY else "test")
            )

    # Lines are grouped into submissions inside each split, so the split is by submission.
    plan, n_submission = [], 0
    for split in ("dev", "test"):
        pending = rows[split]
        rng.shuffle(pending)
        while pending:
            size = min(rng.choice(SUBMISSION_SIZES), len(pending))
            n_submission += 1
            customer = rng.choice(CUSTOMERS).code
            for line in pending[:size]:
                plan.append(dict(line, submission_id=f"WFS-{n_submission:04d}", customer_code=customer))
            pending = pending[size:]
    for n, line in enumerate(plan, start=1):
        line["id"] = f"WFM-{n:04d}"
        line["dataset_version"] = DATASET_VERSION
    return plan


def validate_line(line: dict) -> list[str]:
    """Return the reasons a line is rejected; empty when it passes."""
    text = (line.get("product_text") or "").strip()
    sku = line["expected_sku"]
    category = line["category"]
    if not text:
        return ["product text is empty"]
    errors = []
    if len(text) > MAX_CHARS:
        errors.append(f"product text longer than {MAX_CHARS} characters")
    if category == "out_of_catalog":
        if sku is not None:
            errors.append("out_of_catalog line must expect a null SKU")
    elif sku not in PRODUCT_BY_SKU:
        errors.append(f"expected SKU {sku} does not exist")
    if errors:
        return errors
    key = match_key(text)
    if category == "exact_name" and key != match_key(PRODUCT_BY_SKU[sku].name):
        errors.append("exact_name text is not the catalog name")
    elif category == "sku_typed" and key != match_key(sku):
        errors.append("sku_typed text is not the SKU")
    elif category in WRITTEN_CATEGORIES and key in CATALOG_KEYS:
        errors.append(f"{category} text equals the catalog name or SKU of {CATALOG_KEYS[key]}")
    return errors


def load_texts(directory: Path = TEXTS_DIR) -> dict[str, str]:
    texts = {}
    for path in sorted(directory.glob("*.jsonl")):
        for row in read_jsonl(path):
            texts[row["id"]] = row["product_text"]
    return texts


def build_dataset(plan: list[dict], texts: dict[str, str]) -> tuple[list[dict], dict[str, list[str]]]:
    """Join plan and written texts; return the lines and the rejected ids with reasons."""
    lines, rejected, seen = [], {}, {}
    for planned in plan:
        line = dict(planned)
        if line["category"] in WRITTEN_CATEGORIES:
            line["product_text"] = texts.get(line["id"], "")
        errors = validate_line(line)
        key = match_key(line["product_text"])
        if key and key in seen:
            errors.append(f"duplicates {seen[key]} after normalisation")
        seen.setdefault(key, line["id"])
        if errors:
            rejected[line["id"]] = errors
        lines.append(line)
    return lines, rejected


def submissions(lines: list[dict]) -> list[dict]:
    """Group dataset lines into web form submissions, keeping the line order."""
    grouped = {}
    for line in lines:
        sub = grouped.setdefault(
            line["submission_id"],
            {
                "submission_id": line["submission_id"],
                "customer_code": line["customer_code"],
                "split": line["split"],
                "lines": [],
            },
        )
        sub["lines"].append(line)
    return list(grouped.values())


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    return read_jsonl(path)


def cmd_plan(args) -> int:
    plan = build_plan(args.seed)
    write_jsonl(PLAN_PATH, plan)
    print(f"Planned {len(plan)} lines in {len(submissions(plan))} submissions with seed {args.seed} -> {PLAN_PATH}")
    return 0


def cmd_check(args) -> int:
    plan = {line["id"]: line for line in read_jsonl(PLAN_PATH)}
    rows = read_jsonl(Path(args.batch))
    failed = 0
    for row in rows:
        errors = validate_line(dict(plan[row["id"]], product_text=row["product_text"]))
        if errors:
            failed += 1
            print(f"{row['id']}: {'; '.join(errors)}")
    print(f"{len(rows)} product texts checked, {failed} rejected")
    return 1 if failed else 0


def cmd_build(args) -> int:
    plan = read_jsonl(PLAN_PATH)
    lines, rejected = build_dataset(plan, load_texts())
    for line_id, errors in rejected.items():
        print(f"{line_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} of {len(plan)} lines rejected; rewrite their texts and build again")
        return 1
    write_jsonl(DATASET_PATH, lines)
    counts = Counter(line["category"] for line in lines)
    print(f"Built {len(lines)} lines in {len(submissions(lines))} submissions -> {DATASET_PATH}")
    print("  " + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    return 0


def create_audit(path: Path = AUDIT_PATH) -> int:
    if path.exists():
        print(f"{path} already exists; it may hold the owner's verdicts, so it is not overwritten")
        return 1
    written = [line for line in load_dataset() if line["category"] in WRITTEN_CATEGORIES]
    sample = sorted(random.Random(AUDIT_SEED).sample(written, AUDIT_SIZE), key=lambda line: line["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Semicolons and a BOM so a Spanish-locale Excel opens the columns directly, as in phase 01.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=AUDIT_COLUMNS, delimiter=";")
        writer.writeheader()
        for line in sample:
            product = PRODUCT_BY_SKU.get(line["expected_sku"])
            writer.writerow(
                {
                    "id": line["id"],
                    "category": line["category"],
                    "product_text": line["product_text"],
                    "expected_product": product.name if product else "NOT IN CATALOG",
                    "expected_sku": line["expected_sku"] or "",
                    "sale_unit": product.sale_unit if product else "",
                    "verdict": "",
                    "comment": "",
                }
            )
    print(f"Wrote {len(sample)} agent-written lines -> {path}")
    return 0


def report_audit(path: Path = AUDIT_PATH) -> int:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    pending = [r["id"] for r in rows if r["verdict"].strip().lower() not in VERDICTS]
    if pending:
        print(f"{len(pending)} rows still without a verdict (ok or wrong), first: {', '.join(pending[:5])}")
        return 2
    wrong = [r for r in rows if r["verdict"].strip().lower() == "wrong"]
    low, high = wilson_interval(len(wrong), len(rows))
    print(
        f"web_form_matching audit n={len(rows)} wrong labels={len(wrong)} "
        f"error rate={len(wrong) / len(rows):.1%}  95% CI [{low:.1%}, {high:.1%}]"
    )
    for r in wrong:
        print(f"  {r['id']}: {r['comment'] or 'no comment'}")
    if len(wrong) > MAX_WRONG:
        print(f"FAILED: more than {MAX_WRONG} wrong label in the audit sample")
        return 1
    return 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("web-form-dataset", help="plan, check and build the web form matching dataset")
    dsub = dataset.add_subparsers(dest="web_form_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded line plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_plan)
    check = dsub.add_parser("check", help="validate a batch of written product texts against the plan")
    check.add_argument("batch")
    check.set_defaults(handler=cmd_check)
    dsub.add_parser("build", help="join plan and texts, validate and write the dataset").set_defaults(handler=cmd_build)
    audit = sub.add_parser("web-form-audit", help="owner audit of the web form matching labels")
    asub = audit.add_subparsers(dest="web_form_audit_command", required=True)
    asub.add_parser("create", help="write the review file").set_defaults(handler=lambda args: create_audit())
    asub.add_parser("report", help="compute the label error rate").set_defaults(handler=lambda args: report_audit())
