"""Evaluation runs of the response drafting on the `response_drafting` dataset (phase 11, C19, C20).

Every case goes through the product call: the facts prompt and the model body. The grader scores the model draft
before any template fallback, with the fact check of `customer_responses`; all graders are deterministic and a schema failure
grades as `draft_failed`.
"""

import json
import sys
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import customer_responses
from purchase_cycle.config import EVALS_DIR, MODEL_ID, RESPONSE_DRAFTING_RECORDINGS_PATH
from purchase_cycle.evaluation import response_dataset as cd
from purchase_cycle.evaluation.email_eval import _cell, _ci, _groups, _mcnemar, _pct, load_baseline, rate
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.llm import RESPONSE_DRAFTING, InvalidModelOutput, MissingRecording, ModelClient

SUITE = "response_drafting"
BASELINE_PATH = EVALS_DIR / "baselines" / "response_drafting.json"
GATED = ("case_pass_rate",)  # the spec absolute gate
METRICS = (
    "reference_accuracy",
    "incident_coverage",
    "document_accuracy",
    "invented_amount_rate",
    "case_pass_rate",
    "critical_errors",
)
UNITS = {
    "reference_accuracy": "response",
    "incident_coverage": "incident",
    "document_accuracy": "document",
    "invented_amount_rate": "response",
    "case_pass_rate": "response",
    "critical_errors": "response",
}
COUNTED = {"incident_coverage": ("incidents_covered", "incidents"), "document_accuracy": ("documents_ok", "documents")}


def grade(case: dict, body: str | None, error: str | None = None) -> dict:
    """Grade one response; `body` is None when the model answer broke the schema."""
    facts = case["facts"]
    text = body or ""
    problems = customer_responses.check_response(text, facts) if body is not None else ["no draft"]
    # The model draft is scored before any template fallback, with the product fact check as the grader.
    incidents = sum(
        not any(p in (f"missing incident {i['sku']}", f"missing units {i['units']} of {i['sku']}") for p in problems)
        for i in facts["incidents"]
    )
    documents = sum(
        not any(p == f"missing document {d['number']}" or p.endswith(f"of {d['number']}") for p in problems)
        for d in facts["documents"]
    )
    reference_ok = not any(p.startswith(("missing invoice number", "missing delivery note number")) for p in problems)
    invented = any(p.startswith("invented amount") for p in problems)
    return {
        "id": case["id"],
        "category": case["category"],
        "error": error,
        "body": body,
        "problems": problems,
        "incidents": len(facts["incidents"]),
        "incidents_covered": incidents if body is not None else 0,
        "documents": len(facts["documents"]),
        "documents_ok": documents if body is not None else 0,
        "reference_accuracy": body is not None and reference_ok,
        "incident_coverage": incidents / len(facts["incidents"]) if body is not None else 0.0,
        "document_accuracy": documents / len(facts["documents"]) if body is not None else 0.0,
        "invented_amount_rate": invented,
        "case_pass_rate": not problems,
        # A sent response with a missing incident, a wrong or invented amount, a missing or wrong document total
        # or a wrong reference would be wrong.
        "critical_errors": body is None
        or incidents < len(facts["incidents"])
        or documents < len(facts["documents"])
        or invented
        or not reference_ok,
    }


def run_case(client: ModelClient, case: dict) -> dict:
    try:
        body = client.extract(cd.model_text(case), case_id=case["id"]).body
    except InvalidModelOutput as error:
        return grade(case, None, f"{type(error).__name__}: {error}")
    return grade(case, body)


def summarise(results: list[dict]) -> dict:
    """Each metric over its unit, with its Wilson interval; coverage counts incidents and documents."""
    summary = {}
    for m in METRICS:
        if m in COUNTED:
            hit, total = COUNTED[m]
            summary[m] = rate(sum(r[hit] for r in results), sum(r[total] for r in results))
        else:
            summary[m] = rate(sum(1 for r in results if r[m]), len(results))
    return summary


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per response against the stored baseline for the gated metric."""
    stored = baseline["cases"]
    tests = {m: _mcnemar([(stored[r["id"]][m], r[m]) for r in results if r["id"] in stored]) for m in GATED}
    failures = [
        f"{m} dropped against the baseline (lost {t['lost']}, gained {t['gained']}, McNemar p={t['p']:.4f})"
        for m, t in tests.items()
        if t["lost"] > t["gained"] and t["p"] < ALPHA
    ]
    if set(stored) != {r["id"] for r in results}:
        failures.append("the evaluated responses differ from the baseline")
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
        return f"draft_failed: {r['error']}"
    return "; ".join(r["problems"]) + ("  CRITICAL" if r["critical_errors"] else "")


def print_report(mode, split, summary, results, threshold, regression, baseline) -> None:
    print(f"{SUITE}  mode={mode}  split={split}  responses={len(results)}  model={MODEL_ID}")
    print(
        f"{'metric':<28}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<18}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    for m in METRICS:
        s = summary[m]
        if m in GATED:
            target, met = target_cells(s["value"], DEFAULT_THRESHOLD)
            thr = _pct(threshold) if threshold is not None else "none"
            result = "PASS" if threshold is not None and s["value"] >= threshold else "FAIL"
        elif m in ("critical_errors", "invented_amount_rate"):
            target, met = target_cells(s["value"], 0.0, "<=")
            thr, result = "none", "reported"
        else:
            target, met, thr, result = "none", "n/a", "none", "reported"
        print(
            f"{m:<28}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNITS[m]:<18}{target:<9}{met:<12}{thr:<11}{result}"
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
    by_category = _groups(results, "category", cd.CATEGORIES)
    print("per category:")
    for group, rows in by_category.items():
        s = summarise(rows)
        print(f"  {group:<26} responses={len(rows)}")
        for names in (METRICS[:3], METRICS[3:]):
            print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in names))
    failures = [r for r in results if not r["case_pass_rate"]]
    print(f"failures: {len(failures)}")
    for category in by_category:
        for r in (f for f in failures if f["category"] == category):
            print(f"  {category:<26} {r['id']}  {_failure_text(r)}")


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline with per-response results; only called once the case pass rate reaches 95%."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": cd.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if the case pass rate reaches 95% on the test split; otherwise the owner decides (deviation)",
        "metrics": summary,
        "cases": {
            r["id"]: {"body": r["body"], "error": r["error"], "problems": r["problems"], **{m: r[m] for m in METRICS}}
            for r in results
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_path: Path = cd.DATASET_PATH,
    recordings_path: Path = RESPONSE_DRAFTING_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
) -> int:
    """Run the response drafting evaluation and print the report; returns the process exit code."""
    cases = [c for c in cd.load_dataset(dataset_path) if split == "all" or c["split"] == split]
    client = ModelClient(mode, [], recordings_path, task=RESPONSE_DRAFTING)
    try:
        with tracing_context(enabled=False), ContextThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda c: run_case(client, c), cases))
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
            "STOP: Haiku did not reach 95% on the response case pass rate; the stored baseline was not changed and the owner decides the threshold (deviation)."
        )
        return 3
    failures = absolute_gates(summary, threshold) + (regression[0] if regression else [])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0
