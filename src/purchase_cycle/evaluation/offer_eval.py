"""Evaluation runs of the supplier offer extraction on the `offer_extraction` dataset (phase 07, C12, C13).

Every reply goes through the same steps as the product: the request and reply text, the model extraction and the
`quote_decision.assess` rule, so usability and its reason are computed and never taken from the model.
All graders are deterministic; a schema failure grades as `extraction_failed`.
"""

import json
import sys
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import quote_decision
from purchase_cycle.config import EVALS_DIR, MODEL_ID, OFFER_EXTRACTION_RECORDINGS_PATH
from purchase_cycle.evaluation import offer_dataset as od
from purchase_cycle.evaluation.email_eval import _cell, _ci, _groups, _mcnemar, _pct, load_baseline, rate
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.llm import OFFER_EXTRACTION, InvalidModelOutput, MissingRecording, ModelClient

SUITE = "offer_extraction"
BASELINE_PATH = EVALS_DIR / "baselines" / "offer_extraction.json"
GATED = ("exact_match", "unit_price_accuracy")  # the spec absolute gates
METRICS = (
    "offer_accuracy",
    "quantity_accuracy",
    "unit_price_accuracy",
    "currency_accuracy",
    "vat_accuracy",
    "lead_time_accuracy",
    "usability_accuracy",
    "exact_match",
    "critical_errors",
)
UNITS = {
    "offer_accuracy": "reply",
    "quantity_accuracy": "offering reply",
    "unit_price_accuracy": "priced reply",
    "currency_accuracy": "priced reply",
    "vat_accuracy": "priced reply",
    "lead_time_accuracy": "dated reply",
    "usability_accuracy": "reply",
    "exact_match": "reply",
    "critical_errors": "reply",
}


def langsmith_dataset_name(split: str) -> str:
    return f"offer-extraction-v{od.DATASET_VERSION}-{split}"


def grade(case: dict, fields: dict | None, error: str | None = None) -> dict:
    """Grade one reply; `fields` is None when the model answer broke the schema."""
    expected = {f: case["expected"].get(f) for f in od.GRADED}  # a decline labels only the fields it states
    if fields is None:
        usable, reason, net = False, quote_decision.EXTRACTION_FAILED, None
        got = dict.fromkeys(od.GRADED)
    else:
        got = fields
        usable, reason, net = quote_decision.assess(fields)
    ok = {f: od._same(f, expected[f], got.get(f)) for f in od.GRADED}
    priced = expected["unit_price"] is not None
    expected_net = case.get("expected_net_price")
    # A VAT misread leaves the stated price right but moves the net price that the comparison uses.
    net_wrong = usable and expected_net is not None and (net is None or abs(net - expected_net) > od.PRICE_TOLERANCE)
    r = {
        "id": case["id"],
        "category": case["category"],
        "error": error,
        "fields": {f: got.get(f) for f in od.GRADED},
        "usable": usable,
        "reason": reason,
        "net_unit_price": net,
        "offer_accuracy": ok["offers"],
        "quantity_accuracy": ok["quantity"] if expected["quantity"] is not None else None,
        "unit_price_accuracy": ok["unit_price"] if priced else None,
        "currency_accuracy": ok["currency"] if priced else None,
        "vat_accuracy": (ok["vat_included"] and ok["vat_rate"]) if priced else None,
        "lead_time_accuracy": (ok["lead_time_days"] and ok["working_days"])
        if expected["lead_time_days"] is not None
        else None,
        "usability_accuracy": usable == case["expected_usable"] and reason == case["expected_reason"],
        "exact_match": all(ok.values()),
        # A usable reply with a wrong price, quantity, currency or net price would reach a purchase order,
        # and so would a reply that must be unusable (missing VAT rate, no lead time) that the model made usable.
        "critical_errors": usable
        and (not case["expected_usable"] or not (ok["unit_price"] and ok["quantity"] and ok["currency"]) or net_wrong),
    }
    r["wrong_fields"] = [f for f in od.GRADED if not ok[f]]
    return r


def run_reply(client: ModelClient, case: dict) -> dict:
    try:
        fields = client.extract(od.model_text(case), case_id=case["id"]).model_dump()
    except InvalidModelOutput as error:
        return grade(case, None, f"{type(error).__name__}: {error}")
    return grade(case, fields)


def summarise(results: list[dict]) -> dict:
    """Each metric over the replies where it applies, with its Wilson interval."""
    summary = {}
    for m in METRICS:
        counted = [r[m] for r in results if r[m] is not None]
        summary[m] = rate(sum(counted), len(counted))
    return summary


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per reply against the stored baseline for the two gated metrics."""
    stored = baseline["replies"]
    tests = {
        m: _mcnemar([(stored[r["id"]][m], r[m]) for r in results if r["id"] in stored and r[m] is not None])
        for m in GATED
    }
    failures = [
        f"{m} dropped against the baseline (lost {t['lost']}, gained {t['gained']}, McNemar p={t['p']:.4f})"
        for m, t in tests.items()
        if t["lost"] > t["gained"] and t["p"] < ALPHA
    ]
    if set(stored) != {r["id"] for r in results}:
        failures.append("the evaluated replies differ from the baseline")
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
    wrong = ", ".join(r["wrong_fields"]) or "usability"
    return f"wrong {wrong}; usable={r['usable']} reason={r['reason']}" + ("  CRITICAL" if r["critical_errors"] else "")


def print_report(mode, split, summary, results, threshold, regression, baseline) -> None:
    print(f"{SUITE}  mode={mode}  split={split}  replies={len(results)}  model={MODEL_ID}")
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
    by_category = _groups(results, "category", od.CATEGORIES)
    print("per category:")
    for group, rows in by_category.items():
        s = summarise(rows)
        print(f"  {group:<18} replies={len(rows)}")
        print(
            "    "
            + "   ".join(f"{m} {_cell(s[m])}" for m in ("exact_match", "unit_price_accuracy", "usability_accuracy"))
        )
        print(
            "    "
            + "   ".join(f"{m} {_cell(s[m])}" for m in ("quantity_accuracy", "currency_accuracy", "critical_errors"))
        )
        print(
            "    " + "   ".join(f"{m} {_cell(s[m])}" for m in ("offer_accuracy", "vat_accuracy", "lead_time_accuracy"))
        )
    failures = [r for r in results if not r["exact_match"] or not r["usability_accuracy"]]
    print(f"failures: {len(failures)}")
    for category in by_category:
        for r in (f for f in failures if f["category"] == category):
            print(f"  {category:<18} {r['id']}  {_failure_text(r)}")


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline with per-reply results; only called once the gated metrics reach 95%."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": od.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if reply exact match and unit price accuracy reach 95% on the test split; "
        "otherwise the owner decides (deviation)",
        "metrics": summary,
        "replies": {
            r["id"]: {
                "fields": r["fields"],
                "usable": r["usable"],
                "reason": r["reason"],
                "error": r["error"],
                **{m: r[m] for m in METRICS},
            }
            for r in results
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_path: Path = od.DATASET_PATH,
    recordings_path: Path = OFFER_EXTRACTION_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
) -> int:
    """Run the offer extraction evaluation and print the report; returns the process exit code."""
    cases = [c for c in od.load_dataset(dataset_path) if split == "all" or c["split"] == split]
    client = ModelClient(mode, [], recordings_path, task=OFFER_EXTRACTION)
    try:
        with tracing_context(enabled=False), ContextThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda c: run_reply(client, c), cases))
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
            "STOP: Haiku did not reach 95% on reply exact match and unit price accuracy; the stored baseline was not changed and the owner decides the threshold (deviation)."
        )
        return 3
    failures = absolute_gates(summary, threshold) + (regression[0] if regression else [])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = od.DATASET_PATH) -> int:
    from langsmith import Client

    client = Client()
    cases = od.load_dataset(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        rows = [c for c in cases if c["split"] == split]
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("reply_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(c["id"] for c in rows):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose reply ids differ "
                    f"from the {len(rows)} local replies; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name,
            description=f"Supplier offer extraction golden dataset v{od.DATASET_VERSION}, {split} split (synthetic)",
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"reply_id": c["id"], "reply_text": od.model_text(c)},
                    "outputs": {**c["expected"], "usable": c["expected_usable"], "reason": c["expected_reason"]},
                    "metadata": {"category": c["category"], "dataset_version": c["dataset_version"]},
                }
                for c in rows
            ],
        )
        print(f"{name}: uploaded {len(rows)} examples")
    return 0
