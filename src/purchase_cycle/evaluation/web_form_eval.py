"""Evaluation runs of the web form subgraph on the `web_form_matching` dataset: graders, report and gates.

Every submission of the dataset goes through the whole subgraph on a temporary
database, so the evaluated path is the one a real submission takes.
"""

import json
import os
import sys
import tempfile
from collections import defaultdict
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import db
from purchase_cycle.config import EVALS_DIR, MATCHING_RECORDINGS_PATH, MODEL_ID
from purchase_cycle.evaluation import web_form_dataset as wf
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import mcnemar_exact, target_cells, wilson_interval, zero_event_note
from purchase_cycle.llm import MATCHING, InvalidModelOutput, MissingRecording, ModelClient
from purchase_cycle.web_form import build_web_form_graph

SUITE = "web_form_matching"
BASELINE_PATH = EVALS_DIR / "baselines" / "web_form_matching.json"
METRIC = "product_accuracy"
DETERMINISTIC_CATEGORIES = wf.SCRIPT_CATEGORIES
_submission_calls: ContextVar[list[int]] = ContextVar("submission_calls")


def langsmith_dataset_name(split: str) -> str:
    return f"web-form-matching-v{wf.DATASET_VERSION}-{split}"


def grade(line: dict, sku, source: str | None, error: str | None = None) -> dict:
    return {
        "id": line["id"],
        "submission_id": line["submission_id"],
        "category": line["category"],
        "expected_sku": line["expected_sku"],
        "sku": sku,
        "source": source,
        "error": error,
        METRIC: error is None and sku == line["expected_sku"],
    }


def to_form(submission: dict) -> dict:
    return {
        "submission_id": submission["submission_id"],
        "customer_code": submission["customer_code"],
        "lines": [{"product": line["product_text"], "quantity": line["quantity"]} for line in submission["lines"]],
    }


class _CountingClient:
    """Delegates to the model client and counts the calls of the submission running in this context."""

    def __init__(self, client: ModelClient):
        self._client = client

    def extract(self, *args, **kwargs):
        counter = _submission_calls.get(None)
        if counter is not None:
            counter[0] += 1
        return self._client.extract(*args, **kwargs)


def run_submission(graph, submission: dict) -> tuple[list[dict], int]:
    """Grade the lines of one submission; the int counts the model calls of a submission whose answer the schema rejected."""
    counter = [0]
    token = _submission_calls.set(counter)
    try:
        state = graph.invoke({"submission": to_form(submission)})
    except InvalidModelOutput as error:
        return [grade(line, None, None, str(error)) for line in submission["lines"]], counter[0]
    finally:
        _submission_calls.reset(token)
    if state.get("errors"):
        return [grade(line, None, None, "; ".join(state["errors"])) for line in submission["lines"]], 0
    return [
        grade(line, got["sku"], got["source"]) for line, got in zip(submission["lines"], state["lines"], strict=True)
    ], 0


def _new_graph(mode: str, recordings_path: Path, db_path: Path):
    conn = db.connect(db_path)
    db.seed(conn)
    client = ModelClient(mode, db.catalog_rows(conn), recordings_path, task=MATCHING)
    conn.close()
    return client, build_web_form_graph(_CountingClient(client), db_path)


def run_submissions(graph, subs: list[dict], workers: int) -> tuple[list[dict], int]:
    with ContextThreadPoolExecutor(max_workers=workers) as pool:
        graded = list(pool.map(lambda s: run_submission(graph, s), subs))
    return [r for rows, _ in graded for r in rows], sum(calls for _, calls in graded)


def run_experiment(graph, subs: list[dict], split: str, mode: str, workers: int) -> tuple[list[dict], str, int]:
    """Run the submissions through LangSmith `evaluate` so the run is logged as an experiment."""
    from langsmith import Client

    by_id = {s["submission_id"]: s for s in subs}
    results, errors, invalid_calls = {}, {}, {}

    def target(inputs: dict) -> dict:
        try:
            rows, calls = run_submission(graph, by_id[inputs["submission_id"]])
        except Exception as error:
            errors[inputs["submission_id"]] = repr(error)
            raise
        results[inputs["submission_id"]] = rows
        invalid_calls[inputs["submission_id"]] = calls
        return {"skus": [r["sku"] for r in rows], "errors": [r["error"] for r in rows]}

    def line_accuracy(outputs: dict, reference_outputs: dict) -> dict:
        pairs = zip(outputs["skus"], outputs["errors"], reference_outputs["skus"], strict=True)
        hits = sum(error is None and got == expected for got, error, expected in pairs)
        return {"key": "line_product_accuracy", "score": hits / len(reference_outputs["skus"])}

    experiment = Client().evaluate(
        target,
        data=langsmith_dataset_name(split),
        evaluators=[line_accuracy],
        experiment_prefix=f"web-form-matching-{MODEL_ID}",
        metadata={"model": MODEL_ID, "mode": mode, "split": split, "dataset_version": wf.DATASET_VERSION},
        max_concurrency=workers,
        blocking=True,
    )
    missing = set(by_id) - set(results)
    raised = sorted(missing & set(errors))
    absent = missing - set(errors)
    problems = []
    if raised:
        problems.append(f"{len(raised)} submissions raised in the target, first {raised[0]}: {errors[raised[0]]}")
    if absent:
        problems.append(
            f"LangSmith dataset {langsmith_dataset_name(split)} lacks {len(absent)} local submissions; "
            "run eval-upload --suite web_form_matching"
        )
    if problems:
        raise RuntimeError("; ".join(problems))
    rows = [r for s in subs for r in results[s["submission_id"]]]
    return rows, experiment.experiment_name, sum(invalid_calls.values())


def rate(hits: int, n: int) -> dict:
    low, high = wilson_interval(hits, n)
    return {"value": hits / n if n else 0.0, "low": low, "high": high, "hits": hits, "n": n}


def summarise(results: list[dict]) -> dict:
    by_submission = defaultdict(list)
    for r in results:
        by_submission[r["submission_id"]].append(r[METRIC])
    return {
        METRIC: rate(sum(r[METRIC] for r in results), len(results)),
        "submission_accuracy": rate(sum(all(v) for v in by_submission.values()), len(by_submission)),
    }


def model_calls(results: list[dict]) -> dict[str, int]:
    calls = {c: 0 for c in wf.CATEGORIES if any(r["category"] == c for r in results)}
    for r in results:
        calls[r["category"]] += r["source"] == "model"
    return calls


def absolute_gates(summary: dict, calls: dict[str, int], threshold: float | None) -> list[str]:
    failures = []
    if threshold is None:
        failures.append("no threshold set in the baseline")
    elif summary[METRIC]["value"] < threshold:
        failures.append(f"line {METRIC} {summary[METRIC]['value']:.1%} below threshold {threshold:.1%}")
    failures += [
        f"{category} made {calls[category]} model calls; deterministic categories must make none"
        for category in DETERMINISTIC_CATEGORIES
        if calls.get(category)
    ]
    return failures


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    stored = baseline["lines"]
    lost = sum(1 for r in results if r["id"] in stored and stored[r["id"]][METRIC] and not r[METRIC])
    gained = sum(1 for r in results if r["id"] in stored and not stored[r["id"]][METRIC] and r[METRIC])
    p = mcnemar_exact(lost, gained)
    failures = []
    if lost > gained and p < ALPHA:
        failures.append(f"{METRIC} dropped against the baseline (lost {lost}, gained {gained}, McNemar p={p:.4f})")
    if set(stored) != {r["id"] for r in results}:
        failures.append("the evaluated lines differ from the baseline lines")
    return failures, {"lost": lost, "gained": gained, "p": p}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _ci(s: dict) -> str:
    return f"[{s['low'] * 100:.1f}, {s['high'] * 100:.1f}]"


def print_report(mode, split, summary, results, calls, threshold, regression, baseline, experiment) -> None:
    n_subs = summary["submission_accuracy"]["n"]
    print(f"{SUITE}  mode={mode}  split={split}  lines={len(results)}  submissions={n_subs}  model={MODEL_ID}")
    print(
        f"{'metric':<22}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    s = summary[METRIC]
    target, met = target_cells(s["value"], DEFAULT_THRESHOLD)
    thr = _pct(threshold) if threshold is not None else "none"
    result = "PASS" if threshold is not None and s["value"] >= threshold else "FAIL"
    print(
        f"{'line_' + METRIC:<22}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{'line':<16}{target:<9}{met:<12}{thr:<11}"
        f"{result}" + zero_event_note(s)
    )
    s = summary["submission_accuracy"]
    print(
        f"{'submission_accuracy':<22}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{'submission':<16}{'none':<9}"
        f"{'n/a':<12}{'none':<11}reported" + zero_event_note(s)
    )
    if baseline is None:
        print("regression vs baseline: no baseline stored")
    elif regression is None:
        print(f"regression vs baseline: skipped (the baseline holds the {baseline['split']} split, not {split})")
    else:
        t = regression[1]
        verdict = "FAIL" if regression[0] else "no significant drop"
        print(
            f"regression vs baseline {baseline['measured_at']}: {verdict} "
            f"({METRIC} lost {t['lost']} gained {t['gained']} p={t['p']:.2f})"
        )
    by_category = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
    print("per category:")
    for category in calls:
        rows = by_category[category]
        s = rate(sum(r[METRIC] for r in rows), len(rows))
        print(
            f"  {category:<16} n={len(rows):<4} product {_pct(s['value']):>6} {_ci(s):<14} model calls {calls[category]}"
        )
    failures = [r for r in results if not r[METRIC]]
    print(f"failures: {len(failures)}")
    for category in calls:
        for r in (f for f in failures if f["category"] == category):
            got = f"error: {r['error']}" if r["error"] else f"got {r['sku']}"
            print(f"  {category:<16} {r['id']}  expected {r['expected_sku']}, {got}")
    if experiment:
        print(f"langsmith experiment: {experiment}")


def load_baseline(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline; it is only called once line accuracy reaches the default threshold."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": wf.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if line product accuracy reaches 95% on the test split; otherwise the owner decides (deviation)",
        "metrics": summary,
        "lines": {r["id"]: {"sku": r["sku"], "source": r["source"], METRIC: r[METRIC]} for r in results},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_path: Path = wf.DATASET_PATH,
    recordings_path: Path = MATCHING_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
    log_experiment: bool | None = None,
) -> int:
    """Run the web form evaluation and print the report; returns the process exit code."""
    lines = [line for line in wf.load_dataset(dataset_path) if split == "all" or line["split"] == split]
    subs = wf.submissions(lines)
    if log_experiment is None:
        log_experiment = mode != "replay" and split == "test" and bool(os.environ.get("LANGSMITH_API_KEY"))

    with tempfile.TemporaryDirectory() as tmp:
        client, graph = _new_graph(mode, recordings_path, Path(tmp) / "eval.db")
        experiment = None
        try:
            if log_experiment:
                results, experiment, invalid_calls = run_experiment(graph, subs, split, mode, workers)
            else:
                with tracing_context(enabled=False):
                    results, invalid_calls = run_submissions(graph, subs, workers)
        except MissingRecording as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
        finally:
            client.save_recordings()

    summary = summarise(results)
    calls = model_calls(results)
    baseline = load_baseline(baseline_path)
    meets = summary[METRIC]["value"] >= DEFAULT_THRESHOLD
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
    print_report(mode, split, summary, results, calls, threshold, regression, baseline, experiment)
    if client.usage:
        total = {k: sum(u[k] for u in client.usage) for k in client.usage[0]}
        print(
            f"tokens: calls={len(client.usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )

    if invalid_calls:
        print(f"model calls in submissions with a schema-invalid answer: {invalid_calls}")

    failures = absolute_gates(summary, calls, threshold) + (regression[0] if regression else [])
    attributed = sum(calls.values()) + invalid_calls
    if attributed != client.calls:
        failures.append(
            f"the client made {client.calls} model calls but {attributed} are attributed to a graded line or an invalid answer"
        )
    if set_baseline and not meets:
        print(
            "STOP: Haiku did not reach 95% line product accuracy; the stored baseline was not changed and the owner decides the threshold or another model (deviation)."
        )
        return 3
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = wf.DATASET_PATH) -> int:
    from langsmith import Client

    client = Client()
    lines = wf.load_dataset(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        subs = wf.submissions([line for line in lines if line["split"] == split])
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("submission_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(s["submission_id"] for s in subs):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose submission ids differ "
                    f"from the {len(subs)} local submissions; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name, description=f"Web form matching golden dataset v{wf.DATASET_VERSION}, {split} split (synthetic)"
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"submission_id": s["submission_id"], "submission": to_form(s)},
                    "outputs": {"skus": [line["expected_sku"] for line in s["lines"]]},
                    "metadata": {
                        "line_ids": [line["id"] for line in s["lines"]],
                        "categories": [line["category"] for line in s["lines"]],
                        "dataset_version": wf.DATASET_VERSION,
                    },
                }
                for s in subs
            ],
        )
        print(f"{name}: uploaded {len(subs)} examples")
    return 0
