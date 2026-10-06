"""Golden dataset files, automatic validation and build from plan plus sentences."""

import re
import unicodedata
from pathlib import Path

from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.config import EVALS_DIR
from purchase_cycle.evaluation.planning import has_number, read_jsonl, write_jsonl

DATASET_DIR = EVALS_DIR / "datasets" / "order_line_extraction"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
SENTENCES_DIR = DATASET_DIR / "sentences"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
CONTRAST_PATH = DATASET_DIR / "contrast.jsonl"

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}
MIN_NOISE_WORDS = 18
MAX_CHARS = 600


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9%.]+", " ", text).split()).strip(" .")


def validate_case(case: dict) -> list[str]:
    """Return the reasons a case is rejected; empty when it passes."""
    errors = []
    sentence = case.get("sentence") or ""
    sku = case["expected_sku"]
    quantity = case["expected_quantity"]
    category = case["category"]
    if not sentence.strip():
        return ["sentence is empty"]
    if len(sentence) > MAX_CHARS:
        errors.append(f"sentence longer than {MAX_CHARS} characters")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        errors.append("expected quantity is not a positive integer")
    if category == "out_of_catalog":
        if sku is not None:
            errors.append("out_of_catalog case must expect a null SKU")
    elif sku not in PRODUCT_BY_SKU:
        errors.append(f"expected SKU {sku} does not exist")
    if errors:
        return errors

    norm = normalise(sentence)
    name = normalise(PRODUCT_BY_SKU[sku].name) if sku else None
    digits_expected = category not in ("quantity_words", "unit_expression")
    if digits_expected and not has_number(sentence, quantity):
        errors.append(f"quantity {quantity} does not appear as digits")
    if not digits_expected and has_number(sentence, quantity):
        errors.append(f"quantity {quantity} appears as digits in a {category} case")
    if category == "exact_name" and name not in norm:
        errors.append("exact_name case does not contain the catalog name verbatim")
    if category in ("synonym", "typo") and name in norm:
        errors.append(f"{category} case contains the exact catalog name")
    if category == "noise" and len(sentence.split()) < MIN_NOISE_WORDS:
        errors.append(f"noise case shorter than {MIN_NOISE_WORDS} words")
    if category == "unit_expression" and case["trap"]["style"] == "items_to_packs":
        if not has_number(sentence, case["trap"]["items_requested"]):
            errors.append("items_to_packs case does not state the number of items")
    return errors


def load_sentences(directory: Path = SENTENCES_DIR) -> dict[str, str]:
    sentences = {}
    for path in sorted(directory.glob("*.jsonl")):
        for row in read_jsonl(path):
            sentences[row["id"]] = row["sentence"]
    return sentences


def build_dataset(plan: list[dict], sentences: dict[str, str]) -> tuple[list[dict], dict[str, list[str]]]:
    """Join plan and sentences; return the cases and the rejected ids with reasons."""
    cases, rejected, seen = [], {}, {}
    for planned in plan:
        case = dict(planned, sentence=sentences.get(planned["id"], ""))
        errors = validate_case(case)
        key = normalise(case["sentence"])
        if key and key in seen:
            errors.append(f"duplicates {seen[key]} after normalisation")
        seen.setdefault(key, case["id"])
        if errors:
            rejected[case["id"]] = errors
        cases.append(case)
    return cases, rejected


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    return read_jsonl(path)


def save_dataset(cases: list[dict], path: Path = DATASET_PATH) -> None:
    write_jsonl(path, cases)
