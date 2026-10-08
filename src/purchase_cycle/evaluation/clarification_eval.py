"""Evaluation runs of the clarification step: graders, reports and gates of two suites.

`clarification_detection` runs each order through its channel graph without clarification,
then the runtime `detect` on the resulting lines, so it measures the doubt rules on real matcher
and extractor output. `clarification_answers` calls the interpreter directly with each case's
doubtful lines, candidates, written question and customer answer.
"""

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import db
from purchase_cycle.clarification import (
    AMBIGUOUS,
    QUANTITY,
    UNKNOWN,
    InvalidAnswer,
    answer_message,
    check_resolutions,
    detect,
    line_text,
)
from purchase_cycle.config import (
    CLARIFICATION_ANSWER_RECORDINGS_PATH,
    CLARIFICATION_DETECTION_EXTRACTION_RECORDINGS_PATH,
    CLARIFICATION_DETECTION_INTAKE_RECORDINGS_PATH,
    CLARIFICATION_DETECTION_MATCHING_RECORDINGS_PATH,
    EVALS_DIR,
    MODEL_ID,
)
from purchase_cycle.email_order import InvalidExtraction, build_email_order_graph
from purchase_cycle.evaluation import clarification_dataset as cd
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.planning import read_jsonl
from purchase_cycle.evaluation.stats import mcnemar_exact, wilson_interval
from purchase_cycle.llm import (
    CLARIFICATION_ANSWER,
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    MATCHING,
    InvalidModelOutput,
    MissingRecording,
    ModelClient,
)
from purchase_cycle.web_form import build_web_form_graph, match_key

DETECTION = "clarification_detection"
ANSWERS = "clarification_answers"
DETECTION_DATASET = cd.DETECTION_DIR / "dataset.jsonl"
ANSWERS_DATASET = cd.ANSWERS_DIR / "dataset.jsonl"
DETECTION_BASELINE = EVALS_DIR / "baselines" / "clarification_detection.json"
ANSWERS_BASELINE = EVALS_DIR / "baselines" / "clarification_answers.json"
DOUBT_TYPES = (AMBIGUOUS, UNKNOWN, QUANTITY)
KINDS = ("clear", "ambiguous", "unknown", "over_ceiling", "unsupported")
RECALLS = tuple(f"recall_{t}" for t in DOUBT_TYPES)
PRECISIONS = tuple(f"precision_{t}" for t in DOUBT_TYPES)
DETECTION_METRICS = (*RECALLS, *PRECISIONS, "false_question_rate")
ANSWER_METRICS = ("resolution_accuracy", "case_exact_match")
# Detection gates on the test split after deviation 04.1: recall of each doubt type at the level the owner
# accepted from the replay measured after the rule change (spec amended 2026-10-08), false question rate at most 5%.
DETECTION_THRESHOLDS = {
    "recall_ambiguous": 49 / 55,
    "recall_unknown": 50 / 53,
    "recall_quantity": 42 / 46,
    "false_question_rate": 1 - DEFAULT_THRESHOLD,
}


def rate(hits: int, n: int) -> dict:
    low, high = wilson_interval(hits, n)
    return {"value": hits / n if n else 0.0, "low": low, "high": high, "hits": hits, "n": n}


def _mcnemar(pairs: list[tuple[bool, bool]]) -> dict:
    lost = sum(1 for before, now in pairs if before and not now)
    gained = sum(1 for before, now in pairs if not before and now)
    return {"lost": lost, "gained": gained, "p": mcnemar_exact(lost, gained)}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _cell(s: dict) -> str:
    return f"{_pct(s['value'])} [{s['low'] * 100:.1f}, {s['high'] * 100:.1f}] n={s['n']}" if s["n"] else "n/a"


def _new_catalog(db_path: Path) -> list:
    conn = db.connect(db_path)
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    return catalog


# Detection


def align(expected: list[dict], produced: list[dict]) -> list[int | None]:
    """Index of the produced line each expected line maps to: same text first, then one text inside the other."""
    keys = [match_key(line_text(line)) for line in produced]
    used: set[int] = set()
    found: list[int | None] = []
    for line in expected:
        key = match_key(line["text"])
        same = [i for i, k in enumerate(keys) if i not in used and k == key]
        near = [i for i, k in enumerate(keys) if i not in used and k and (k in key or key in k)]
        pick = (same or near or [None])[0]
        if pick is not None:
            used.add(pick)
        found.append(pick)
    return found


def grade_order(case: dict, produced: list[dict], catalog: list, error: str | None = None) -> dict:
    """Grade the doubts `detect` raises on the produced lines against the expected doubts per line."""
    raised = {d["line_id"] - 1: d["types"] for d in detect(produced, catalog, case["channel"])} if not error else {}
    mapping = align(case["lines"], produced) if not error else [None] * len(case["lines"])
    line_rows = []
    for line, index in zip(case["lines"], mapping, strict=True):
        got = raised.get(index, []) if index is not None else []
        line_rows.append(
            {
                "id": line["line_id"],
                "kind": line["kind"],
                "channel": case["channel"],
                "expected": line["expected_doubts"],
                "raised": got,
                "aligned": index is not None,
            }
        )
    aligned = {i for i in mapping if i is not None}
    return {
        "id": case["id"],
        "channel": case["channel"],
        "has_doubt": case["has_doubt"],
        "error": error,
        "question": bool(raised),
        "lines": [{"text": line_text(p), "sku": p["sku"], "quantity": p["quantity"]} for p in produced],
        "line_rows": line_rows,
        # Doubts on produced lines no expected line maps to; each one is wrong for precision.
        "stray": [t for i, types in raised.items() if i not in aligned for t in types],
    }


def line_metrics(rows: list[dict], stray: list[str] = ()) -> dict:
    """Recall per expected doubt type and precision per raised doubt type over line rows."""
    summary = {}
    for t in DOUBT_TYPES:
        wanted = [row for row in rows if t in row["expected"]]
        summary[f"recall_{t}"] = rate(sum(t in row["raised"] for row in wanted), len(wanted))
        flagged = [row for row in rows if t in row["raised"]]
        right = sum(t in row["expected"] for row in flagged)
        summary[f"precision_{t}"] = rate(right, len(flagged) + sum(s == t for s in stray))
    return summary


def summarise_detection(results: list[dict]) -> dict:
    """Line metrics plus the question rate of the orders with no expected doubt."""
    summary = line_metrics([row for r in results for row in r["line_rows"]], [t for r in results for t in r["stray"]])
    no_doubt = [r for r in results if not r["has_doubt"]]
    summary["false_question_rate"] = rate(sum(r["question"] for r in no_doubt), len(no_doubt))
    return summary


def _limit(threshold: float | dict, metric: str, op: str) -> float:
    """Gate limit of one metric from a single threshold or from per-metric limits."""
    if isinstance(threshold, dict):
        return threshold[metric]
    return threshold if op == ">=" else 1 - threshold


def detection_gates(summary: dict, threshold: float | dict | None) -> list[str]:
    if threshold is None:
        return ["no threshold set in the baseline"]
    failures = [
        f"{m} {summary[m]['value']:.1%} below threshold {_limit(threshold, m, '>='):.1%}"
        for m in RECALLS
        if summary[m]["value"] < _limit(threshold, m, ">=") - 1e-9
    ]
    ceiling = _limit(threshold, "false_question_rate", "<=")
    if summary["false_question_rate"]["value"] > ceiling + 1e-9:
        failures.append(f"false_question_rate {summary['false_question_rate']['value']:.1%} above {ceiling:.1%}")
    return failures


def detection_meets(summary: dict) -> bool:
    return not detection_gates(summary, DETECTION_THRESHOLDS)


def _recall_pairs(results: list[dict]) -> dict[str, bool]:
    """Recall outcome per expected doubt, keyed line id and doubt type."""
    return {f"{row['id']}:{t}": t in row["raised"] for r in results for row in r["line_rows"] for t in row["expected"]}


def detection_regression(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per expected doubt of each line for detection recall."""
    stored, now = baseline["doubts"], _recall_pairs(results)
    test = _mcnemar([(stored[k], v) for k, v in now.items() if k in stored])
    failures = []
    if test["lost"] > test["gained"] and test["p"] < ALPHA:
        failures.append(
            f"detection recall dropped against the baseline (lost {test['lost']}, gained {test['gained']}, McNemar p={test['p']:.4f})"
        )
    if set(stored) != set(now):
        failures.append("the evaluated lines differ from the baseline lines")
    return failures, {"detection_recall": test}


def run_order(graph, case: dict, catalog: list, root: Path) -> dict:
    """Run one order through its channel graph without clarification and grade the doubts on its lines."""
    state: dict = {}
    try:
        if case["channel"] == cd.WEB_FORM:
            state = graph.invoke({"submission": case["submission"]})
        else:
            for snapshot in graph.stream({"email_path": str(root / case["file"])}, stream_mode="values"):
                state = snapshot
    except (InvalidModelOutput, InvalidExtraction) as error:
        return grade_order(case, [], catalog, str(error))
    if state.get("errors"):
        return grade_order(case, [], catalog, "; ".join(state["errors"]))
    return grade_order(case, state.get("lines", []), catalog)


def _detection_graphs(mode: str, paths: dict, db_path: Path):
    catalog = _new_catalog(db_path)
    clients = {name: ModelClient(mode, catalog, paths[name], task=task) for name, task in DETECTION_TASKS.items()}
    graphs = {
        cd.WEB_FORM: build_web_form_graph(clients["matching"], db_path),
        cd.EMAIL: build_email_order_graph(clients["intake"], clients["extraction"], db_path),
    }
    return catalog, list(clients.values()), graphs


DETECTION_TASKS = {"matching": MATCHING, "intake": EMAIL_INTAKE, "extraction": EMAIL_EXTRACTION}


def _wrong_rows(r: dict) -> list[dict]:
    return [row for row in r["line_rows"] if sorted(row["expected"]) != sorted(row["raised"])]


def _failure_text(r: dict) -> str:
    if r["error"]:
        return f"error: {r['error']}"
    parts = [
        f"{row['id']} expected {row['expected'] or 'none'}, raised {row['raised'] or 'none'}"
        + ("" if row["aligned"] else " (no produced line)")
        for row in _wrong_rows(r)
    ]
    if r["stray"]:
        parts.append(f"doubts on lines with no expected line: {', '.join(r['stray'])}")
    return "; ".join(parts)


def detection_failed(r: dict) -> bool:
    return bool(r["error"] or r["stray"] or _wrong_rows(r))


def _failure_kind(r: dict) -> str:
    """Category of a failed order: the kind of its first wrong line, else `error` or `stray`."""
    wrong = _wrong_rows(r)
    return wrong[0]["kind"] if wrong else "error" if r["error"] else "stray"


def print_detection(mode, split, summary, results, threshold, regression, baseline, experiment) -> None:
    lines = sum(len(r["line_rows"]) for r in results)
    print(f"{DETECTION}  mode={mode}  split={split}  orders={len(results)}  lines={lines}  model={MODEL_ID}")
    _print_table(summary, DETECTION_METRICS, threshold, {**{m: ">=" for m in RECALLS}, "false_question_rate": "<="})
    _print_regression(regression, baseline, split)
    print("per channel:")
    for channel in cd.CHANNELS:
        rows = [r for r in results if r["channel"] == channel]
        if rows:
            _print_group(channel, f"orders={len(rows)}", summarise_detection(rows), DETECTION_METRICS)
    print("per category (line kind):")
    all_rows = [row for r in results for row in r["line_rows"]]
    for kind in KINDS:
        rows = [row for row in all_rows if row["kind"] == kind]
        if rows:
            _print_group(kind, f"lines={len(rows)}", line_metrics(rows), (*RECALLS, *PRECISIONS))
    failures = [r for r in results if detection_failed(r)]
    print(f"failures: {len(failures)}")
    for kind in (*KINDS, "error", "stray"):
        for r in (f for f in failures if _failure_kind(f) == kind):
            print(f"  {kind:<14} {r['id']} ({r['channel']})  {_failure_text(r)}")
    if experiment:
        print(f"langsmith experiment: {experiment}")


def save_detection_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    _write_baseline(
        path,
        mode,
        split,
        summary,
        "recall of each doubt type at the level measured after the rule change of deviation 04.1, accepted by the owner "
        "on 2026-10-08 as a phase 03 extractor limitation (change 001); false question rate at most 5% on the test split",
        threshold=DETECTION_THRESHOLDS,
        orders={r["id"]: {k: r[k] for k in ("error", "question", "lines")} for r in results},
        doubts=_recall_pairs(results),
    )


def evaluate_detection(
    mode: str,
    split: str,
    *,
    dataset_path: Path = DETECTION_DATASET,
    matching_recordings_path: Path = CLARIFICATION_DETECTION_MATCHING_RECORDINGS_PATH,
    intake_recordings_path: Path = CLARIFICATION_DETECTION_INTAKE_RECORDINGS_PATH,
    extraction_recordings_path: Path = CLARIFICATION_DETECTION_EXTRACTION_RECORDINGS_PATH,
    baseline_path: Path = DETECTION_BASELINE,
    set_baseline: bool = False,
    workers: int = 8,
    log_experiment: bool | None = None,
) -> int:
    """Run the detection evaluation and print the report; returns the process exit code."""
    cases = [c for c in read_jsonl(dataset_path) if split == "all" or c["split"] == split]
    root = dataset_path.parent
    paths = {
        "matching": matching_recordings_path,
        "intake": intake_recordings_path,
        "extraction": extraction_recordings_path,
    }
    with tempfile.TemporaryDirectory() as tmp:
        catalog, clients, graphs = _detection_graphs(mode, paths, Path(tmp) / "eval.db")

        def run(case: dict) -> dict:
            return run_order(graphs[case["channel"]], case, catalog, root)

        outcome = _run(run, cases, clients, split, mode, workers, log_experiment, DETECTION, _detection_outputs)
    if isinstance(outcome, int):
        return outcome
    results, experiment = outcome
    return _finish(
        results,
        summarise_detection(results),
        mode,
        split,
        baseline_path,
        set_baseline,
        clients,
        experiment,
        meets=detection_meets,
        gates=detection_gates,
        regression=detection_regression,
        save=save_detection_baseline,
        report=print_detection,
        stop="recall of every doubt type at its accepted level and a false question rate of at most 5%",
    )


def _detection_outputs(r: dict) -> dict:
    return {"doubts": {row["id"]: row["raised"] for row in r["line_rows"]}, "error": r["error"]}


# Answers


def case_doubts(case: dict) -> list[dict]:
    """The doubt records the interpreter sees, built as `validate_case` builds them."""
    return cd.runtime_doubts(case, {str(d["line_id"]): d["text"] for d in case["doubts"]})


def resolution_right(expected: dict, got: dict | None) -> bool:
    if got is None or got["action"] != expected["action"]:
        return False
    return expected["action"] != "set" or (got["sku"], got["quantity"]) == (expected["sku"], expected["quantity"])


def grade_case(case: dict, resolutions: list[dict], error: str | None = None) -> dict:
    """Grade the interpreted resolutions per doubtful line; a stopped run gets every line wrong."""
    by_line = {r["line_id"]: r for r in resolutions} if not error else {}
    rows = [
        {
            "id": f"{case['id']}-L{d['line_id']}",
            "line_id": d["line_id"],
            "expected": d["expected"],
            "got": by_line.get(d["line_id"]),
            "resolution_accuracy": resolution_right(d["expected"], by_line.get(d["line_id"])),
        }
        for d in case["doubts"]
    ]
    return {
        "id": case["id"],
        "channel": case["channel"],
        "category": case["category"],
        "error": error,
        "resolutions": resolutions,
        "line_rows": rows,
        "case_exact_match": all(row["resolution_accuracy"] for row in rows),
    }


def run_case(client: ModelClient, case: dict, catalog: list) -> dict:
    """Ask the interpreter about one case and apply the runtime answer checks before grading."""
    doubts = case_doubts(case)
    try:
        read = client.extract(answer_message(doubts, case["question"], case["answer"]), case_id=case["id"])
        check_resolutions(read.resolutions, doubts, catalog)
    except (InvalidModelOutput, InvalidAnswer) as error:
        return grade_case(case, [], str(error))
    return grade_case(case, [r.model_dump() for r in read.resolutions])


def summarise_answers(results: list[dict]) -> dict:
    rows = [row for r in results for row in r["line_rows"]]
    return {
        "resolution_accuracy": rate(sum(row["resolution_accuracy"] for row in rows), len(rows)),
        "case_exact_match": rate(sum(r["case_exact_match"] for r in results), len(results)),
    }


def answer_gates(summary: dict, threshold: float | None) -> list[str]:
    if threshold is None:
        return ["no threshold set in the baseline"]
    s = summary["resolution_accuracy"]
    return [f"resolution_accuracy {s['value']:.1%} below threshold {threshold:.1%}"] if s["value"] < threshold else []


def answers_meet(summary: dict) -> bool:
    return not answer_gates(summary, DEFAULT_THRESHOLD)


def answers_regression(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per doubtful line for resolution accuracy."""
    stored = baseline["lines"]
    rows = [row for r in results for row in r["line_rows"]]
    test = _mcnemar(
        [(stored[row["id"]]["resolution_accuracy"], row["resolution_accuracy"]) for row in rows if row["id"] in stored]
    )
    failures = []
    if test["lost"] > test["gained"] and test["p"] < ALPHA:
        failures.append(
            f"resolution_accuracy dropped against the baseline (lost {test['lost']}, gained {test['gained']}, McNemar p={test['p']:.4f})"
        )
    if set(stored) != {row["id"] for row in rows}:
        failures.append("the evaluated lines differ from the baseline lines")
    return failures, {"resolution_accuracy": test}


def print_answers(mode, split, summary, results, threshold, regression, baseline, experiment) -> None:
    n = summary["resolution_accuracy"]["n"]
    print(f"{ANSWERS}  mode={mode}  split={split}  cases={len(results)}  doubtful_lines={n}  model={MODEL_ID}")
    _print_table(summary, ANSWER_METRICS, threshold, {"resolution_accuracy": ">="})
    _print_regression(regression, baseline, split)
    for title, key, order in (
        ("per channel", "channel", cd.CHANNELS),
        ("per category", "category", cd.ANSWER_CATEGORIES),
    ):
        print(f"{title}:")
        for group in order:
            rows = [r for r in results if r[key] == group]
            if rows:
                _print_group(group, f"cases={len(rows)}", summarise_answers(rows), ANSWER_METRICS)
    failures = [r for r in results if not r["case_exact_match"]]
    print(f"failures: {len(failures)}")
    for category in cd.ANSWER_CATEGORIES:
        for r in (f for f in failures if f["category"] == category):
            if r["error"]:
                text = f"error: {r['error']}"
            else:
                text = "; ".join(
                    f"line {row['line_id']} expected {_resolution(row['expected'])}, got {_resolution(row['got'])}"
                    for row in r["line_rows"]
                    if not row["resolution_accuracy"]
                )
            print(f"  {category:<18} {r['id']} ({r['channel']})  {text}")
    if experiment:
        print(f"langsmith experiment: {experiment}")


def _resolution(r: dict | None) -> str:
    if r is None:
        return "no resolution"
    return f"set {r['sku']} x {r['quantity']}" if r["action"] == "set" else r["action"]


def save_answers_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    _write_baseline(
        path,
        mode,
        split,
        summary,
        "95% if resolution accuracy reaches 95% on the test split; otherwise the owner decides (deviation 04.1)",
        cases={r["id"]: {k: r[k] for k in ("error", "resolutions", "case_exact_match")} for r in results},
        lines={
            row["id"]: {"resolution_accuracy": row["resolution_accuracy"]} for r in results for row in r["line_rows"]
        },
    )


def evaluate_answers(
    mode: str,
    split: str,
    *,
    dataset_path: Path = ANSWERS_DATASET,
    recordings_path: Path = CLARIFICATION_ANSWER_RECORDINGS_PATH,
    baseline_path: Path = ANSWERS_BASELINE,
    set_baseline: bool = False,
    workers: int = 8,
    log_experiment: bool | None = None,
) -> int:
    """Run the answer evaluation and print the report; returns the process exit code."""
    cases = [c for c in read_jsonl(dataset_path) if split == "all" or c["split"] == split]
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _new_catalog(Path(tmp) / "eval.db")
    client = ModelClient(mode, catalog, recordings_path, task=CLARIFICATION_ANSWER)

    def run(case: dict) -> dict:
        return run_case(client, case, catalog)

    outcome = _run(run, cases, [client], split, mode, workers, log_experiment, ANSWERS, _answer_outputs)
    if isinstance(outcome, int):
        return outcome
    results, experiment = outcome
    return _finish(
        results,
        summarise_answers(results),
        mode,
        split,
        baseline_path,
        set_baseline,
        [client],
        experiment,
        meets=answers_meet,
        gates=answer_gates,
        regression=answers_regression,
        save=save_answers_baseline,
        report=print_answers,
        stop="resolution accuracy",
    )


def _answer_outputs(r: dict) -> dict:
    return {"resolutions": r["resolutions"], "error": r["error"]}


# Shared run, report and gate steps


def _print_table(summary: dict, metrics, threshold: float | dict | None, gated: dict[str, str]) -> None:
    print(f"{'metric':<24}{'value':<8}{'95% CI':<16}{'n':<6}{'threshold':<11}result")
    for m in metrics:
        s = summary[m]
        ci = f"[{s['low'] * 100:.1f}, {s['high'] * 100:.1f}]"
        if m not in gated:
            thr, result = "none", "reported"
        elif threshold is None:
            thr, result = "none", "FAIL"
        else:
            limit = _limit(threshold, m, gated[m])
            ok = s["value"] >= limit - 1e-9 if gated[m] == ">=" else s["value"] <= limit + 1e-9
            thr, result = f"{gated[m]}{_pct(limit)}", "PASS" if ok else "FAIL"
        print(f"{m:<24}{_pct(s['value']):<8}{ci:<16}{s['n']:<6}{thr:<11}{result}")


def _print_regression(regression, baseline, split) -> None:
    if baseline is None:
        print("regression vs baseline: no baseline stored")
    elif regression is None:
        print(f"regression vs baseline: skipped (the baseline holds the {baseline['split']} split, not {split})")
    else:
        parts = ", ".join(f"{m} lost {t['lost']} gained {t['gained']} p={t['p']:.2f}" for m, t in regression[1].items())
        verdict = "FAIL" if regression[0] else "no significant drop"
        print(f"regression vs baseline {baseline['measured_at']}: {verdict} ({parts})")


def _print_group(name: str, size: str, summary: dict, metrics) -> None:
    print(f"  {name:<20} {size}")
    cells = [f"{m} {_cell(summary[m])}" for m in metrics]
    for i in range(0, len(cells), 3):
        print("    " + "   ".join(cells[i : i + 3]))


def _write_baseline(
    path: Path, mode: str, split: str, summary: dict, rule: str, threshold: float | dict = DEFAULT_THRESHOLD, **items
) -> None:
    payload = {
        "model": MODEL_ID,
        "dataset_version": cd.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": threshold,
        "threshold_rule": rule,
        "metrics": summary,
        **items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def load_baseline(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _run(run, cases, clients, split, mode, workers, log_experiment, suite, outputs):
    """Results and experiment name, or exit code 2 when a recording is missing; recordings are saved either way."""
    if log_experiment is None:
        log_experiment = mode != "replay" and split == "test" and bool(os.environ.get("LANGSMITH_API_KEY"))
    try:
        if log_experiment:
            return run_experiment(run, cases, split, mode, workers, suite, outputs)
        with tracing_context(enabled=False), ContextThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(run, cases)), None
    except MissingRecording as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    finally:
        for client in clients:
            client.save_recordings()


def _finish(results, summary, mode, split, baseline_path, set_baseline, clients, experiment, **suite) -> int:
    baseline = load_baseline(baseline_path)
    meets = suite["meets"](summary)
    if set_baseline and meets:
        suite["save"](baseline_path, mode, split, summary, results)
        baseline = load_baseline(baseline_path)
    threshold = baseline["threshold"] if baseline else None
    if not baseline:
        regression = (["no baseline stored"], {})
    elif baseline["split"] == split:
        regression = suite["regression"](results, baseline)
    else:
        regression = None
    suite["report"](mode, split, summary, results, threshold, regression, baseline, experiment)
    usage = [u for client in clients for u in client.usage]
    if usage:
        total = {k: sum(u[k] for u in usage) for k in usage[0]}
        print(
            f"tokens: calls={len(usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )
    if set_baseline and not meets:
        print(
            f"STOP: Haiku did not reach the gate on {suite['stop']}; the stored baseline was not changed and the owner decides the threshold."
        )
        return 3
    failures = suite["gates"](summary, threshold) + (regression[0] if regression else [])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def langsmith_dataset_name(suite: str, split: str) -> str:
    return f"{suite.replace('_', '-')}-v{cd.DATASET_VERSION}-{split}"


def run_experiment(run, cases, split, mode, workers, suite, outputs):
    """Run the cases through LangSmith `evaluate` so the run is logged as an experiment."""
    from langsmith import Client

    by_id = {c["id"]: c for c in cases}
    results, errors = {}, {}

    def target(inputs: dict) -> dict:
        try:
            result = run(by_id[inputs["case_id"]])
        except Exception as error:
            errors[inputs["case_id"]] = repr(error)
            raise
        results[inputs["case_id"]] = result
        return outputs(result)

    experiment = Client().evaluate(
        target,
        data=langsmith_dataset_name(suite, split),
        evaluators=[],
        experiment_prefix=f"{suite.replace('_', '-')}-{MODEL_ID}",
        metadata={"model": MODEL_ID, "mode": mode, "split": split, "dataset_version": cd.DATASET_VERSION},
        max_concurrency=workers,
        blocking=True,
    )
    missing = set(by_id) - set(results)
    raised = sorted(missing & set(errors))
    problems = []
    if raised:
        problems.append(f"{len(raised)} cases raised in the target, first {raised[0]}: {errors[raised[0]]}")
    if missing - set(errors):
        problems.append(
            f"LangSmith dataset {langsmith_dataset_name(suite, split)} lacks {len(missing - set(errors))} local cases; "
            f"run eval-upload --suite {suite}"
        )
    if problems:
        raise RuntimeError("; ".join(problems))
    return [results[c["id"]] for c in cases], experiment.experiment_name


def _detection_example(case: dict, root: Path) -> dict:
    from purchase_cycle.email_order import model_text, parse_email

    if case["channel"] == cd.WEB_FORM:
        source = {"submission": case["submission"]}
    else:
        source = {"email_text": model_text(parse_email((root / case["file"]).read_bytes()))}
    return {
        "inputs": {"case_id": case["id"], "channel": case["channel"], **source},
        "outputs": {"doubts": {line["line_id"]: line["expected_doubts"] for line in case["lines"]}},
        "metadata": {"has_doubt": case["has_doubt"], "dataset_version": case["dataset_version"]},
    }


def _answer_example(case: dict, root: Path) -> dict:
    return {
        "inputs": {
            "case_id": case["id"],
            "doubts": case_doubts(case),
            "question": case["question"],
            "answer": case["answer"],
        },
        "outputs": {"resolutions": {d["line_id"]: d["expected"] for d in case["doubts"]}},
        "metadata": {
            "category": case["category"],
            "channel": case["channel"],
            "dataset_version": case["dataset_version"],
        },
    }


def upload_datasets(suite: str, dataset_path: Path, example) -> int:
    from langsmith import Client

    client = Client()
    cases = read_jsonl(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(suite, split)
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
            name, description=f"{suite} golden dataset v{cd.DATASET_VERSION}, {split} split (synthetic)"
        )
        client.create_examples(dataset_id=dataset.id, examples=[example(c, dataset_path.parent) for c in rows])
        print(f"{name}: uploaded {len(rows)} examples")
    return 0


# The harness looks a suite up by name and calls `evaluate` and `upload_datasets` on it.
detection = SimpleNamespace(
    evaluate=evaluate_detection,
    upload_datasets=lambda dataset_path=DETECTION_DATASET: upload_datasets(DETECTION, dataset_path, _detection_example),
)
answers = SimpleNamespace(
    evaluate=evaluate_answers,
    upload_datasets=lambda dataset_path=ANSWERS_DATASET: upload_datasets(ANSWERS, dataset_path, _answer_example),
)
