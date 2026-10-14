"""C12 (phase 09): the `claim_drafting` dataset, its seeded plan and its automatic validation."""

from collections import Counter

import pytest

from purchase_cycle import claims, reconcile_match
from purchase_cycle.evaluation import claim_dataset as cd
from purchase_cycle.evaluation.planning import read_jsonl


@pytest.fixture(scope="module")
def plan():
    return cd.build_plan()


@pytest.fixture(scope="module")
def dataset():
    return cd.load_dataset()


def test_ten_categories_of_at_least_six_and_sixty_cases(dataset):
    counts = Counter(r["category"] for r in dataset)
    assert set(counts) == set(cd.CATEGORIES) and len(cd.CATEGORIES) == 10
    assert len(dataset) >= 60 and min(counts.values()) >= 6
    assert len({r["id"] for r in dataset}) == len(dataset)


def test_split_is_stratified_about_a_quarter_development(dataset):
    for category in cd.CATEGORIES:
        splits = Counter(r["split"] for r in dataset if r["category"] == category)
        assert splits["dev"] == cd.DEV_PER_CATEGORY and splits["test"] >= 6 - cd.DEV_PER_CATEGORY


def test_same_seed_gives_the_same_plan_and_files_are_that_plan(plan):
    assert cd.build_plan(cd.SEED) == plan
    assert cd.build_plan(cd.SEED + 1) != plan
    assert read_jsonl(cd.PLAN_PATH) == plan


def test_dataset_file_is_the_build_of_the_plan(plan, dataset):
    rows, rejected = cd.build_dataset(plan)
    assert not rejected and rows == dataset


def test_every_case_recomputes_its_rows_and_amount_in_dispute(dataset):
    for row in dataset:
        result, differences = reconcile_match.classify(row["order"], row["invoice_lines"], row["note_lines"])
        assert result == reconcile_match.DISCREPANCIES
        assert row["facts"]["differences"] == differences
        assert row["facts"]["amount_in_dispute"] == claims.amount_in_dispute(differences)
        assert cd.validate_case(row) == []


def test_every_template_passes_the_fact_check(dataset):
    for row in dataset:
        assert claims.check_draft(claims.fill_template(row["facts"]), row["facts"]) == []


def test_validation_rejects_a_wrong_row_or_amount(dataset):
    row = dict(dataset[0], facts=dict(dataset[0]["facts"], amount_in_dispute=1.0))
    assert any("amount in dispute" in e for e in cd.validate_case(row))
    other = next(r for r in dataset if r["category"] == "price_below")
    assert any("types" in e for e in cd.validate_case(dict(other, category="extra_line")))


def test_review_disagreements_compare_rows_and_amounts(dataset):
    row = dataset[0]
    read = {"differences": row["facts"]["differences"], "amount_in_dispute": row["facts"]["amount_in_dispute"]}
    assert cd.review_disagreements(row, read) == []
    assert cd.review_disagreements(row, dict(read, amount_in_dispute=row["facts"]["amount_in_dispute"] + 1))
    assert cd.review_disagreements(row, dict(read, differences=[]))
