"""C8 and C9: golden dataset shape, validation and labels fixed by the plan."""

from collections import Counter

import pytest

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.evaluation import dataset as ds
from purchase_cycle.evaluation.planning import CATEGORIES, DATASET_VERSION, SEED, build_plan, read_jsonl

LABEL_FIELDS = ("category", "split", "expected_sku", "expected_quantity", "trap", "dataset_version")


@pytest.fixture(scope="module")
def cases():
    return ds.load_dataset()


def test_plan_is_reproducible_from_the_seed():
    assert read_jsonl(ds.PLAN_PATH) == build_plan(SEED)
    assert build_plan(SEED) == build_plan(SEED)
    assert build_plan(SEED + 1) != build_plan(SEED)


def test_dataset_labels_come_from_the_plan(cases):
    plan = {c["id"]: c for c in read_jsonl(ds.PLAN_PATH)}
    assert [c["id"] for c in cases] == list(plan)
    for case in cases:
        assert {k: case[k] for k in LABEL_FIELDS} == {k: plan[case["id"]][k] for k in LABEL_FIELDS}


def test_dataset_size_categories_and_split(cases):
    assert len(cases) >= 2000
    per_category = Counter(c["category"] for c in cases)
    assert set(per_category) == set(CATEGORIES)
    assert min(per_category.values()) >= 200
    splits = Counter(c["split"] for c in cases)
    assert splits == {"dev": 500, "test": 1500}
    for category in CATEGORIES:
        dev = sum(1 for c in cases if c["category"] == category and c["split"] == "dev")
        assert dev in (62, 63), "split is stratified by category"
    assert {c["dataset_version"] for c in cases} == {DATASET_VERSION}


def test_every_product_is_covered_without_dominance(cases):
    coverage = Counter(c["expected_sku"] for c in cases if c["expected_sku"])
    assert set(coverage) == {p.sku for p in PRODUCTS}
    assert min(coverage.values()) >= 3
    assert max(coverage.values()) <= 2 * min(coverage.values())


def test_every_case_passes_validation(cases):
    rebuilt, rejected = ds.build_dataset(read_jsonl(ds.PLAN_PATH), {c["id"]: c["sentence"] for c in cases})
    assert rejected == {}
    assert rebuilt == cases


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"expected_sku": "NOPE-1"}, "does not exist"),
        ({"expected_quantity": 0}, "positive integer"),
        (
            {"category": "quantity_words", "sentence": "Send 40 boxes of FFP2 respirator mask, no valve"},
            "appears as digits",
        ),
        ({"category": "exact_name", "sentence": "Send 40 boxes of FFP2 masks"}, "verbatim"),
    ],
)
def test_validation_rejects_broken_cases(change, reason):
    case = {
        "id": "X",
        "category": "exact_name",
        "sentence": "Send 40 boxes of FFP2 respirator mask, no valve",
        "expected_sku": "MASK-FFP2",
        "expected_quantity": 40,
        "trap": {"style": "verbatim_catalog_name"},
    }
    case.update(change)
    assert any(reason in e for e in ds.validate_case(case))


def test_duplicate_sentences_are_rejected():
    plan = build_plan(SEED)[:2]
    _, rejected = ds.build_dataset(plan, {plan[0]["id"]: "Same text!", plan[1]["id"]: "same   TEXT"})
    assert any("duplicates" in e for e in rejected[plan[1]["id"]])
