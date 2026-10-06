"""Owner audit of dataset labels: review file and observed label error rate."""

import csv
import random
from pathlib import Path

from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation import dataset as ds
from purchase_cycle.evaluation.planning import DATASET_VERSION, read_jsonl
from purchase_cycle.evaluation.stats import wilson_interval

AUDIT_PATH = EVALS_DIR / "audit" / f"audit-v{DATASET_VERSION}.csv"
AUDIT_SEED = 150
AUDIT_SIZE = 150
MAX_WRONG = 2
COLUMNS = (
    "id",
    "set",
    "sentence",
    "expected_product",
    "expected_sku",
    "expected_quantity",
    "sale_unit",
    "verdict",
    "comment",
)
VERDICTS = ("ok", "wrong")


def _row(case: dict, kind: str) -> dict:
    product = ds.PRODUCT_BY_SKU.get(case["expected_sku"])
    return {
        "id": case["id"],
        "set": kind,
        "sentence": case["sentence"],
        "expected_product": product.name if product else "NOT IN CATALOG",
        "expected_sku": case["expected_sku"] or "",
        "expected_quantity": case["expected_quantity"],
        "sale_unit": product.sale_unit if product else "",
        "verdict": "",
        "comment": "",
    }


def create_audit(path: Path = AUDIT_PATH) -> int:
    if path.exists():
        print(f"{path} already exists; it may hold the owner's verdicts, so it is not overwritten")
        return 1
    cases = ds.load_dataset()
    sample = sorted(random.Random(AUDIT_SEED).sample(cases, AUDIT_SIZE), key=lambda c: c["id"])
    rows = [_row(c, "sample") for c in sample] + [_row(c, "contrast") for c in read_jsonl(ds.CONTRAST_PATH)]
    path.parent.mkdir(parents=True, exist_ok=True)
    # Semicolons and a BOM so a Spanish-locale Excel opens the columns directly.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows ({AUDIT_SIZE} sampled cases and the contrast set) -> {path}")
    return 0


def read_audit(path: Path = AUDIT_PATH) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def report_audit(path: Path = AUDIT_PATH) -> int:
    rows = read_audit(path)
    pending = [r["id"] for r in rows if r["verdict"].strip().lower() not in VERDICTS]
    if pending:
        print(f"{len(pending)} rows still without a verdict (ok or wrong), first: {', '.join(pending[:5])}")
        return 2
    exit_code = 0
    for kind in ("sample", "contrast"):
        subset = [r for r in rows if r["set"] == kind]
        wrong = [r for r in subset if r["verdict"].strip().lower() == "wrong"]
        low, high = wilson_interval(len(wrong), len(subset))
        print(
            f"{kind:<9} n={len(subset):<4} wrong labels={len(wrong):<3} "
            f"error rate={len(wrong) / len(subset):.1%}  95% CI [{low:.1%}, {high:.1%}]"
        )
        for r in wrong:
            print(f"  {r['id']}: {r['comment'] or 'no comment'}")
        if kind == "sample" and len(wrong) > MAX_WRONG:
            print(f"FAILED: more than {MAX_WRONG} wrong labels in the audit sample")
            exit_code = 1
    return exit_code
