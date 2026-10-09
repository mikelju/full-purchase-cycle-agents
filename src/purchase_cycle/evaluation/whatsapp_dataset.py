"""Golden dataset of the WhatsApp order evaluation: seeded plan, validation, rendering and build.

The seeded plan fixes, before any text exists, the category, sender, order or
not, and every line with its expected SKU (or out-of-catalog item), expected
quantity in sale units, unit expression and trap, so labels are decided by
construction. The coding agent writes the message texts; this module validates
them and renders deterministic WhatsApp message files.
"""

import json
import random
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import ValidationError

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR, ROOT
from purchase_cycle.evaluation.email_dataset import REVIEW_DECISIONS, build_review, review_disagreements
from purchase_cycle.evaluation.planning import (
    GENERIC_QUANTITIES,
    OUT_OF_CATALOG_ITEMS,
    WORD_QUANTITIES,
    read_jsonl,
    write_jsonl,
)
from purchase_cycle.quantities import has_number, pack_size, supports_quantity
from purchase_cycle.web_form import match_key
from purchase_cycle.whatsapp_order import WhatsAppMessage, model_text, phone_digits

DATASET_VERSION = "1.0"
SEED = 20261009
MESSAGES_PER_CATEGORY = 32
DEV_PER_CATEGORY = 8  # 25% development, 75% test

CATEGORIES = (
    "short_list",
    "chatty_single_line",
    "one_sentence_lines",
    "words_or_dozens",
    "not_an_order",
)
ORDER_CATEGORIES = CATEGORIES[:-1]
# Line counts cycled over the messages of a category, so the totals do not depend on the seed.
LINE_COUNTS = {
    "short_list": (2, 3, 4, 5, 6, 7),
    "chatty_single_line": (1,),
    "one_sentence_lines": (2, 3, 4),
    "words_or_dozens": (1, 2, 3, 4),
}
# Fixed share of the lines of every order category; the rest are plain lines.
TRAP_SHARES = (("out_of_catalog", 0.10), ("typo", 0.10), ("near_miss", 0.10))
WORD_STYLES = ("quantity_words", "dozen", "half_dozen", "couple")
UNIT_WEIGHTS = {
    "short_list": (("sale_units", 6), ("items_to_packs", 1)),
    "chatty_single_line": (("sale_units", 2), ("quantity_words", 1), ("items_to_packs", 1), ("couple", 1)),
    "one_sentence_lines": (("sale_units", 3), ("quantity_words", 1)),
    "words_or_dozens": (("quantity_words", 3), ("dozen", 2), ("half_dozen", 1), ("couple", 1)),
}
NOT_ORDER_KINDS = ("question", "greeting", "complaint")
SOURCE = "message"  # the only line source of a WhatsApp message
BASE_DATE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
MAX_LINE_CHARS = 160
MAX_BODY_CHARS = 1000  # well under the WhatsApp limit of 4096
MIN_CONTEXT_CHARS = 40  # chatty text around the single line

DATASET_DIR = EVALS_DIR / "datasets" / "whatsapp_order_extraction"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
TEXTS_DIR = DATASET_DIR / "texts"
MESSAGES_DIR = DATASET_DIR / "messages"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
REVIEW_PATH = DATASET_DIR / "second_pass_review.jsonl"
BLIND_DIR = ROOT / ".runtime" / "whatsapp_second_pass"  # gitignored input of the blind annotators
BLIND_BATCHES = 4

# The second pass reuses the phase 03 comparison: same reading shape, same disagreements and decisions.
__all__ = ["REVIEW_DECISIONS", "build_review", "review_disagreements"]

PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}
CUSTOMER_BY_CODE = {c.code: c for c in CUSTOMERS}


# ---------- planning ----------


def _unit(rng, category: str, product) -> tuple[str, int, dict]:
    """Unit expression, expected quantity in sale units and extra trap fields."""
    pack = pack_size(product.sale_unit) if product else None
    styles = [(s, w) for s, w in UNIT_WEIGHTS[category] if s != "items_to_packs" or (pack or 0) >= 10]
    if product is None:  # out-of-catalog lines keep the quantity simple
        styles = [(s, w) for s, w in styles if s in ("sale_units", "quantity_words")] or [("quantity_words", 1)]
    style = rng.choices([s for s, _ in styles], weights=[w for _, w in styles])[0]
    if style == "quantity_words":
        return style, rng.choice(WORD_QUANTITIES), {}
    if style == "items_to_packs":
        quantity = rng.randint(1, 10)
        return style, quantity, {"items_requested": quantity * pack}
    if style == "dozen":
        return style, rng.choice([12, 24, 36, 48]), {}
    if style == "half_dozen":
        return style, 6, {}
    if style == "couple":
        return style, 2, {}
    return style, rng.choice(GENERIC_QUANTITIES), {}


def _closest_sibling(product, siblings) -> str:
    others = [s for s in siblings[product.family] if s.sku != product.sku]
    return max(others, key=lambda s: (SequenceMatcher(None, s.name, product.name).ratio(), s.sku)).sku


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    products = sorted(PRODUCTS, key=lambda p: p.sku)
    siblings = defaultdict(list)
    for p in products:
        siblings[p.family].append(p)
    uses = Counter()
    messages = []
    for category in CATEGORIES:
        group = []
        if category == "not_an_order":
            kinds = [NOT_ORDER_KINDS[i % len(NOT_ORDER_KINDS)] for i in range(MESSAGES_PER_CATEGORY)]
            rng.shuffle(kinds)
            group = [{"category": category, "is_order": False, "kind": kind, "lines": []} for kind in kinds]
        else:
            counts = LINE_COUNTS[category]
            sizes = [counts[i % len(counts)] for i in range(MESSAGES_PER_CATEGORY)]
            rng.shuffle(sizes)
            total = sum(sizes)
            traps = []
            for trap, share in TRAP_SHARES:
                traps += [trap] * round(share * total)
            traps += ["plain"] * (total - len(traps))
            rng.shuffle(traps)
            for size in sizes:
                message_traps, traps = traps[:size], traps[size:]
                in_catalog = sum(t != "out_of_catalog" for t in message_traps)
                chosen = sorted(products, key=lambda p: (uses[p.sku], rng.random()))[:in_catalog]
                items = rng.sample(OUT_OF_CATALOG_ITEMS, size - in_catalog)
                lines = []
                for trap in message_traps:
                    product = None if trap == "out_of_catalog" else chosen.pop()
                    unit_style, quantity, extra = _unit(rng, category, product)
                    line = {
                        "expected_sku": product.sku if product else None,
                        "expected_quantity": quantity,
                        "unit_style": unit_style,
                        "trap": {"style": trap, **extra},
                    }
                    if product:
                        uses[product.sku] += 1
                        line["sale_unit"] = product.sale_unit
                    else:
                        line["trap"]["requested_item"] = items.pop()
                    if trap == "near_miss":
                        line["trap"]["confusable_with"] = _closest_sibling(product, siblings)
                    lines.append(line)
                group.append({"category": category, "is_order": True, "lines": lines})
        rng.shuffle(group)
        for i, message in enumerate(group):
            message["split"] = "dev" if i < DEV_PER_CATEGORY else "test"
        messages += group

    # Messages are numbered in a seeded order so ids do not reveal the category.
    rng.shuffle(messages)
    for number, message in enumerate(messages, start=1):
        customer = rng.choice(CUSTOMERS)
        message["id"] = f"WA-{number:04d}"
        message["dataset_version"] = DATASET_VERSION
        message["customer_code"] = customer.code
        message["sender"] = customer.phone
        message["message_id"] = f"wamid.dataset.{message['id']}"
        message["timestamp"] = str(int(BASE_DATE.timestamp()) + 3600 * 7 * number + 60 * (13 * number % 60))
        message["source"] = SOURCE
        for n, line in enumerate(message["lines"], start=1):
            line["line_id"] = f"{message['id']}-L{n}"
            line["location"] = SOURCE
    return messages


# ---------- rendering ----------


def render_message(planned: dict, text: dict) -> bytes:
    """The WhatsApp message file of one planned message and its written text; same input, same bytes."""
    message = {
        "from": planned["sender"],
        "message_id": planned["message_id"],
        "text": {"body": text["body"]},
        "timestamp": planned["timestamp"],
        "type": "text",
    }
    return (json.dumps(message, indent=2, sort_keys=True) + "\n").encode("utf-8")


# ---------- validation ----------


def _flat(text: str) -> str:
    """Whitespace-insensitive form used to find a line text inside the message."""
    return " ".join(text.split())


def _contains_name(text: str, sku: str) -> bool:
    return f" {match_key(PRODUCT_BY_SKU[sku].name)} " in f" {match_key(text)} "


def validate_line(line: dict, line_text: str) -> list[str]:
    """Reasons one written line is rejected; empty when it passes."""
    lid, sku, quantity = line["line_id"], line["expected_sku"], line["expected_quantity"]
    errors = []
    if sku is not None and sku not in PRODUCT_BY_SKU:
        errors.append(f"{lid}: expected SKU {sku} does not exist")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        errors.append(f"{lid}: expected quantity {quantity!r} is not a positive whole number")
    if (sku is None) != (line["trap"]["style"] == "out_of_catalog"):
        errors.append(f"{lid}: only out_of_catalog lines expect a null SKU")
    text = line_text.strip()
    if not text:
        return [*errors, f"{lid}: line text is empty"]
    if "\n" in text or "\t" in text:
        errors.append(f"{lid}: line text must be one line")
    if len(text) > MAX_LINE_CHARS:
        errors.append(f"{lid}: line text longer than {MAX_LINE_CHARS} characters")
    if not text.isascii():
        errors.append(f"{lid}: line text must be plain ASCII")
    if errors:
        return errors
    style = line["trap"]["style"]
    if style == "out_of_catalog" and match_key(text) in {match_key(p.name) for p in PRODUCTS}:
        errors.append(f"{lid}: out_of_catalog text is a catalog name")
    if style in ("typo", "near_miss") and _contains_name(text, sku):
        errors.append(f"{lid}: {style} text contains the exact catalog name")
    if style == "near_miss" and _contains_name(text, line["trap"]["confusable_with"]):
        errors.append(f"{lid}: near_miss text names the confusable product")
    unit, words = line["unit_style"], text.lower()
    if unit == "sale_units" and not has_number(text, quantity):
        errors.append(f"{lid}: quantity {quantity} not written as a figure")
    elif unit == "items_to_packs" and not has_number(text, line["trap"]["items_requested"]):
        errors.append(f"{lid}: item count {line['trap']['items_requested']} not written as a figure")
    elif unit == "quantity_words" and (has_number(text, quantity) or not re.search(r"[a-z]", words)):
        errors.append(f"{lid}: quantity {quantity} must be written in words")
    elif unit == "dozen" and "dozen" not in words:
        errors.append(f"{lid}: dozen expression missing")
    elif unit == "half_dozen" and not ("half" in words and "dozen" in words):
        errors.append(f"{lid}: half dozen expression missing")
    elif unit == "couple" and "couple" not in words:
        errors.append(f"{lid}: couple expression missing")
    elif not supports_quantity(text, quantity, line.get("sale_unit")):
        # the runtime check of clarification.line_doubts for the free-text channels
        errors.append(f"{lid}: line text does not state the quantity {quantity}")
    return errors


def validate_message(planned: dict, text: dict) -> list[str]:
    """Reasons a written message is rejected before rendering; empty when it passes."""
    errors = []
    body = text.get("body") or ""
    written = text.get("lines") or {}
    if not body.strip() or len(body) > MAX_BODY_CHARS:
        errors.append(f"body must have 1 to {MAX_BODY_CHARS} characters")
    if not body.isascii():
        errors.append("body must be plain ASCII (no accents or emoji)")
    planned_ids = [line["line_id"] for line in planned["lines"]]
    if sorted(written) != sorted(planned_ids):
        errors.append(f"written lines {sorted(written)} do not match the planned lines {planned_ids}")
        return errors
    if planned["is_order"] != bool(planned["lines"]):
        errors.append("an order needs lines and a non-order has none")
    for line in planned["lines"]:
        errors += validate_line(line, written[line["line_id"]])
    if errors:
        return errors
    category, flat_body = planned["category"], _flat(body).lower()
    texts = [_flat(written[i]).lower() for i in planned_ids]
    for i, t in zip(planned_ids, texts, strict=True):
        if t not in flat_body:
            errors.append(f"{i}: planned line missing from the message")
    if errors:
        return errors
    if category == "short_list":
        body_lines = [_flat(b).lower() for b in body.splitlines()]
        shared = [
            i
            for i, t in zip(planned_ids, texts, strict=True)
            if not any(t in b and sum(o in b for o in texts) == 1 for b in body_lines)
        ]
        if shared:
            errors.append(f"short_list lines must each sit on their own message line: {', '.join(shared)}")
    if category == "one_sentence_lines" and "\n" in body.strip():
        errors.append("one_sentence_lines body must be one line")
    if category == "chatty_single_line":
        rest = flat_body.replace(texts[0], " ")
        if len(_flat(rest)) < MIN_CONTEXT_CHARS:
            errors.append(f"chatty_single_line needs at least {MIN_CONTEXT_CHARS} characters of context")
    return errors


def check_rendered(planned: dict, text: dict, data: bytes) -> list[str]:
    """Read the rendered file with the WhatsApp intake model and confirm the sender and the planned lines."""
    try:
        message = WhatsAppMessage.model_validate(json.loads(data))
    except (ValueError, ValidationError) as error:
        return [f"rendered message cannot be read: {error}"]
    errors = []
    customer = CUSTOMER_BY_CODE[planned["customer_code"]]
    if phone_digits(message.sender) != phone_digits(customer.phone):
        errors.append(f"rendered sender {message.sender} is not the phone of {customer.code}")
    if message.type != "text" or message.text is None:
        return [*errors, "rendered message is not a text message"]
    flat_body = _flat(message.text.body).lower()
    for line in planned["lines"]:
        if _flat(text["lines"][line["line_id"]]).lower() not in flat_body:
            errors.append(f"{line['line_id']}: planned line missing from the rendered message")
    return errors


def load_texts(directory: Path | None = None) -> dict[str, dict]:
    texts = {}
    for path in sorted((directory or TEXTS_DIR).glob("*.jsonl")):
        for row in read_jsonl(path):
            texts[row["id"]] = row
    return texts


def build_dataset(plan: list[dict], texts: dict[str, dict]) -> tuple[list[dict], dict[str, bytes], dict[str, list]]:
    """Validate and render every planned message; return dataset rows, rendered files and rejected ids with reasons."""
    rows, files, rejected, seen = [], {}, {}, {}
    for planned in plan:
        text = texts.get(planned["id"]) or {}
        errors = validate_message(planned, text)
        if not errors:
            key = match_key(text["body"])
            if key in seen:
                errors.append(f"duplicates {seen[key]} after normalisation")
            seen.setdefault(key, planned["id"])
        if not errors:
            data = render_message(planned, text)
            errors = check_rendered(planned, text, data)
            files[planned["id"]] = data
        if errors:
            rejected[planned["id"]] = errors
            continue
        lines = [dict(line, text=text["lines"][line["line_id"]]) for line in planned["lines"]]
        rows.append(dict(planned, body=text["body"], lines=lines, file=f"messages/{planned['id']}.json"))
    return rows, files, rejected


def load_dataset(path: Path | None = None) -> list[dict]:
    return read_jsonl(path or DATASET_PATH)


# ---------- second-pass review ----------


def blind_batches(dataset: list[dict], messages_dir: Path | None = None) -> list[list[dict]]:
    """The model text of every rendered message, with no label, plan, category or trap, in BLIND_BATCHES id ranges."""
    rows = []
    for message in dataset:
        data = json.loads(((messages_dir or MESSAGES_DIR) / f"{message['id']}.json").read_text(encoding="utf-8"))
        rows.append({"id": message["id"], "text": model_text({"body": WhatsAppMessage.model_validate(data).text.body})})
    size, extra = divmod(len(rows), BLIND_BATCHES)
    batches, start = [], 0
    for n in range(BLIND_BATCHES):
        end = start + size + (1 if n < extra else 0)
        batches.append(rows[start:end])
        start = end
    return batches


def cmd_blind(args) -> int:
    batches = blind_batches(load_dataset())
    BLIND_DIR.mkdir(parents=True, exist_ok=True)
    catalog = "".join(f"{p.sku} | {p.name} | {p.sale_unit}\n" for p in PRODUCTS)
    (BLIND_DIR / "catalog.txt").write_text("SKU | name | sale unit\n" + catalog, encoding="utf-8", newline="\n")
    for n, batch in enumerate(batches, start=1):
        write_jsonl(BLIND_DIR / f"batch-{n}.jsonl", batch)
        print(f"batch-{n}.jsonl: {len(batch)} messages, {batch[0]['id']} to {batch[-1]['id']}")
    print(f"Wrote the catalog and {len(batches)} blind batches -> {BLIND_DIR}")
    return 0


def cmd_review(args) -> int:
    reviews = [row for path in args.annotations for row in read_jsonl(Path(path))]
    dataset = load_dataset()
    read_ids = {row["id"] for row in reviews}
    missing = [m["id"] for m in dataset if m["id"] not in read_ids]
    if missing:
        print(f"{len(missing)} messages not annotated: {', '.join(missing)}")
        return 1
    previous = read_jsonl(REVIEW_PATH) if REVIEW_PATH.exists() else []
    records = build_review(dataset, reviews, previous)
    write_jsonl(REVIEW_PATH, records)
    counts = Counter(r["decision"] for r in records)
    print(f"Reviewed {len(records)} messages -> {REVIEW_PATH}")
    print("  " + ", ".join(f"{d} {counts[d]}" for d in (*REVIEW_DECISIONS, "pending")))
    return 1 if counts["pending"] else 0


# ---------- commands ----------


def cmd_plan(args) -> int:
    plan = build_plan(args.seed)
    write_jsonl(PLAN_PATH, plan)
    lines = sum(len(m["lines"]) for m in plan)
    catalog = sum(1 for m in plan for line in m["lines"] if line["expected_sku"])
    print(f"Planned {len(plan)} messages with {lines} expected lines ({catalog} in the catalog), seed {args.seed}")
    print(f"  -> {PLAN_PATH}")
    return 0


def cmd_check(args) -> int:
    plan = {m["id"]: m for m in read_jsonl(PLAN_PATH)}
    batch = read_jsonl(Path(args.batch))
    unknown = [str(row.get("id")) for row in batch if row.get("id") not in plan]
    if unknown:
        print(f"ids not in the plan: {', '.join(unknown)}")
        return 1
    others = {k: v for k, v in load_texts().items() if k not in {row["id"] for row in batch}}
    subset = [plan[row["id"]] for row in batch]
    known = [m for m in plan.values() if m["id"] in others]
    _, _, rejected = build_dataset(known + subset, {**others, **{row["id"]: row for row in batch}})
    failed = 0
    for planned in subset:
        if planned["id"] in rejected:
            failed += 1
            print(f"{planned['id']}: {'; '.join(rejected[planned['id']])}")
    print(f"{len(batch)} messages checked, {failed} rejected")
    return 1 if failed else 0


def cmd_build(args) -> int:
    plan = read_jsonl(PLAN_PATH)
    rows, files, rejected = build_dataset(plan, load_texts())
    for message_id, errors in rejected.items():
        print(f"{message_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} of {len(plan)} messages rejected; rewrite their texts and build again")
        return 1
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    for message_id, data in files.items():
        (MESSAGES_DIR / f"{message_id}.json").write_bytes(data)
    write_jsonl(DATASET_PATH, rows)
    counts = Counter(row["category"] for row in rows)
    lines = sum(len(row["lines"]) for row in rows)
    print(f"Built {len(rows)} messages with {lines} expected lines -> {DATASET_PATH}")
    print("  " + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    return 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("whatsapp-dataset", help="plan, check and build the WhatsApp order extraction dataset")
    dsub = dataset.add_subparsers(dest="whatsapp_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded message plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_plan)
    check = dsub.add_parser("check", help="validate and render a batch of written messages against the plan")
    check.add_argument("batch", help="JSONL file with one written message per row")
    check.set_defaults(handler=cmd_check)
    dsub.add_parser("build", help="validate, render the message files and write the dataset").set_defaults(
        handler=cmd_build
    )
    dsub.add_parser("blind", help="write the catalog and the message texts for the blind annotators").set_defaults(
        handler=cmd_blind
    )
    review = dsub.add_parser("review", help="compare blind second-pass annotations with the labels")
    review.add_argument("annotations", nargs="+", help="JSONL files with one blind reading per message")
    review.set_defaults(handler=cmd_review)
