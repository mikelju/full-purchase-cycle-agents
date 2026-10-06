"""C9: the web form matching dataset, its plan and the automatic validation."""

from collections import Counter

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import web_form_dataset as wf
from purchase_cycle.evaluation.planning import read_jsonl


def test_plan_is_reproducible_from_the_seed():
    assert wf.build_plan(wf.SEED) == read_jsonl(wf.PLAN_PATH)


def test_script_texts_pass_validation():
    plan = wf.build_plan()
    assert all(wf.validate_line(line) == [] for line in plan if line["category"] in wf.SCRIPT_CATEGORIES)


def _line(category, sku, text):
    return {"category": category, "expected_sku": sku, "product_text": text}


def test_validation_rejects_broken_lines():
    assert wf.validate_line(_line("short_name", "NOT-A-SKU", "gloves")) == ["expected SKU NOT-A-SKU does not exist"]
    assert wf.validate_line(_line("typo", "GLV-NIT-M", "nitrile examination gloves powder free size m")) == [
        "typo text equals the catalog name or SKU of GLV-NIT-M"
    ]
    assert wf.validate_line(_line("out_of_catalog", None, "glv-nit-m")) == [
        "out_of_catalog text equals the catalog name or SKU of GLV-NIT-M"
    ]
    assert wf.validate_line(_line("exact_name", "GLV-NIT-M", "nitrile gloves M")) == [
        "exact_name text is not the catalog name"
    ]
    assert wf.validate_line(_line("short_name", "GLV-NIT-M", " ")) == ["product text is empty"]
    plan = [
        {"id": "A", **_line("short_name", "GLV-NIT-M", "")},
        {"id": "B", **_line("short_name", "GLV-NIT-L", "")},
    ]
    _, rejected = wf.build_dataset(plan, {"A": "nitrile gloves M", "B": "Nitrile gloves, M"})
    assert rejected == {"B": ["duplicates A after normalisation"]}


def test_dataset_size_categories_and_submissions():
    lines = wf.load_dataset()
    counts = Counter(line["category"] for line in lines)
    assert len(lines) >= 600
    assert set(counts) == set(wf.CATEGORIES)
    assert min(counts.values()) >= 100
    subs = wf.submissions(lines)
    assert len(subs) >= 200
    assert all(1 <= len(s["lines"]) <= 5 for s in subs)
    assert all(len({line["split"] for line in s["lines"]}) == 1 for s in subs)
    for category in wf.CATEGORIES:
        rows = [line for line in lines if line["category"] == category]
        dev = sum(line["split"] == "dev" for line in rows)
        assert 0.2 <= dev / len(rows) <= 0.3


def test_dataset_covers_every_family_and_no_product_dominates():
    lines = wf.load_dataset()
    uses = Counter(line["expected_sku"] for line in lines if line["expected_sku"])
    assert {wf.PRODUCT_BY_SKU[sku].family for sku in uses} == {p.family for p in PRODUCTS}
    assert max(uses.values()) <= 2


def test_dataset_lines_pass_validation_and_labels_come_from_the_plan():
    plan = read_jsonl(wf.PLAN_PATH)
    lines, rejected = wf.build_dataset(plan, wf.load_texts())
    assert rejected == {}
    assert lines == wf.load_dataset()
    for planned, line in zip(plan, lines, strict=True):
        assert {k: v for k, v in line.items() if k != "product_text"} == {
            k: v for k, v in planned.items() if k != "product_text"
        }


def test_second_pass_review_covers_every_written_line():
    written = {line["id"] for line in wf.load_dataset() if line["category"] in wf.WRITTEN_CATEGORIES}
    reviewed = {row["id"] for row in read_jsonl(wf.REVIEW_PATH)}
    assert written == reviewed
