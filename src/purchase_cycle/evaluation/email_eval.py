"""Evaluation runs of the email order subgraph on the `email_order_extraction` dataset: graders, report and gates.

Every email of the dataset goes through the whole subgraph on a temporary database,
so the evaluated path is the one a real email takes: parsing, intake, extraction and storing.
"""

import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import db
from purchase_cycle.config import EMAIL_EXTRACTION_RECORDINGS_PATH, EMAIL_INTAKE_RECORDINGS_PATH, EVALS_DIR, MODEL_ID
from purchase_cycle.email_order import build_email_order_graph
from purchase_cycle.evaluation import email_dataset as ed
from purchase_cycle.evaluation.harness import ALPHA, DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import mcnemar_exact, target_cells, wilson_interval, zero_event_note
from purchase_cycle.llm import (
    EMAIL_EXTRACTION,
    EMAIL_INTAKE,
    InvalidExtraction,
    InvalidModelOutput,
    MissingRecording,
    ModelClient,
)

SUITE = "email_order_extraction"
BASELINE_PATH = EVALS_DIR / "baselines" / "email_order_extraction.json"
GATED = ("intake_accuracy", "line_recall", "line_precision")
METRICS = (*GATED, "field_sku", "field_quantity", "out_of_catalog_detection", "email_exact_match")
# Counting unit of each metric (see `summarise`); the target of the gated ones is DEFAULT_THRESHOLD.
UNITS = {
    "intake_accuracy": "email",
    "line_recall": "expected_line",
    "line_precision": "produced_line",
    "field_sku": "expected_line",
    "field_quantity": "sku_found_line",
    "out_of_catalog_detection": "order_email",
    "email_exact_match": "order_email",
}
SOURCES = ("body", "txt", "pdf", "xlsx")


def langsmith_dataset_name(split: str) -> str:
    return f"email-order-extraction-v{ed.DATASET_VERSION}-{split}"


def _take(pool: Counter, key) -> bool:
    """Consume one produced line with this key, so a produced line counts for at most one expected line."""
    if pool[key] > 0:
        pool[key] -= 1
        return True
    return False


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word containment of normalised `phrase` in normalised `text`, so "gel" is not in "angel wings"."""
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _cited_text(source_text: str, texts: set[str]) -> str | None:
    """The one expected text a citation names: those it contains as whole words, less sub-phrases of another.

    A citation naming more than one distinct expected text (a copied table) names none.
    """
    cited = _normalise(source_text)
    found = {text for text in texts if _has_phrase(cited, text)}
    kept = {text for text in found if not any(other != text and _has_phrase(other, text) for other in found)}
    return kept.pop() if len(kept) == 1 else None


def _max_matching(edges: list[list[int]], n_right: int) -> int:
    """Size of a maximum one-to-one matching; `edges[i]` lists the right nodes left node `i` may take."""
    owner = [-1] * n_right

    def augment(i: int, seen: set[int]) -> bool:
        for j in edges[i]:
            if j not in seen:
                seen.add(j)
                if owner[j] == -1 or augment(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    return sum(augment(i, set()) for i in range(len(edges)))


def _unknown_hits(expected: list[dict], produced: list[dict]) -> int:
    """Pair out-of-catalog lines one to one on source, quantity and the requested text named by the citation."""
    texts = {_normalise(line["text"]) for line in expected}
    cited = [_cited_text(line["source_text"], texts) for line in produced]
    edges = [
        [
            j
            for j, got in enumerate(produced)
            if got["source"] == line["location"]
            and got["quantity"] == line["expected_quantity"]
            and cited[j] == _normalise(line["text"])
        ]
        for line in expected
    ]
    return _max_matching(edges, len(produced))


def grade(case: dict, is_order: bool | None, lines: list[dict], error: str | None = None) -> dict:
    """Grade one email; `lines` are the extracted lines, empty when the run stopped or the intake said no order.

    Expected and produced lines are matched one to one: a catalog line hits only with the expected
    SKU, quantity and source, and an unknown line only with the expected quantity, source and requested
    text named by its citation (deviation 05.2).
    """
    catalog = [line for line in lines if line["sku"] is not None]
    full = Counter((line["sku"], line["quantity"], line["source"]) for line in catalog)
    by_sku = Counter(line["sku"] for line in catalog)
    by_sku_quantity = Counter((line["sku"], line["quantity"]) for line in catalog)
    expected = [line for line in case["lines"] if line["expected_sku"] is not None]
    recall = [_take(full, (line["expected_sku"], line["expected_quantity"], line["location"])) for line in expected]
    # Exact (SKU, quantity) matches first, recalled lines before the others; SKU-only credit on the leftovers.
    quantity = [False] * len(expected)
    for i in sorted(range(len(expected)), key=lambda i: not recall[i]):
        quantity[i] = _take(by_sku_quantity, (expected[i]["expected_sku"], expected[i]["expected_quantity"]))
    skus_left = by_sku - Counter(line["expected_sku"] for line, q in zip(expected, quantity, strict=True) if q)
    line_rows = [
        {
            "id": line["line_id"],
            "line_recall": r,
            "field_sku": q or _take(skus_left, line["expected_sku"]),
            "field_quantity": q,
        }
        for line, r, q in zip(expected, recall, quantity, strict=True)
    ]
    precision_hits = sum(row["line_recall"] for row in line_rows)
    expected_unknown = [line for line in case["lines"] if line["expected_sku"] is None]
    unknown = [line for line in lines if line["sku"] is None]
    unknown_hits = _unknown_hits(expected_unknown, unknown)
    detection = unknown_hits == len(expected_unknown) == len(unknown)
    exact = (
        error is None
        and is_order == case["is_order"]
        and precision_hits == len(line_rows) == len(catalog)
        and detection
    )
    return {
        "id": case["id"],
        "category": case["category"],
        "source": case["source"],
        "expected_is_order": case["is_order"],
        "is_order": is_order,
        "error": error,
        "lines": lines,
        # A run that stopped after the intake keeps its decision; a rejected email has none.
        "intake_accuracy": is_order is not None and is_order == case["is_order"],
        "line_rows": line_rows,
        "produced_catalog": len(catalog),
        "precision_hits": precision_hits,
        "expected_unmatched": len(expected_unknown),
        "unmatched": len(unknown),
        "unmatched_hits": unknown_hits,
        "out_of_catalog_detection": detection,
        "email_exact_match": exact,
    }


def run_email(graph, case: dict, emails_root: Path) -> dict:
    """Run one email through the subgraph; the last streamed state keeps the intake decision if extraction fails."""
    state: dict = {}
    try:
        for snapshot in graph.stream({"email_path": str(emails_root / case["file"])}, stream_mode="values"):
            state = snapshot
    except (InvalidModelOutput, InvalidExtraction) as error:
        return grade(case, state.get("is_order"), [], str(error))
    if state.get("errors"):
        return grade(case, None, [], "; ".join(state["errors"]))
    return grade(case, state["is_order"], state.get("lines", []))


def _new_graph(mode: str, intake_path: Path, extraction_path: Path, db_path: Path):
    conn = db.connect(db_path)
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    intake = ModelClient(mode, catalog, intake_path, task=EMAIL_INTAKE)
    extraction = ModelClient(mode, catalog, extraction_path, task=EMAIL_EXTRACTION)
    return (intake, extraction), build_email_order_graph(intake, extraction, db_path)


def run_emails(graph, cases: list[dict], emails_root: Path, workers: int) -> list[dict]:
    with ContextThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda c: run_email(graph, c, emails_root), cases))


def run_experiment(graph, cases: list[dict], emails_root: Path, split: str, mode: str, workers: int):
    """Run the emails through LangSmith `evaluate` so the run is logged as an experiment."""
    from langsmith import Client

    by_id = {c["id"]: c for c in cases}
    results, errors = {}, {}

    def target(inputs: dict) -> dict:
        try:
            result = run_email(graph, by_id[inputs["email_id"]], emails_root)
        except Exception as error:
            errors[inputs["email_id"]] = repr(error)
            raise
        results[inputs["email_id"]] = result
        return {
            "is_order": result["is_order"],
            "lines": [{"sku": line["sku"], "quantity": line["quantity"]} for line in result["lines"]],
            "error": result["error"],
            "email_exact_match": result["email_exact_match"],
        }

    def intake_accuracy(outputs: dict, reference_outputs: dict) -> dict:
        return {"key": "intake_accuracy", "score": int(outputs["is_order"] == reference_outputs["is_order"])}

    def email_exact_match(outputs: dict) -> dict:
        return {"key": "email_exact_match", "score": int(outputs["email_exact_match"])}

    experiment = Client().evaluate(
        target,
        data=langsmith_dataset_name(split),
        evaluators=[intake_accuracy, email_exact_match],
        experiment_prefix=f"email-order-extraction-{MODEL_ID}",
        metadata={"model": MODEL_ID, "mode": mode, "split": split, "dataset_version": ed.DATASET_VERSION},
        max_concurrency=workers,
        blocking=True,
    )
    missing = set(by_id) - set(results)
    raised = sorted(missing & set(errors))
    absent = missing - set(errors)
    problems = []
    if raised:
        problems.append(f"{len(raised)} emails raised in the target, first {raised[0]}: {errors[raised[0]]}")
    if absent:
        problems.append(
            f"LangSmith dataset {langsmith_dataset_name(split)} lacks {len(absent)} local emails; "
            "run eval-upload --suite email_order_extraction"
        )
    if problems:
        raise RuntimeError("; ".join(problems))
    return [results[c["id"]] for c in cases], experiment.experiment_name


def rate(hits: int, n: int) -> dict:
    low, high = wilson_interval(hits, n)
    return {"value": hits / n if n else 0.0, "low": low, "high": high, "hits": hits, "n": n}


def summarise(results: list[dict]) -> dict:
    """Each metric over its own unit: emails, expected catalog lines, produced catalog lines or order emails.

    line_recall: expected catalog lines matched by a distinct produced line with the same SKU, quantity and source.
    line_precision: produced catalog lines matched that way by a distinct expected line.
    field_sku: expected catalog lines whose SKU a distinct produced line carries; field_quantity: of those,
    the ones whose quantity also matches.
    out_of_catalog_detection: order emails whose unknown lines pair one to one with the expected ones on
    quantity, source and the requested text named by the citation.
    email_exact_match: order emails with the right intake, every catalog line matched, none extra and
    out-of-catalog detection.
    """
    lines = [row for r in results for row in r["line_rows"]]
    found = [row for row in lines if row["field_sku"]]
    orders = [r for r in results if r["expected_is_order"]]
    return {
        "intake_accuracy": rate(sum(r["intake_accuracy"] for r in results), len(results)),
        "line_recall": rate(sum(row["line_recall"] for row in lines), len(lines)),
        "line_precision": rate(sum(r["precision_hits"] for r in results), sum(r["produced_catalog"] for r in results)),
        "field_sku": rate(len(found), len(lines)),
        "field_quantity": rate(sum(row["field_quantity"] for row in found), len(found)),
        "out_of_catalog_detection": rate(sum(r["out_of_catalog_detection"] for r in orders), len(orders)),
        "email_exact_match": rate(sum(r["email_exact_match"] for r in orders), len(orders)),
    }


def absolute_gates(summary: dict, threshold: float | None) -> list[str]:
    if threshold is None:
        return ["no threshold set in the baseline"]
    return [
        f"{m} {summary[m]['value']:.1%} below threshold {threshold:.1%}"
        for m in GATED
        if summary[m]["value"] < threshold
    ]


def _mcnemar(pairs: list[tuple[bool, bool]]) -> dict:
    lost = sum(1 for before, now in pairs if before and not now)
    gained = sum(1 for before, now in pairs if not before and now)
    return {"lost": lost, "gained": gained, "p": mcnemar_exact(lost, gained)}


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """Exact McNemar per email for intake accuracy and per expected line for line recall."""
    emails, lines = baseline["emails"], baseline["lines"]
    rows = [row for r in results for row in r["line_rows"]]
    tests = {
        "intake_accuracy": _mcnemar(
            [(emails[r["id"]]["intake_accuracy"], r["intake_accuracy"]) for r in results if r["id"] in emails]
        ),
        "line_recall": _mcnemar(
            [(lines[row["id"]]["line_recall"], row["line_recall"]) for row in rows if row["id"] in lines]
        ),
    }
    failures = [
        f"{m} dropped against the baseline (lost {t['lost']}, gained {t['gained']}, McNemar p={t['p']:.4f})"
        for m, t in tests.items()
        if t["lost"] > t["gained"] and t["p"] < ALPHA
    ]
    if set(emails) != {r["id"] for r in results} or set(lines) != {row["id"] for row in rows}:
        failures.append("the evaluated emails or lines differ from the baseline")
    return failures, tests


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _ci(s: dict) -> str:
    return f"[{s['low'] * 100:.1f}, {s['high'] * 100:.1f}]"


def _cell(s: dict) -> str:
    return f"{_pct(s['value'])} {_ci(s)} n={s['n']}" if s["n"] else "n/a"


def _groups(results: list[dict], key: str, order) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for r in results:
        groups[r[key]].append(r)
    return {g: groups[g] for g in order if g in groups}


def _failure_text(r: dict) -> str:
    if r["error"]:
        return f"error: {r['error']}"
    parts = []
    if not r["intake_accuracy"]:
        parts.append(f"intake said is_order={r['is_order']}")
    missed = [row["id"] for row in r["line_rows"] if not row["line_recall"]]
    if missed:
        parts.append(f"missed {', '.join(missed)}")
    extra = r["produced_catalog"] - r["precision_hits"]
    if extra:
        parts.append(f"{extra} wrong catalog lines")
    if not r["out_of_catalog_detection"]:
        parts.append(
            f"unknown lines paired {r['unmatched_hits']} of {r['expected_unmatched']} on quantity, source and"
            f" requested text ({r['unmatched']} produced)"
        )
    return "; ".join(parts)


def print_report(mode, split, summary, results, threshold, regression, baseline, experiment) -> None:
    n_lines = summary["line_recall"]["n"]
    print(f"{SUITE}  mode={mode}  split={split}  emails={len(results)}  expected_lines={n_lines}  model={MODEL_ID}")
    print(
        f"{'metric':<26}{'value':<8}{'95% CI':<16}{'n':<6}{'unit':<16}{'target':<9}{'target met':<12}{'threshold':<11}gate"
    )
    for m in METRICS:
        s = summary[m]
        if m in GATED:
            target, met = target_cells(s["value"], DEFAULT_THRESHOLD)
            thr = _pct(threshold) if threshold is not None else "none"
            result = "PASS" if threshold is not None and s["value"] >= threshold else "FAIL"
        else:
            target, met, thr, result = "none", "n/a", "none", "reported"
        print(
            f"{m:<26}{_pct(s['value']):<8}{_ci(s):<16}{s['n']:<6}{UNITS[m]:<16}{target:<9}{met:<12}{thr:<11}{result}"
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
    by_category = _groups(results, "category", ed.CATEGORIES)
    for title, groups in (("per category", by_category), ("per source", _groups(results, "source", SOURCES))):
        print(f"{title}:")
        for group, rows in groups.items():
            s = summarise(rows)
            print(f"  {group:<20} emails={len(rows)}")
            print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in METRICS[:4]))
            print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in METRICS[4:]))
    # Exact match also holds for a not-order email the intake rejected, so it marks every failure.
    failures = [r for r in results if not r["email_exact_match"]]
    print(f"failures: {len(failures)}")
    for category in by_category:
        for r in (f for f in failures if f["category"] == category):
            print(f"  {category:<20} {r['id']}  {_failure_text(r)}")
    if experiment:
        print(f"langsmith experiment: {experiment}")


def load_baseline(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline with per-email and per-line results; only called once the gated metrics reach 95%."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": ed.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if intake accuracy, line recall and line precision reach 95% on the test split; "
        "otherwise the owner decides (deviation)",
        "metrics": summary,
        "emails": {
            r["id"]: {
                "is_order": r["is_order"],
                "error": r["error"],
                "lines": [
                    {"sku": line["sku"], "quantity": line["quantity"], "source": line["source"]} for line in r["lines"]
                ],
                **{m: r[m] for m in ("intake_accuracy", "out_of_catalog_detection", "email_exact_match")},
            }
            for r in results
        },
        "lines": {
            row["id"]: {k: row[k] for k in ("line_recall", "field_sku", "field_quantity")}
            for r in results
            for row in r["line_rows"]
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(
    mode: str,
    split: str,
    *,
    dataset_path: Path = ed.DATASET_PATH,
    intake_recordings_path: Path = EMAIL_INTAKE_RECORDINGS_PATH,
    extraction_recordings_path: Path = EMAIL_EXTRACTION_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
    log_experiment: bool | None = None,
) -> int:
    """Run the email evaluation and print the report; returns the process exit code."""
    cases = [c for c in ed.load_dataset(dataset_path) if split == "all" or c["split"] == split]
    emails_root = dataset_path.parent
    if log_experiment is None:
        log_experiment = mode != "replay" and split == "test" and bool(os.environ.get("LANGSMITH_API_KEY"))

    with tempfile.TemporaryDirectory() as tmp:
        clients, graph = _new_graph(mode, intake_recordings_path, extraction_recordings_path, Path(tmp) / "eval.db")
        experiment = None
        try:
            if log_experiment:
                results, experiment = run_experiment(graph, cases, emails_root, split, mode, workers)
            else:
                with tracing_context(enabled=False):
                    results = run_emails(graph, cases, emails_root, workers)
        except MissingRecording as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
        finally:
            for client in clients:
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
    print_report(mode, split, summary, results, threshold, regression, baseline, experiment)
    usage = [u for client in clients for u in client.usage]
    if usage:
        total = {k: sum(u[k] for u in usage) for k in usage[0]}
        print(
            f"tokens: calls={len(usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )

    failures = absolute_gates(summary, threshold) + (regression[0] if regression else [])
    if set_baseline and not meets:
        print(
            "STOP: Haiku did not reach 95% on intake accuracy, line recall and line precision; the stored baseline was not changed and the owner decides the threshold (deviation)."
        )
        return 3
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = ed.DATASET_PATH) -> int:
    from langsmith import Client

    from purchase_cycle.email_order import model_text, parse_email

    client = Client()
    cases = ed.load_dataset(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        rows = [c for c in cases if c["split"] == split]
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("email_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(c["id"] for c in rows):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose email ids differ "
                    f"from the {len(rows)} local emails; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name, description=f"Email order extraction golden dataset v{ed.DATASET_VERSION}, {split} split (synthetic)"
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {
                        "email_id": c["id"],
                        "email_text": model_text(parse_email((dataset_path.parent / c["file"]).read_bytes())),
                    },
                    "outputs": {
                        "is_order": c["is_order"],
                        "lines": [
                            {"sku": line["expected_sku"], "quantity": line["expected_quantity"]} for line in c["lines"]
                        ],
                    },
                    "metadata": {
                        "category": c["category"],
                        "source": c["source"],
                        "line_ids": [line["line_id"] for line in c["lines"]],
                        "dataset_version": c["dataset_version"],
                    },
                }
                for c in rows
            ],
        )
        print(f"{name}: uploaded {len(rows)} examples")
    return 0
