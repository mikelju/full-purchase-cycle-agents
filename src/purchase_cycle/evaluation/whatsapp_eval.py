"""Evaluation runs of the WhatsApp order graph on the `whatsapp_order_extraction` dataset (phase 05, C13, C15).

Every message goes through the whole WhatsApp graph, without clarification, on a temporary database and
outbox, so the evaluated path is the one a real message takes: reading, intake, extraction and storing.
The graders, metrics and McNemar tests are the phase 03 email ones (one-to-one line matching, C20).
"""

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from langsmith import tracing_context
from langsmith.utils import ContextThreadPoolExecutor

from purchase_cycle import db
from purchase_cycle.config import (
    EVALS_DIR,
    MODEL_ID,
    WHATSAPP_EXTRACTION_RECORDINGS_PATH,
    WHATSAPP_INTAKE_RECORDINGS_PATH,
)
from purchase_cycle.evaluation import email_eval
from purchase_cycle.evaluation import whatsapp_dataset as wd
from purchase_cycle.evaluation.email_eval import (
    _cell,
    _ci,
    _failure_text,
    _groups,
    _pct,
    grade,
    load_baseline,
    summarise,
)
from purchase_cycle.evaluation.harness import DEFAULT_THRESHOLD
from purchase_cycle.evaluation.stats import target_cells, zero_event_note
from purchase_cycle.llm import (
    WHATSAPP_EXTRACTION,
    WHATSAPP_INTAKE,
    InvalidExtraction,
    InvalidModelOutput,
    MissingRecording,
    ModelClient,
)
from purchase_cycle.recovery import NeedsReview
from purchase_cycle.whatsapp_order import build_whatsapp_order_graph

SUITE = "whatsapp_order_extraction"
BASELINE_PATH = EVALS_DIR / "baselines" / "whatsapp_order_extraction.json"
GATED = ("intake_accuracy", "line_recall")  # the spec absolute gates; line precision is reported
METRICS = email_eval.METRICS
UNITS = {
    **email_eval.UNITS,
    "intake_accuracy": "message",
    "out_of_catalog_detection": "order_message",
    "email_exact_match": "order_message",
}


def langsmith_dataset_name(split: str) -> str:
    return f"whatsapp-order-extraction-v{wd.DATASET_VERSION}-{split}"


def run_message(graph, case: dict, dataset_dir: Path) -> dict:
    """Run one message through the graph; the last streamed state keeps the intake decision if extraction fails."""
    case = {**case, "source": wd.SOURCE}
    state: dict = {}
    try:
        for snapshot in graph.stream({"message_path": str(dataset_dir / case["file"])}, stream_mode="values"):
            state = snapshot
    except (InvalidModelOutput, InvalidExtraction, NeedsReview) as error:
        return grade(case, state.get("is_order"), [], str(error))
    if state.get("errors"):
        return grade(case, None, [], "; ".join(state["errors"]))
    return grade(case, state["is_order"], state.get("lines", []))


def _new_graph(mode: str, intake_path: Path, extraction_path: Path, tmp: Path):
    db_path = tmp / "eval.db"
    conn = db.connect(db_path)
    db.seed(conn)
    catalog = db.catalog_rows(conn)
    conn.close()
    intake = ModelClient(mode, catalog, intake_path, task=WHATSAPP_INTAKE)
    extraction = ModelClient(mode, catalog, extraction_path, task=WHATSAPP_EXTRACTION)
    return (intake, extraction), build_whatsapp_order_graph(intake, extraction, db_path, tmp / "outbox")


def regression_gate(results: list[dict], baseline: dict) -> tuple[list[str], dict]:
    """The phase 03 exact McNemar per message for intake accuracy and per expected line for line recall."""
    return email_eval.regression_gate(results, {"emails": baseline["messages"], "lines": baseline["lines"]})


def absolute_gates(summary: dict, threshold: float | None) -> list[str]:
    if threshold is None:
        return ["no threshold set in the baseline"]
    return [
        f"{m} {summary[m]['value']:.1%} below threshold {threshold:.1%}"
        for m in GATED
        if summary[m]["value"] < threshold
    ]


def print_report(mode, split, summary, results, threshold, regression, baseline) -> None:
    n_lines = summary["line_recall"]["n"]
    print(f"{SUITE}  mode={mode}  split={split}  messages={len(results)}  expected_lines={n_lines}  model={MODEL_ID}")
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
    by_category = _groups(results, "category", wd.CATEGORIES)
    print("per category:")
    for group, rows in by_category.items():
        s = summarise(rows)
        print(f"  {group:<20} messages={len(rows)}")
        print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in METRICS[:4]))
        print("    " + "   ".join(f"{m} {_cell(s[m])}" for m in METRICS[4:]))
    failures = [r for r in results if not r["email_exact_match"]]
    print(f"failures: {len(failures)}")
    for category in by_category:
        for r in (f for f in failures if f["category"] == category):
            print(f"  {category:<20} {r['id']}  {_failure_text(r)}")


def save_baseline(path: Path, mode: str, split: str, summary: dict, results: list[dict]) -> None:
    """Store the baseline with per-message and per-line results; only called once the gated metrics reach 95%."""
    payload = {
        "model": MODEL_ID,
        "dataset_version": wd.DATASET_VERSION,
        "measured_at": date.today().isoformat(),
        "mode": mode,
        "split": split,
        "threshold": DEFAULT_THRESHOLD,
        "threshold_rule": "95% if intake accuracy and line recall reach 95% on the test split; "
        "otherwise the owner decides (deviation)",
        "metrics": summary,
        "messages": {
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
    dataset_path: Path = wd.DATASET_PATH,
    intake_recordings_path: Path = WHATSAPP_INTAKE_RECORDINGS_PATH,
    extraction_recordings_path: Path = WHATSAPP_EXTRACTION_RECORDINGS_PATH,
    baseline_path: Path = BASELINE_PATH,
    set_baseline: bool = False,
    workers: int = 8,
) -> int:
    """Run the WhatsApp evaluation and print the report; returns the process exit code."""
    cases = [c for c in wd.load_dataset(dataset_path) if split == "all" or c["split"] == split]
    with tempfile.TemporaryDirectory() as tmp:
        clients, graph = _new_graph(mode, intake_recordings_path, extraction_recordings_path, Path(tmp))
        try:
            with tracing_context(enabled=False), ContextThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda c: run_message(graph, c, dataset_path.parent), cases))
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
    print_report(mode, split, summary, results, threshold, regression, baseline)
    usage = [u for client in clients for u in client.usage]
    if usage:
        total = {k: sum(u[k] for u in usage) for k in usage[0]}
        print(
            f"tokens: calls={len(usage)}  uncached_input={total['input_tokens']}  cache_read={total['cache_read']}  cache_write={total['cache_creation']}  output={total['output_tokens']}"
        )
    if set_baseline and not meets:
        print(
            "STOP: Haiku did not reach 95% on intake accuracy and line recall; the stored baseline was not changed and the owner decides the threshold (deviation)."
        )
        return 3
    failures = absolute_gates(summary, threshold) + (regression[0] if regression else [])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    return 1 if failures else 0


def upload_datasets(dataset_path: Path = wd.DATASET_PATH) -> int:
    from langsmith import Client

    from purchase_cycle.whatsapp_order import model_text

    client = Client()
    cases = wd.load_dataset(dataset_path)
    for split in ("dev", "test"):
        name = langsmith_dataset_name(split)
        rows = [c for c in cases if c["split"] == split]
        if client.has_dataset(dataset_name=name):
            remote = [e.inputs.get("message_id") for e in client.list_examples(dataset_name=name)]
            if sorted(remote) != sorted(c["id"] for c in rows):
                print(
                    f"Error: {name} already exists in LangSmith with {len(remote)} examples whose message ids differ "
                    f"from the {len(rows)} local messages; bump the dataset version or delete the remote dataset",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: already uploaded with {len(remote)} examples")
            continue
        dataset = client.create_dataset(
            name,
            description=f"WhatsApp order extraction golden dataset v{wd.DATASET_VERSION}, {split} split (synthetic)",
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {
                        "message_id": c["id"],
                        "message_text": model_text({"body": c["body"]}),
                    },
                    "outputs": {
                        "is_order": c["is_order"],
                        "lines": [
                            {"sku": line["expected_sku"], "quantity": line["expected_quantity"]} for line in c["lines"]
                        ],
                    },
                    "metadata": {
                        "category": c["category"],
                        "line_ids": [line["line_id"] for line in c["lines"]],
                        "dataset_version": c["dataset_version"],
                    },
                }
                for c in rows
            ],
        )
        print(f"{name}: uploaded {len(rows)} examples")
    return 0
