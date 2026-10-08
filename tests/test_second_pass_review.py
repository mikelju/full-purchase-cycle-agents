import json
from collections import Counter

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import clarification_dataset as cd

SKUS = {p.sku for p in PRODUCTS}
CATEGORY = {
    "pick_by_size_or_variant": "pick_variant",
    "pick_by_description": "pick_description",
    "unclear_or_off_topic": "still_unclear",
}


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _judgments(directory) -> list[dict]:
    return [r for path in sorted((directory / "second_pass_judgments").glob("*.jsonl")) for r in _rows(path)]


def _detection_disagreements() -> tuple[int, set]:
    lines = {
        line["line_id"]: (order["channel"], line)
        for order in _rows(cd.DETECTION_DIR / "plan.jsonl")
        for line in order["lines"]
    }
    judged = _judgments(cd.DETECTION_DIR)
    assert Counter(r["line_id"] for r in judged) == Counter(lines.keys())
    found = set()
    for r in judged:
        channel, line = lines[r["line_id"]]
        kind = line["kind"]
        product = {"ambiguous": "ambiguous", "unknown": "unknown"}.get(kind, "clear")
        assert {r["sku"], *r["candidates"]} - {None} <= SKUS
        if r["product"] != product:
            found.add((r["line_id"], "product"))
        elif product == "clear" and r["sku"] != line["expected_sku"]:
            found.add((r["line_id"], "sku"))
        elif product == "ambiguous" and set(r["candidates"]) != set(line["candidates"]):
            found.add((r["line_id"], "candidates"))
        if channel == "email":  # a web form quantity is a form field, not text: not reviewable blind
            doubt = {"over_ceiling": "over_ceiling", "unsupported": "missing"}.get(kind, "none")
            if r["quantity_doubt"] != doubt:
                found.add((r["line_id"], "quantity_doubt"))
            elif kind != "unsupported" and r["quantity"] != line["quantity"]:
                found.add((r["line_id"], "quantity"))
    return len(lines), found


def _answers_disagreements() -> tuple[int, set]:
    plan = _rows(cd.ANSWERS_DIR / "plan.jsonl")
    doubts = {(case["id"], d["line_id"]): (case, d) for case in plan for d in case["doubts"]}
    judged = _judgments(cd.ANSWERS_DIR)
    assert Counter((r["case_id"], r["line"]) for r in judged) == Counter(doubts.keys())
    found = set()
    for r in judged:
        key = (r["case_id"], r["line"])
        case, doubt = doubts[key]
        expected, resolution = doubt["expected"], r["resolution"]
        assert r["sku"] is None or r["sku"] in SKUS
        if expected["action"] in ("remove", "unclear"):
            if resolution != {"remove": "remove", "unclear": "unresolved"}[expected["action"]]:
                found.add((*key, "resolution"))
        elif resolution not in ("pick", "quantity", "pick_and_quantity"):
            found.add((*key, "resolution"))
        else:
            if (r["sku"] or doubt["expected_sku"]) != expected["sku"]:
                found.add((*key, "sku"))
            if (doubt["quantity"] if r["quantity"] is None else r["quantity"]) != expected["quantity"]:
                found.add((*key, "quantity"))
        if CATEGORY.get(r["category"], r["category"]) != case["category"]:
            found.add((*key, "category"))
    return len(doubts), found


def test_every_second_pass_disagreement_has_a_decision():
    for directory, compare, key in (
        (cd.DETECTION_DIR, _detection_disagreements, lambda r: (r["item"], r["field"])),
        (cd.ANSWERS_DIR, _answers_disagreements, lambda r: (r["item"], r["line"], r["field"])),
    ):
        reviewed, found = compare()
        counts, *records = _rows(directory / "second_pass_review.jsonl")
        assert counts["record"] == "counts" and counts["reviewed"] == reviewed
        assert {r["record"] for r in records} == {"disagreement"}
        assert Counter(key(r) for r in records) == Counter(found)
        disagreeing_items = {k[:-1] for k in found}
        assert counts["agree"] == reviewed - len(disagreeing_items) and counts["disagree"] == len(records)
        for r in records:
            assert r["decision"] in ("fixed", "justified")
            assert r["decision"] == "fixed" or r["reason"].strip()
        decisions = Counter(r["decision"] for r in records)
        assert (counts["fixed"], counts["justified"]) == (decisions["fixed"], decisions["justified"])
