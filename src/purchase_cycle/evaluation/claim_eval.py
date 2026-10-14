"""Evaluation runs of the claim drafting on the `claim_drafting` dataset (phase 09, C13, C14).

Every case goes through the product call: the facts prompt and the model body. The grader scores the model draft
before any template fallback, with the fact check of `claims`; all graders are deterministic and a schema failure
grades as `draft_failed`.
"""

import json
import sys
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import claims
from purchase_cycle.config import CLAIM_DRAFTING_RECORDINGS_PATH, EVALS_DIR, MODEL_ID
from purchase_cycle.evaluation import claim_dataset as cd
from purchase_cycle.evaluation.email_eval import _cell, _ci, _groups, _mcnemar, _pct, load_baseline, rate
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.llm import CLAIM_DRAFTING, InvalidModelOutput, MissingRecording, ModelClient

SUITE = "claim_drafting"
BASELINE_PATH = EVALS_DIR / "baselines" / "claim_drafting.json"
GATED = ("case_pass_rate",)  # the spec absolute gate
METRICS = (
    "reference_accuracy",
    "discrepancy_coverage",
    "amount_in_dispute_accuracy",
    "action_accuracy",
    "invented_amount_rate",
    "case_pass_rate",
    "critical_errors",
)
UNITS = {
    "reference_accuracy": "claim",
    "discrepancy_coverage": "discrepancy row",
    "amount_in_dispute_accuracy": "claim",
    "action_accuracy": "claim",
    "invented_amount_rate": "claim",
    "case_pass_rate": "claim",
    "critical_errors": "claim",
}


def langsmith_dataset_name(split: str) -> str:
    return f"claim-drafting-v{cd.DATASET_VERSION}-{split}"


def grade(case: dict, body: str | None, error: str | None = None) -> dict:
    """Grade one claim; `body` is None when the model answer broke the schema."""
    facts = case["facts"]
    rows = facts["differences"]
    text = body or ""
    problems = claims.check_draft(text, facts) if body is not None else ["no draft"]
    amounts = claims.money_amounts(text)
    covered = 0
    for row in rows:
        item = row["sku"] or row["text"]
        stated = row["amount"] is None or any(abs(a - abs(row["amount"])) < 0.005 for a in amounts)
        covered += bool(item and item in text and stated)
    reference_ok = not any(p.startswith(("missing invoice number", "missing purchase order")) for p in problems)
    amount_ok = not any(p.startswith("missing amount in dispute") for p in problems)
    invented = any(p.startswith("invented amount") for p in problems)
    return {
        "id": case["id"],
        "category": case["category"],
        "error": error,
        "body": body,
        "problems": problems,
        "rows": len(rows),
        "rows_covered": covered,
        "discrepancy_coverage": covered / len(rows) if rows else 1.0,
        "reference_accuracy": body is not None and reference_ok,
        "amount_in_dispute_accuracy": body is not None and amount_ok,
        "action_accuracy": body is not None and not any(p.startswith("missing request") for p in problems),
        "invented_amount_rate": invented,
        "case_pass_rate": not problems,
        # A sent claim with a missing difference, a wrong or invented amount or a wrong reference would be wrong.
        "critical_errors": body is None or covered < len(rows) or invented or not (reference_ok and amount_ok),
    }


def run_case(client: ModelClient, case: dict) -> dict:
    try:
        body = client.extract(cd.model_text(case), case_id=case["id"]).body
    except InvalidModelOutput as error:
        return grade(case, None, f"{type(error).__name__}: {error}")
    return grade(case, body)


def summarise(results: list[dict]) -> dict:
    """Each metric over its unit, with its Wilson interval; coverage counts discrepancy rows."""
    summary = {}
    for m in METRICS:
        if m == "discrepancy_coverage":
            summary[m] = rate(sum(r["rows_covered"] for r in results), sum(r["rows"] for r in results))
        else:
            summary[m] = rate(sum(1 for r in results if r[m]), len(results))
    return summary


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per claim against the stored baseline for the gated metric."""
    stored = baseline["cases"]
    tests = {m: _mcnemar([(stored[r["id"]][m], r[m]) for r in results if r["id"] in stored]) for m in GATED}
    failures = [
        f"{m} dropped against the baseline (lost {t['lost']}, gained {t['gained']}, McNemar p={t['p']:.4f})"
        for m, t in tests.items()
        if t["lost"] > t["gained"] and t["p"] < ALPHA
    ]
    if set(stored) != {r["id"] for r in results}:
        failures.append("the evaluated claims differ from the baseline")
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
    print(f"{SUITE}  mode={mode}  split={split}  claims={len(results)}  model={MODEL_ID}")
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
        print(f"  {group:<26} claims={len(rows)}")
        for names in (METRICS[:3], METRICS[3:]):
            print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in names))
    failures = [r for r in results if not r["case_pass_rate"]]
    print(f"failures: {len(failures)}")
    for category in by_category:
        for r in (f for f in failures if f["category"] == category):
            print(f"  {category:<26} {r['id']}  {_failure_text(r)}")


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline with per-claim results; only called once the case pass rate reaches 95%."""
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
    recordings_path: Path = CLAIM_DRAFTING_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
) -> int:
    """Run the claim drafting evaluation and print the report; returns the process exit code."""
    cases = [c for c in cd.load_dataset(dataset_path) if split == "all" or c["split"] == split]
    client = ModelClient(mode, [], recordings_path, task=CLAIM_DRAFTING)
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
            "STOP: Haiku did not reach 95% on the claim case pass rate; the stored baseline was not changed and the owner decides the threshold (deviation)."
        )
        return 3
    failures = absolute_gates(summary, threshold) + (regression[0] if regression else [])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = cd.DATASET_PATH) -> int:
    from langsmith import Client

    client = Client()
    cases = cd.load_dataset(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        rows = [c for c in cases if c["split"] == split]
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("claim_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(c["id"] for c in rows):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose claim ids differ "
                    f"from the {len(rows)} local claims; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name, description=f"Claim drafting golden dataset v{cd.DATASET_VERSION}, {split} split (synthetic)"
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"claim_id": c["id"], "facts": cd.model_text(c)},
                    "outputs": {"amount_in_dispute": c["facts"]["amount_in_dispute"]},
                    "metadata": {"category": c["category"], "dataset_version": c["dataset_version"]},
                }
                for c in rows
            ],
        )
        print(f"{name}: uploaded {len(rows)} examples")
    return 0
