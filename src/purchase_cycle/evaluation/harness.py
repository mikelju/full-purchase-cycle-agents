"""Evaluation runs of the order line extraction graph: graders, report and gates."""

import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import db
from purchase_cycle.config import EVALS_DIR, MODEL_ID, RECORDINGS_PATH
from purchase_cycle.evaluation import dataset as ds
from purchase_cycle.evaluation.planning import DATASET_VERSION, read_jsonl
from purchase_cycle.evaluation.stats import mcnemar_exact, wilson_interval
from purchase_cycle.graph import build_graph
from purchase_cycle.llm import InvalidModelOutput, MissingRecording, ModelClient

BASELINE_PATH = EVALS_DIR / "baselines" / "order_line_extraction.json"
METRICS = ("product_accuracy", "quantity_accuracy")
DEFAULT_THRESHOLD = 0.95
ALPHA = 0.05


def langsmith_dataset_name(split: str) -> str:
    return f"order-line-extraction-v{DATASET_VERSION}-{split}"


def grade(case: dict, sku, quantity, error: str | None = None) -> dict:
    return {
        "id": case["id"],
        "category": case["category"],
        "expected_sku": case["expected_sku"],
        "expected_quantity": case["expected_quantity"],
        "sku": sku,
        "quantity": quantity,
        "error": error,
        "product_accuracy": error is None and sku == case["expected_sku"],
        "quantity_accuracy": error is None and quantity == case["expected_quantity"],
    }


def _run_one(graph, case: dict) -> dict:
    try:
        state = graph.invoke({"sentence": case["sentence"], "case_id": case["id"]})
    except InvalidModelOutput as error:
        return grade(case, None, None, str(error))
    return grade(case, state["extracted"]["sku"], state["extracted"]["quantity"])


def _new_graph(mode: str, recordings_path: Path, db_path: Path):
    conn = db.connect(db_path)
    db.seed(conn)
    client = ModelClient(mode, db.catalog_rows(conn), recordings_path)
    conn.close()
    return client, build_graph(client, db_path)


def run_cases(graph, cases: list[dict], workers: int) -> list[dict]:
    # The context-aware pool carries a surrounding `tracing_context` into the workers.
    with ContextThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda c: _run_one(graph, c), cases))


def run_experiment(graph, cases: list[dict], split: str, mode: str, workers: int) -> tuple[list[dict], str]:
    """Run the cases through LangSmith `evaluate` so the run is logged as an experiment."""
    from langsmith import Client

    by_id = {c["id"]: c for c in cases}
    results, errors = {}, {}

    def target(inputs: dict) -> dict:
        try:
            result = _run_one(graph, by_id[inputs["case_id"]])
        except Exception as error:
            # `evaluate` logs target exceptions and carries on, so keep them for the final message.
            errors[inputs["case_id"]] = repr(error)
            raise
        results[inputs["case_id"]] = result
        return {"sku": result["sku"], "quantity": result["quantity"], "error": result["error"]}

    def product_accuracy(outputs: dict, reference_outputs: dict) -> dict:
        return {
            "key": "product_accuracy",
            "score": int(outputs.get("error") is None and outputs["sku"] == reference_outputs["sku"]),
        }

    def quantity_accuracy(outputs: dict, reference_outputs: dict) -> dict:
        return {
            "key": "quantity_accuracy",
            "score": int(outputs.get("error") is None and outputs["quantity"] == reference_outputs["quantity"]),
        }

    experiment = Client().evaluate(
        target,
        data=langsmith_dataset_name(split),
        evaluators=[product_accuracy, quantity_accuracy],
        experiment_prefix=f"order-line-extraction-{MODEL_ID}",
        metadata={"model": MODEL_ID, "mode": mode, "split": split, "dataset_version": DATASET_VERSION},
        max_concurrency=workers,
        blocking=True,
    )
    missing = set(by_id) - set(results)
    raised = sorted(missing & set(errors))
    absent = missing - set(errors)
    problems = []
    if raised:
        problems.append(f"{len(raised)} cases raised in the target, first {raised[0]}: {errors[raised[0]]}")
    if absent:
        problems.append(
            f"LangSmith dataset {langsmith_dataset_name(split)} lacks {len(absent)} local cases; run eval-upload"
        )
    if problems:
        raise RuntimeError("; ".join(problems))
    return [results[c["id"]] for c in cases], experiment.experiment_name


def summarise(results: list[dict]) -> dict:
    summary = {}
    for metric in METRICS:
        hits = sum(r[metric] for r in results)
        low, high = wilson_interval(hits, len(results))
        summary[metric] = {
            "value": hits / len(results) if results else 0.0,
            "low": low,
            "high": high,
            "hits": hits,
            "n": len(results),
        }
    return summary


def absolute_gate(summary: dict, threshold: float | None) -> list[str]:
    if threshold is None:
        return ["no threshold set in the baseline"]
    return [
        f"{m} {summary[m]['value']:.1%} below threshold {threshold:.1%}"
        for m in METRICS
        if summary[m]["value"] < threshold
    ]


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    failures, tests = [], {}
    stored = baseline["cases"]
    for metric in METRICS:
        lost = sum(1 for r in results if r["id"] in stored and stored[r["id"]][metric] and not r[metric])
        gained = sum(1 for r in results if r["id"] in stored and not stored[r["id"]][metric] and r[metric])
        p = mcnemar_exact(lost, gained)
        tests[metric] = {"lost": lost, "gained": gained, "p": p}
        if lost > gained and p < ALPHA:
            failures.append(f"{metric} dropped against the baseline (lost {lost}, gained {gained}, McNemar p={p:.4f})")
    if set(stored) != {r["id"] for r in results}:
        failures.append("the evaluated cases differ from the baseline cases")
    return failures, tests


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def print_report(mode, split, summary, results, threshold, regression, baseline, contrast, experiment) -> None:
    print(f"order_line_extraction  mode={mode}  split={split}  cases={len(results)}  model={MODEL_ID}")
    print(f"{'metric':<20}{'value':<8}{'95% CI':<16}{'threshold':<11}result")
    for m in METRICS:
        s = summary[m]
        ci = f"[{s['low'] * 100:.1f}, {s['high'] * 100:.1f}]"
        thr = _pct(threshold) if threshold is not None else "none"
        result = "PASS" if threshold is not None and s["value"] >= threshold else "FAIL"
        print(f"{m:<20}{_pct(s['value']):<8}{ci:<16}{thr:<11}{result}")
    if baseline is None:
        print("regression vs baseline: no baseline stored")
    elif regression is None:
        print(f"regression vs baseline: skipped (the baseline holds the {baseline['split']} split, not {split})")
    else:
        parts = ", ".join(f"{m} lost {t['lost']} gained {t['gained']} p={t['p']:.2f}" for m, t in regression[1].items())
        verdict = "FAIL" if regression[0] else "no significant drop"
        print(f"regression vs baseline {baseline['measured_at']}: {verdict} ({parts})")
    by_category = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
    print("per category:")
    for category in ds_categories(by_category):
        rows = by_category[category]
        cells = []
        for m in METRICS:
            hits = sum(r[m] for r in rows)
            low, high = wilson_interval(hits, len(rows))
            cells.append(f"{m.split('_')[0]} {_pct(hits / len(rows)):>6} [{low * 100:.1f}, {high * 100:.1f}]")
        print(f"  {category:<16} n={len(rows):<4} " + "   ".join(cells))
    failures = [r for r in results if not (r["product_accuracy"] and r["quantity_accuracy"])]
    print(f"failures: {len(failures)}")
    for category in ds_categories(by_category):
        for r in (f for f in failures if f["category"] == category):
            got = f"error: {r['error']}" if r["error"] else f"got {r['sku']} x {r['quantity']}"
            print(f"  {category:<16} {r['id']}  expected {r['expected_sku']} x {r['expected_quantity']}, {got}")
    if contrast is not None:
        cs = summarise(contrast)
        print(
            f"contrast set (hand-curated, n={len(contrast)}): product {_pct(cs['product_accuracy']['value'])}, quantity {_pct(cs['quantity_accuracy']['value'])}"
        )
    if experiment:
        print(f"langsmith experiment: {experiment}")


def ds_categories(by_category) -> list[str]:
    from purchase_cycle.evaluation.planning import CATEGORIES

    return [c for c in CATEGORIES if c in by_category] + sorted(set(by_category) - set(CATEGORIES))


def load_baseline(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline; it is only called once both metrics reach the default threshold."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if both metrics reach 95% on the test split; otherwise the owner decides (deviation)",
        "metrics": {m: {k: summary[m][k] for k in ("value", "low", "high", "hits", "n")} for m in METRICS},
        "cases": {r["id"]: {"sku": r["sku"], "quantity": r["quantity"], **{m: r[m] for m in METRICS}} for r in results},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_path: Path = ds.DATASET_PATH,
    contrast_path: Path = ds.CONTRAST_PATH,
    recordings_path: Path = RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
    log_experiment: bool | None = None,
) -> int:
    """Run one evaluation and print the report; returns the process exit code."""
    cases = [c for c in read_jsonl(dataset_path) if split == "all" or c["split"] == split]
    contrast_cases = read_jsonl(contrast_path) if contrast_path.exists() and split != "dev" else []
    if log_experiment is None:
        log_experiment = mode != "replay" and split == "test" and bool(os.environ.get("LANGSMITH_API_KEY"))

    with tempfile.TemporaryDirectory() as tmp:
        client, graph = _new_graph(mode, recordings_path, Path(tmp) / "eval.db")
        experiment = None
        try:
            if log_experiment:
                results, experiment = run_experiment(graph, cases, split, mode, workers)
            else:
                # Development tuning stays out of LangSmith to respect the free trace allowance.
                with tracing_context(enabled=False):
                    results = run_cases(graph, cases, workers)
            # The contrast set is reported apart and never logged as an experiment.
            with tracing_context(enabled=False):
                contrast = run_cases(graph, contrast_cases, workers) if contrast_cases else None
        except MissingRecording as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
        finally:
            client.save_recordings()

    summary = summarise(results)
    baseline = load_baseline(baseline_path)
    meets = all(summary[m]["value"] >= DEFAULT_THRESHOLD for m in METRICS)
    if set_baseline and meets:
        save_baseline(baseline_path, mode, split, summary, results)
        baseline = load_baseline(baseline_path)
    threshold = baseline["threshold"] if baseline else None
    if not baseline:
        regression = (["no baseline stored"], {})
    elif baseline["split"] == split:
        regression = regression_gate(results, baseline)
    else:
        # The stored per-case results only cover the baseline split; the threshold gate still applies.
        regression = None
    print_report(mode, split, summary, results, threshold, regression, baseline, contrast, experiment)
    if client.usage:
        total = {k: sum(u[k] for u in client.usage) for k in client.usage[0]}
        print(
            f"tokens: calls={len(client.usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )

    failures = absolute_gate(summary, threshold) + (regression[0] if regression else [])
    if set_baseline and not meets:
        print(
            "STOP: Haiku did not reach 95% on both metrics; the stored baseline was not changed and the owner chooses a 90% threshold or another model (deviation)."
        )
        return 3
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = ds.DATASET_PATH) -> int:
    from langsmith import Client

    client = Client()
    cases = read_jsonl(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        rows = [c for c in cases if c["split"] == split]
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("case_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(c["id"] for c in rows):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose case ids differ "
                    f"from the {len(rows)} local cases; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name, description=f"Order line extraction golden dataset v{DATASET_VERSION}, {split} split (synthetic)"
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"sentence": c["sentence"], "case_id": c["id"]},
                    "outputs": {"sku": c["expected_sku"], "quantity": c["expected_quantity"]},
                    "metadata": {"category": c["category"], "trap": c["trap"], "dataset_version": c["dataset_version"]},
                }
                for c in rows
            ],
        )
        print(f"{name}: uploaded {len(rows)} examples")
    return 0


def add_run_commands(sub, modes) -> None:
    run = sub.add_parser("eval", help="run the evaluations on their golden datasets")
    run.add_argument("--suite", choices=(*SUITES, "all"), default="all", help="evaluation to run (default: all)")
    run.add_argument("--mode", choices=modes, default="replay")
    run.add_argument("--split", choices=("dev", "test", "all"), default="test")
    run.add_argument("--workers", type=int, default=8)
    run.add_argument(
        "--set-baseline", action="store_true", help="store this run as the baseline (test split, real model)"
    )
    run.set_defaults(handler=cmd_eval)
    upload = sub.add_parser("eval-upload", help="upload the dataset splits to LangSmith")
    upload.add_argument("--suite", choices=SUITES, default="order_line_extraction")
    upload.set_defaults(handler=lambda args: _suite(args.suite).upload_datasets())


SUITES = ("order_line_extraction", "web_form_matching")


def _suite(name: str):
    if name == "web_form_matching":
        from purchase_cycle.evaluation import web_form_eval

        return web_form_eval
    return sys.modules[__name__]


def cmd_eval(args) -> int:
    if args.set_baseline and (args.mode != "record" or args.split != "test" or args.suite == "all"):
        print(
            "Error: a baseline is measured for one --suite on the test split with the real model (--mode record)",
            file=sys.stderr,
        )
        return 2
    names = SUITES if args.suite == "all" else (args.suite,)
    codes = []
    for n, name in enumerate(names):
        if n:
            print()
        codes.append(_suite(name).evaluate(args.mode, args.split, set_baseline=args.set_baseline, workers=args.workers))
    # The worst exit code wins: 1 for a failed gate, 2 for missing recordings, 3 for a stopped baseline.
    return max(codes)
