"""Evaluation runs of the supplier document extraction on the `document_extraction` dataset (phase 08, C11).

Every document goes through the steps of the product: the file is read with the product readers (`.txt`, PDF text,
Excel text), the model extracts it, the deterministic checks of `reconciliation.check_answer` run and each line
text is resolved to a catalog SKU with `web_form.catalog_index` and `match_key`.
All graders are deterministic; a schema or check failure grades as `extraction_failed`.
Each metric is kept as [hits, n] per document so that every rate has its own counting unit and Wilson interval.
"""

import json
import sys
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import reconciliation, web_form
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.config import EVALS_DIR, MODEL_ID, SUPPLIER_DOCUMENT_RECORDINGS_PATH
from purchase_cycle.evaluation import document_dataset as dd
from purchase_cycle.evaluation.email_eval import _cell, _ci, _groups, _mcnemar, _pct, load_baseline, rate
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.llm import SUPPLIER_DOCUMENT_EXTRACTION, InvalidModelOutput, MissingRecording, ModelClient

SUITE = "document_extraction"
BASELINE_PATH = EVALS_DIR / "baselines" / "document_extraction.json"
GATED = ("exact_match", "unit_price_accuracy")  # the spec absolute gates
METRICS = (
    "kind_accuracy",
    "reference_accuracy",
    "line_recall",
    "line_precision",
    "quantity_accuracy",
    "unit_price_accuracy",
    "exact_match",
    "critical_errors",
)
UNITS = {
    "kind_accuracy": "document",
    "reference_accuracy": "document",
    "line_recall": "expected line",
    "line_precision": "produced line",
    "quantity_accuracy": "expected line",
    "unit_price_accuracy": "priced line",
    "exact_match": "document",
    "critical_errors": "document",
}
PRICE_EPSILON = 1e-9
_CATALOG = [{"sku": p.sku, "name": p.name} for p in PRODUCTS]
_INDEX = web_form.catalog_index(_CATALOG)


def langsmith_dataset_name(split: str) -> str:
    return f"document-extraction-v{dd.DATASET_VERSION}-{split}"


def model_input(case: dict, base: Path) -> str:
    """The prompt text of one document, read as the product reads a file from the inbox folder."""
    if case["format"] == "txt":
        return reconciliation.model_text(case["body"][: reconciliation.MAX_DOCUMENT_TEXT])
    return reconciliation.model_text(reconciliation.document_text(Path(base) / case["file"]))


def _reference(text: str | None) -> str | None:
    found = reconciliation.PO_REFERENCE.search(text or "")
    return f"PO-{int(found.group(1)):04d}" if found else None


def _currency(text: str | None) -> str | None:
    return (text or "").strip().upper() or None


def _pair(expected: list[dict], produced: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair each produced line with an unpaired expected line of the same SKU; return pairs, missing and invented."""
    free = list(expected)
    pairs, invented = [], []
    for line in produced:
        sku = web_form.resolve_product(str(line["product"]), _INDEX, _CATALOG)
        match = next((e for e in free if e["sku"] == sku), None) if sku else None
        if match is None:
            invented.append(line)
        else:
            free.remove(match)
            pairs.append((match, line))
    return pairs, free, invented


def _price_ok(want: float | None, got: float | None) -> bool:
    return want is None or (got is not None and abs(want - got) <= dd.PRICE_TOLERANCE + PRICE_EPSILON)


def grade(case: dict, fields: dict | None, error: str | None = None) -> dict:
    """Grade one document; `fields` is None when the answer broke the schema or a product check."""
    expected = case["lines"]
    priced = [e for e in expected if e["unit_price"] is not None]
    if fields is None:
        counts = {
            "kind_accuracy": [0, 1],
            "reference_accuracy": [0, 1],
            "line_recall": [0, len(expected)],
            "line_precision": [0, 0],
            "quantity_accuracy": [0, len(expected)],
            "unit_price_accuracy": [0, len(priced)],
            "exact_match": [0, 1],
            "critical_errors": [1, 1],
        }
        return {"id": case["id"], "category": case["category"], "error": error, "counts": counts, "problems": []}
    pairs, missing, invented = _pair(expected, fields["lines"])
    wrong_quantity = [e["sku"] for e, g in pairs if abs(e["quantity"] - g["quantity"]) > PRICE_EPSILON]
    wrong_price = [e["sku"] for e, g in pairs if not _price_ok(e["unit_price"], g["unit_price"])]
    kind_ok = fields["kind"] == case["kind"]
    reference_ok = _reference(fields["po_reference"]) == case["po_reference"]
    currency_ok = _currency(fields["currency"]) == _currency(case["currency"])
    critical = bool(missing or invented or wrong_quantity or wrong_price)
    exact = kind_ok and reference_ok and currency_ok and not critical
    problems = (
        [f"missing {e['sku']}" for e in missing]
        + [f"invented '{g['product']}'" for g in invented]
        + [f"quantity {sku}" for sku in wrong_quantity]
        + [f"unit price {sku}" for sku in wrong_price]
    )
    if not kind_ok:
        problems.append(f"kind {fields['kind']}")
    if not reference_ok:
        problems.append(f"reference {fields['po_reference']}")
    if not currency_ok:
        problems.append(f"currency {fields['currency']}")
    counts = {
        "kind_accuracy": [int(kind_ok), 1],
        "reference_accuracy": [int(reference_ok), 1],
        "line_recall": [len(pairs), len(expected)],
        "line_precision": [len(pairs), len(fields["lines"])],
        "quantity_accuracy": [len(expected) - len(missing) - len(wrong_quantity), len(expected)],
        "unit_price_accuracy": [
            len(priced) - len([e for e in missing if e["unit_price"] is not None]) - len(wrong_price),
            len(priced),
        ],
        "exact_match": [int(exact), 1],
        "critical_errors": [int(critical), 1],
    }
    return {"id": case["id"], "category": case["category"], "error": None, "counts": counts, "problems": problems}


def run_document(client: ModelClient, case: dict, base: Path) -> dict:
    try:
        fields = client.extract(model_input(case, base), case_id=case["id"]).model_dump()
        reconciliation.check_answer(fields)
    except (InvalidModelOutput, reconciliation.DocumentRejected) as error:
        return grade(case, None, f"{type(error).__name__}: {error}")
    return grade(case, fields)


def summarise(results: list[dict]) -> dict:
    """Each metric summed over its own counting unit, with its Wilson interval."""
    return {m: rate(sum(r["counts"][m][0] for r in results), sum(r["counts"][m][1] for r in results)) for m in METRICS}


def _document_flag(counts: dict, metric: str) -> bool | None:
    """Per-document view of a metric for McNemar: all of its units right, None when it does not apply."""
    hits, n = counts[metric]
    return None if n == 0 else hits == n


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per document against the stored baseline for the two gated metrics."""
    stored = baseline["documents"]
    tests = {}
    for m in GATED:
        pairs = []
        for r in results:
            now = _document_flag(r["counts"], m)
            if r["id"] in stored and now is not None:
                pairs.append((_document_flag(stored[r["id"]]["counts"], m), now))
        tests[m] = _mcnemar(pairs)
    failures = [
        f"{m} dropped against the baseline (lost {t['lost']}, gained {t['gained']}, McNemar p={t['p']:.4f})"
        for m, t in tests.items()
        if t["lost"] > t["gained"] and t["p"] < ALPHA
    ]
    if set(stored) != {r["id"] for r in results}:
        failures.append("the evaluated documents differ from the baseline")
    return failures, tests


def absolute_gates(summary: dict, threshold: float | None) -> list[str]:
    if threshold is None:
        return ["no threshold set in the baseline"]
    return [
        f"{m} {summary[m]['value']:.1%} below threshold {threshold:.1%}"
        for m in GATED
        if summary[m]["value"] < threshold
    ]


def _failure_text(r: dict) -> str:
    if r["error"]:
        return f"extraction_failed: {r['error']}"
    return "; ".join(r["problems"]) + ("  CRITICAL" if r["counts"]["critical_errors"][0] else "")


def print_report(mode, split, summary, results, threshold, regression, baseline) -> None:
    print(f"{SUITE}  mode={mode}  split={split}  documents={len(results)}  model={MODEL_ID}")
    print(
        f"{'metric':<22}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    for m in METRICS:
        s = summary[m]
        if m in GATED:
            target, met = target_cells(s["value"], DEFAULT_THRESHOLD)
            thr = _pct(threshold) if threshold is not None else "none"
            result = "PASS" if threshold is not None and s["value"] >= threshold else "FAIL"
        elif m == "critical_errors":
            target, met = target_cells(s["value"], 0.0, "<=")
            thr, result = "none", "reported"
        else:
            target, met, thr, result = "none", "n/a", "none", "reported"
        print(
            f"{m:<22}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNITS[m]:<16}{target:<9}{met:<12}{thr:<11}{result}"
            + zero_event_note(s)
        )
    if baseline is None:
        print("regression vs baseline: no baseline stored")
    elif regression is None:
        print(f"regression vs baseline: skipped (the baseline holds the {baseline['split']} split, not {split})")
    else:
        parts = ", ".join(f"{m} lost {t['lost']} gained {t['gained']} p={t['p']:.2f}" for m, t in regression[1].items())
        verdict = "FAIL" if regression[0] else "no significant drop"
        print(f"regression vs baseline {baseline['measured_at']}: {verdict} ({parts})")
    by_category = _groups(results, "category", dd.CATEGORIES)
    print("per category:")
    for group, rows in by_category.items():
        s = summarise(rows)
        print(f"  {group:<18} documents={len(rows)}")
        for names in (METRICS[:3], METRICS[3:6], METRICS[6:]):
            print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in names))
    failures = [r for r in results if not r["counts"]["exact_match"][0]]
    print(f"failures: {len(failures)}")
    for category in by_category:
        for r in (f for f in failures if f["category"] == category):
            print(f"  {category:<18} {r['id']}  {_failure_text(r)}")


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline with per-document results; only called once the gated metrics reach 95%."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": dd.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if document exact match and unit price accuracy reach 95% on the test split; "
        "otherwise the owner decides (deviation)",
        "metrics": summary,
        "documents": {
            r["id"]: {"category": r["category"], "error": r["error"], "problems": r["problems"], "counts": r["counts"]}
            for r in results
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_path: Path = dd.DATASET_PATH,
    recordings_path: Path = SUPPLIER_DOCUMENT_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
) -> int:
    """Run the document extraction evaluation and print the report; returns the process exit code."""
    cases = [c for c in dd.load_dataset(dataset_path) if split == "all" or c["split"] == split]
    base = Path(dataset_path).parent
    client = ModelClient(mode, [], recordings_path, task=SUPPLIER_DOCUMENT_EXTRACTION)
    try:
        with tracing_context(enabled=False), ContextThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda c: run_document(client, c, base), cases))
    except MissingRecording as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    finally:
        client.save_recordings()

    summary = summarise(results)
    baseline = load_baseline(baseline_path)
    meets = all(summary[m]["value"] >= DEFAULT_THRESHOLD for m in GATED)
    if set_baseline and meets:
        save_baseline(baseline_path, mode, split, summary, results)
        baseline = load_baseline(baseline_path)
    threshold = baseline["threshold"] if baseline else None
    if not baseline:
        regression = (["no baseline stored"], {})
    elif baseline["split"] == split:
        regression = regression_gate(results, baseline)
    else:
        regression = None
    print_report(mode, split, summary, results, threshold, regression, baseline)
    if client.usage:
        total = {k: sum(u[k] for u in client.usage) for k in client.usage[0]}
        print(
            f"tokens: calls={len(client.usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )
    if set_baseline and not meets:
        print(
            "STOP: Haiku did not reach 95% on document exact match and unit price accuracy; the stored baseline was not changed and the owner decides the threshold (deviation)."
        )
        return 3
    failures = absolute_gates(summary, threshold) + (regression[0] if regression else [])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = dd.DATASET_PATH) -> int:
    from langsmith import Client

    client = Client()
    cases = dd.load_dataset(dataset_path)
    base = Path(dataset_path).parent
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        rows = [c for c in cases if c["split"] == split]
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("document_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(c["id"] for c in rows):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose document ids differ "
                    f"from the {len(rows)} local documents; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name,
            description=f"Supplier document extraction golden dataset v{dd.DATASET_VERSION}, {split} split (synthetic)",
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"document_id": c["id"], "document_text": model_input(c, base)},
                    "outputs": {
                        "kind": c["kind"],
                        "po_reference": c["po_reference"],
                        "currency": c["currency"],
                        "lines": [
                            {"sku": x["sku"], "quantity": x["quantity"], "unit_price": x["unit_price"]}
                            for x in c["lines"]
                        ],
                    },
                    "metadata": {"category": c["category"], "dataset_version": c["dataset_version"]},
                }
                for c in rows
            ],
        )
        print(f"{name}: uploaded {len(rows)} examples")
    return 0
