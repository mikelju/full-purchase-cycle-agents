"""Golden dataset of the `document_extraction` evaluation: seeded plan, validation, formats, build and second pass.

The seeded plan fixes, before any text exists, the kind, purchase order, supplier, currency and lines (real catalog SKUs
and sale units) of every supplier document, so the labels are decided by construction.
The coding agent writes the document texts; this module checks that each text states what its label needs,
renders a share of them to PDF and Excel, and joins plan and texts into the dataset.
"""

import io
import random
import re
from collections import Counter
from pathlib import Path

from purchase_cycle import llm
from purchase_cycle.catalog import PRODUCTS
from purchase_cycle.config import EVALS_DIR, ROOT
from purchase_cycle.email_order import pdf_text, xlsx_text
from purchase_cycle.evaluation.email_dataset import BASE_DATE, _fixed_zip
from purchase_cycle.evaluation.planning import read_jsonl, write_jsonl
from purchase_cycle.quantities import pack_size
from purchase_cycle.reconciliation import PO_REFERENCE
from purchase_cycle.suppliers import SUPPLIER_FAMILIES, SUPPLIERS
from purchase_cycle.web_form import match_key

DATASET_VERSION = "1.0"
SEED = 20261013
DOCUMENTS_PER_CATEGORY = 12
DEV_PER_CATEGORY = 3  # 25% development, 75% test

CATEGORIES = (
    "clean_invoice",
    "delivery_note",
    "price_basis",
    "several_lines",
    "other_unit",
    "vat_totals",
    "chatty",
    "reference_forms",
    "extra_charge",
    "messy_layout",
)
QUANTITIES = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 500)
VAT_RATES = (4, 10, 21)
REFERENCE_FORMS = (
    "PO-{:04d}",
    "PO {:04d}",
    "po-{:04d}",
    "PO{:04d}",
    "PO - {:04d}",
    "PO-{}",
    "PO {}",
    "Po-{:04d}",
    "PO-{:06d}",
    "PO-{:04d}",
    "PO {}",
    "po{:04d}",
)
OTHER_UNITS = ("carton", "carton", "case", "case", "dozen", "dozen", "pcs", "pcs", "ea", "ea", "pack", "pack")
CHARGES = ("freight", "discount_pct", "discount_amount", "freight_discount", "handling", "pallet")
LAYOUTS = ("columns", "columns", "wrapped_names", "wrapped_names", "pipes", "pipes") + (
    "reordered",
    "reordered",
    "stacked",
    "stacked",
    "semicolons",
    "semicolons",
)
MIN_CHARS, MAX_CHARS, MAX_LINE = 60, 3000, 100
PRICE_PATTERN = re.compile(r"\d+[.,]\d{2}\b")
PRICE_TOLERANCE = 0.005  # half a cent
REVIEW_DECISIONS = ("agree", "label_fixed", "annotator_wrong")

DATASET_DIR = EVALS_DIR / "datasets" / "document_extraction"
PLAN_PATH = DATASET_DIR / "plan.jsonl"
TEXTS_DIR = DATASET_DIR / "texts"
FILES_DIR = DATASET_DIR / "files"
DATASET_PATH = DATASET_DIR / "dataset.jsonl"
REVIEW_PATH = DATASET_DIR / "second_pass_review.jsonl"
BLIND_DIR = ROOT / ".runtime" / "document_second_pass"  # gitignored input of the blind annotator

PRODUCTS_SORTED = sorted(PRODUCTS, key=lambda p: p.sku)
SKU_PATTERNS = {p.sku: re.compile(rf"(?<![a-z0-9-]){re.escape(p.sku.lower())}(?![a-z0-9-])") for p in PRODUCTS}
_FAMILY_PAIRS = set(SUPPLIER_FAMILIES)
SUPPLIER_POOL = {s.code: [p for p in PRODUCTS_SORTED if (s.code, p.family) in _FAMILY_PAIRS] for s in SUPPLIERS}


# ---------- planning ----------


def _price(rng, product) -> float:
    return max(0.5, round(product.price_eur * rng.uniform(0.85, 1.2), 2))


def _document(rng, category, variant, kind, n_lines=1, pool=None) -> dict:
    """One planned document: purchase order and supplier that go together, and distinct real catalog lines."""
    number = rng.randint(1, 60)
    supplier = SUPPLIERS[(number - 1) % len(SUPPLIERS)]
    candidates = [p for p in SUPPLIER_POOL[supplier.code] if pool is None or pool(p)]
    lines = []
    for product in rng.sample(candidates, n_lines):
        quantity = rng.choice(QUANTITIES)
        price = None if kind == "delivery_note" else _price(rng, product)
        lines.append(
            {
                "sku": product.sku,
                "product": product.name,
                "sale_unit": product.sale_unit,
                "quantity": quantity,
                "unit_price": price,
                "line_total": None if price is None else round(quantity * price, 2),
                "written_as": rng.choice(("sku", "name")),
                "written_unit": None,
            }
        )
    prefix = ("INV", "FA", "F") if kind == "invoice" else ("DN", "ALB", "DEL")
    return {
        "category": category,
        "variant": variant,
        "kind": kind,
        "number": f"{rng.choice(prefix)}-{rng.randint(1000, 99999)}",
        "po_number": number,
        "po_reference": f"PO-{number:04d}",
        "reference_written": f"PO-{number:04d}",
        "supplier_code": supplier.code,
        "supplier_name": supplier.name,
        "supplier_email": supplier.email,
        "supplier_written": rng.choice((supplier.name,) * 3 + (supplier.email,)),
        "currency": "EUR" if kind == "invoice" else None,
        "issue_date": f"2026-{rng.randint(9, 10):02d}-{rng.randint(1, 28):02d}",
        "lines": lines,
        "distractors": [],
        "must_contain": [],
    }


def _subtotal(doc) -> float:
    return round(sum(x["line_total"] for x in doc["lines"]), 2)


def _kind(n, every=3) -> str:
    return "delivery_note" if n % every == every - 1 else "invoice"


def _bank_and_phone(rng) -> tuple[str, ...]:
    return (
        f"+34 91{rng.randint(2, 9)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
        f"ES{rng.randint(10, 99)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)} {rng.randint(10, 99)} "
        f"{rng.randint(1000000000, 9999999999)}",
        f"{rng.randint(10, 52):d}000 {rng.choice(('Madrid', 'Bilbao', 'Valencia', 'Sevilla'))}",
        f"SO-{rng.randint(10000, 99999)}",
        f"tel {rng.randint(600, 699)} {rng.randint(100, 999)} {rng.randint(100, 999)}",
        f"VAT ID ES{rng.choice('ABCD')}{rng.randint(1000000, 9999999)}",
    )


def _charges(rng, doc, kind) -> None:
    subtotal = _subtotal(doc)
    freight, pct = round(rng.uniform(8, 45), 2), rng.choice((5, 10))
    discount = round(subtotal * pct / 100, 2)
    plan = {
        "freight": ([f"{freight:.2f}"], ["freight"]),
        "discount_pct": ([f"{discount:.2f}", f"{pct}%"], ["discount"]),
        "discount_amount": ([f"{freight:.2f}"], ["discount"]),
        "freight_discount": ([f"{freight:.2f}", f"{discount:.2f}"], ["freight", "discount"]),
        "handling": ([f"{freight:.2f}"], ["handling"]),
        "pallet": ([f"{freight * 3:.2f}"], ["pallet"]),
    }
    doc["distractors"], doc["must_contain"] = plan[kind]


def _category_documents(rng, category: str) -> list[dict]:
    rows = []
    for n in range(DOCUMENTS_PER_CATEGORY):
        if category == "clean_invoice":
            rows.append(_document(rng, category, "single" if n < 8 else "two_lines", "invoice", 1 if n < 8 else 2))
        elif category == "delivery_note":
            rows.append(_document(rng, category, f"lines_{n % 3 + 1}", "delivery_note", n % 3 + 1))
        elif category == "price_basis":
            packs = lambda p: (pack_size(p.sale_unit) or 0) >= 10  # noqa: E731
            doc = _document(rng, category, "pack_price", "invoice", 1, pool=packs)
            if n >= 6:
                line = doc["lines"][0]
                doc["variant"] = "pack_price_with_piece_price"
                doc["distractors"] = [f"{line['unit_price'] / pack_size(line['sale_unit']):.3f}"]
            rows.append(doc)
        elif category == "several_lines":
            kind = _kind(n)
            rows.append(_document(rng, category, kind, kind, 3 + n % 4))
        elif category == "other_unit":
            unit = OTHER_UNITS[n]
            single = unit in ("pcs", "ea")
            pool = lambda p, s=single: (p.sale_unit == "unit") == s  # noqa: E731
            doc = _document(rng, category, unit, _kind(n, 4), 1 + n % 2, pool=pool)
            for line in doc["lines"]:
                line["written_unit"] = unit
            rows.append(doc)
        elif category == "vat_totals":
            doc = _document(rng, category, "", "invoice", 2 + n % 2)
            rate, subtotal = rng.choice(VAT_RATES), _subtotal(doc)
            vat = round(subtotal * rate / 100, 2)
            doc["variant"] = f"vat_{rate}"
            doc["distractors"] = [f"{subtotal:.2f}", f"{vat:.2f}", f"{round(subtotal + vat, 2):.2f}"]
            doc["must_contain"] = ["VAT", str(rate)]
            rows.append(doc)
        elif category == "chatty":
            doc = _document(rng, category, "chatty", _kind(n, 4), 1 + n % 2)
            doc["distractors"] = rng.sample(_bank_and_phone(rng), 3)
            rows.append(doc)
        elif category == "reference_forms":
            doc = _document(rng, category, "", _kind(n), 1 + n % 2)
            doc["reference_written"] = REFERENCE_FORMS[n].format(doc["po_number"])
            doc["variant"] = f"form_{n + 1}"
            doc["distractors"] = [f"SO-{rng.randint(10000, 99999)}"]
            rows.append(doc)
        elif category == "extra_charge":
            kind = CHARGES[n % 6]
            doc = _document(rng, category, kind, "invoice", 1 + n % 3)
            _charges(rng, doc, kind)
            rows.append(doc)
        else:  # messy_layout
            rows.append(_document(rng, category, LAYOUTS[n], _kind(n), 2 + n % 2))
    return rows


def build_plan(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    plan = []
    for category in CATEGORIES:
        rows = _category_documents(rng, category)
        dev = set(rng.sample(range(len(rows)), DEV_PER_CATEGORY))
        for n, row in enumerate(rows):
            row["split"] = "dev" if n in dev else "test"
            row["format"] = "pdf" if n % 6 == 1 else "xlsx" if n % 6 == 4 else "txt"
        plan += rows
    for n, row in enumerate(plan, start=1):
        row["id"] = f"DOC-{n:03d}"
    return plan


# ---------- validation and build ----------


def _has_number(body: str, value) -> bool:
    return re.search(rf"(?<![\d.,]){re.escape(str(value))}(?!\d|[.,]\d)", body) is not None


def _has_token(low: str, token: str, plural: bool = False) -> bool:
    """The lower-case token stands alone: no letter, digit or hyphen touches it (a plural s is allowed on request)."""
    ending = "(?:e?s)?" if plural else ""
    return re.search(rf"(?<![a-z0-9-]){re.escape(token.lower())}{ending}(?![a-z0-9-])", low) is not None


def validate_document(planned: dict, text: dict) -> list[str]:
    errors = []
    body = text.get("body")
    if text.get("id") != planned["id"]:
        errors.append(f"text id {text.get('id')!r} does not match the plan id")
    if not isinstance(body, str):
        return [*errors, "no body"]
    if not body.isascii():
        errors.append("body is not ASCII")
    if not MIN_CHARS <= len(body) <= MAX_CHARS:
        errors.append(f"body has {len(body)} characters, expected {MIN_CHARS} to {MAX_CHARS}")
    if planned["format"] != "txt" and max(len(x) for x in body.splitlines()) > MAX_LINE:
        errors.append(f"a line is longer than {MAX_LINE} characters, which does not fit a PDF or Excel page")
    low, key = body.lower(), match_key(body)
    invoice = planned["kind"] == "invoice"
    if invoice and "invoice" not in low:
        errors.append("an invoice must say invoice")
    if not invoice and not any(w in low for w in ("delivery", "dispatch", "despatch", "packing")):
        errors.append("a delivery note must say delivery, dispatch or packing")
    for label, fact in (
        ("document number", planned["number"]),
        ("supplier", planned["supplier_written"]),
        ("issue date", planned["issue_date"]),
    ):
        if not _has_token(low, fact):
            errors.append(f"body must contain the {label} {fact!r}")
    if planned["reference_written"] not in body:
        errors.append(f"body must contain the order reference {planned['reference_written']!r}")
    for match in PO_REFERENCE.finditer(body):
        if int(match.group(1)) != planned["po_number"]:
            errors.append(f"body names another purchase order {match.group(0)!r}")
    wanted = {x["sku"] for x in planned["lines"]}
    for line in planned["lines"]:
        written = (
            _has_token(low, line["sku"]) if line["written_as"] == "sku" else _has_token(key, match_key(line["product"]))
        )
        if not written:
            errors.append(f"body must contain the {line['written_as']} of {line['sku']}")
        if not _has_number(body, line["quantity"]):
            errors.append(f"body must contain the quantity {line['quantity']} of {line['sku']}")
        if line["written_unit"] and not _has_token(low, line["written_unit"], plural=True):
            errors.append(f"body must contain the unit {line['written_unit']!r} of {line['sku']}")
        for field in ("unit_price", "line_total"):
            if line[field] is not None and not _has_number(body, f"{line[field]:.2f}"):
                errors.append(f"body must contain the {field} {line[field]:.2f} of {line['sku']}")
    for sku, pattern in SKU_PATTERNS.items():
        if sku not in wanted and pattern.search(low):
            errors.append(f"body names {sku}, which is not in the label")
    # A catalog name inside the name of a labelled product is not another line: cut the labelled names out first.
    rest = key
    for name in sorted((match_key(p.name) for p in PRODUCTS if p.sku in wanted), key=len, reverse=True):
        rest = rest.replace(name, " ")
    for product in PRODUCTS:
        if product.sku not in wanted and _has_token(rest, match_key(product.name)):
            errors.append(f"body names the product {product.sku}, which is not in the label")
    if invoice and "eur" not in low:
        errors.append("body does not name the currency EUR")
    if not invoice and (PRICE_PATTERN.search(body) or re.search(r"\beur\b|\bprice", low)):
        errors.append("body states a price although the delivery note label has none")
    for fact in [*planned["must_contain"], *planned["distractors"]]:
        if fact.lower() not in low:
            errors.append(f"body must contain {fact!r}")
    return errors


def load_texts(directory: Path | None = None) -> dict[str, dict]:
    texts = {}
    for path in sorted((directory or TEXTS_DIR).glob("*.jsonl")):
        for row in read_jsonl(path):
            texts[row["id"]] = row
    return texts


def render_pdf(body: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_creation_date(BASE_DATE)
    pdf.set_margins(10, 10, 10)
    pdf.add_page()
    pdf.set_font("Courier", size=8)
    for line in body.splitlines():
        pdf.cell(0, 4, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def render_xlsx(body: str) -> bytes:
    """One row per non-empty line; cells are split where two or more spaces or a tab separate them, as strings."""
    from openpyxl import Workbook

    workbook = Workbook()
    stamp = BASE_DATE.replace(tzinfo=None)
    workbook.properties.created = workbook.properties.modified = stamp
    sheet = workbook.active
    sheet.title = "Document"
    for line in body.splitlines():
        if line.strip():
            sheet.append(re.split(r"\t+| {2,}", line.strip()))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _fixed_zip(buffer.getvalue())


def rendered_file(row: dict) -> bytes | None:
    renderer = {"pdf": render_pdf, "xlsx": render_xlsx}.get(row["format"])
    return renderer(row["body"]) if renderer else None


def build_dataset(plan: list[dict], texts: dict[str, dict]) -> tuple[list[dict], dict[str, list[str]]]:
    rows, rejected, seen = [], {}, {}
    for planned in plan:
        text = texts.get(planned["id"]) or {}
        errors = validate_document(planned, text)
        if not errors:
            key = re.sub(r"\W+", " ", text["body"].lower()).strip()
            if key in seen:
                errors.append(f"duplicates {seen[key]}")
            seen.setdefault(key, planned["id"])
        if errors:
            rejected[planned["id"]] = errors
            continue
        row = dict(planned, body=text["body"], dataset_version=DATASET_VERSION)
        if planned["format"] != "txt":
            row["file"] = f"files/{planned['id']}.{planned['format']}"
        rows.append(row)
    return rows, rejected


def load_dataset(path: Path | None = None) -> list[dict]:
    return read_jsonl(path or DATASET_PATH)


def document_text(row: dict) -> str:
    """The text the model reads: the `.txt` body, or the text extracted from the rendered PDF or Excel file."""
    if row["format"] == "txt":
        return row["body"]
    data = (DATASET_DIR / row["file"]).read_bytes()
    return pdf_text(data) if row["format"] == "pdf" else xlsx_text(data)


# ---------- second-pass review ----------


def _catalog_index() -> dict[str, str]:
    index = {}
    for p in PRODUCTS:
        index[match_key(p.sku)] = index[match_key(p.name)] = p.sku
    return index


def _line_summary(lines: list[dict], index: dict, key: str) -> dict:
    return {index.get(match_key(str(x[key])), str(x[key])): (x.get("quantity"), x.get("unit_price")) for x in lines}


def review_disagreements(row: dict, read: dict) -> list[dict]:
    """Differences between the labels of a document and a blind annotator's reading of it."""
    found = [
        {"field": field, "label": row[field], "read": read.get(field)}
        for field in ("kind", "currency")
        if row[field] != read.get(field)
    ]
    match = PO_REFERENCE.search(read.get("po_reference") or "")
    if (f"PO-{int(match.group(1)):04d}" if match else None) != row["po_reference"]:
        found.append({"field": "po_reference", "label": row["po_reference"], "read": read.get("po_reference")})
    index = _catalog_index()
    expected = _line_summary(row["lines"], index, "sku")
    seen = _line_summary(read.get("lines") or [], index, "product")
    same = expected.keys() == seen.keys() and all(
        expected[k][0] == seen[k][0]
        and (expected[k][1] is None or (seen[k][1] is not None and abs(expected[k][1] - seen[k][1]) <= PRICE_TOLERANCE))
        for k in expected
    )
    if not same:
        found.append({"field": "lines", "label": expected, "read": seen})
    return found


def build_review(dataset: list[dict], readings: list[dict], previous: list[dict]) -> list[dict]:
    by_id, kept = {r["id"]: r for r in readings}, {r["id"]: r for r in previous}
    records = []
    for row in dataset:
        read = by_id[row["id"]]
        found = review_disagreements(row, read)
        old = kept.get(row["id"], {})
        records.append(
            {
                "id": row["id"],
                "read": {k: read.get(k) for k in ("kind", "po_reference", "currency", "lines")},
                "notes": read.get("notes", ""),
                "disagreements": found,
                "decision": old.get("decision", "pending") if found else "agree",
                "justification": old.get("justification", "") if found else "",
            }
        )
    return records


# ---------- commands ----------


def cmd_plan(args) -> int:
    plan = build_plan(args.seed)
    write_jsonl(PLAN_PATH, plan)
    print(f"Planned {len(plan)} documents with seed {args.seed} -> {PLAN_PATH}")
    for key in ("kind", "format", "split"):
        print(f"  {key}: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r[key] for r in plan).items())))
    return 0


def cmd_check(args) -> int:
    plan = {r["id"]: r for r in read_jsonl(PLAN_PATH)}
    batch = read_jsonl(Path(args.batch))
    unknown = [str(row.get("id")) for row in batch if row.get("id") not in plan]
    if unknown:
        print(f"ids not in the plan: {', '.join(unknown)}")
        return 1
    failed = 0
    for row in batch:
        errors = validate_document(plan[row["id"]], row)
        if errors:
            failed += 1
            print(f"{row['id']}: {'; '.join(errors)}")
    print(f"{len(batch)} documents checked, {failed} rejected")
    return 1 if failed else 0


def cmd_build(args) -> int:
    plan = read_jsonl(PLAN_PATH)
    rows, rejected = build_dataset(plan, load_texts())
    for doc_id, errors in rejected.items():
        print(f"{doc_id}: {'; '.join(errors)}")
    if rejected:
        print(f"{len(rejected)} of {len(plan)} documents rejected; rewrite their texts and build again")
        return 1
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    for row in rows:
        data = rendered_file(row)
        if data is not None:
            (DATASET_DIR / row["file"]).write_bytes(data)
    lost = [r["id"] for r in rows if validate_document(r, {"id": r["id"], "body": document_text(r)})]
    if lost:
        print(f"rendered files lose a labelled fact: {', '.join(lost)}")
        return 1
    write_jsonl(DATASET_PATH, rows)
    counts = Counter(r["category"] for r in rows)
    print(f"Built {len(rows)} documents -> {DATASET_PATH}")
    print("  categories: " + ", ".join(f"{c} {counts[c]}" for c in CATEGORIES))
    for key in ("split", "format", "kind"):
        print(f"  {key}: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r[key] for r in rows).items())))
    print(f"  rendered files read back with every labelled fact: {sum(r['format'] != 'txt' for r in rows)}")
    return 0


def cmd_blind(args) -> int:
    rows = [{"id": r["id"], "text": document_text(r)} for r in load_dataset()]
    BLIND_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(BLIND_DIR / "documents.jsonl", rows)
    (BLIND_DIR / "rules.txt").write_text(llm.SUPPLIER_DOCUMENT_INSTRUCTIONS, encoding="utf-8", newline="\n")
    print(f"Wrote {len(rows)} blind documents and the label rules -> {BLIND_DIR}")
    return 0


def cmd_review(args) -> int:
    readings = [row for path in args.annotations for row in read_jsonl(Path(path))]
    dataset = load_dataset()
    read_ids = {x["id"] for x in readings}
    missing = [r["id"] for r in dataset if r["id"] not in read_ids]
    if missing:
        print(f"{len(missing)} documents not annotated: {', '.join(missing)}")
        return 1
    previous = read_jsonl(REVIEW_PATH) if REVIEW_PATH.exists() else []
    records = build_review(dataset, readings, previous)
    write_jsonl(REVIEW_PATH, records)
    counts = Counter(r["decision"] for r in records)
    print(f"Reviewed {len(records)} documents -> {REVIEW_PATH}")
    print("  " + ", ".join(f"{d} {counts[d]}" for d in (*REVIEW_DECISIONS, "pending")))
    return 1 if counts["pending"] else 0


def add_commands(sub) -> None:
    dataset = sub.add_parser("document-dataset", help="plan, check and build the document extraction dataset")
    dsub = dataset.add_subparsers(dest="document_dataset_command", required=True)
    plan = dsub.add_parser("plan", help="write the seeded document plan")
    plan.add_argument("--seed", type=int, default=SEED)
    plan.set_defaults(handler=cmd_plan)
    check = dsub.add_parser("check", help="validate a batch of written documents against the plan")
    check.add_argument("batch", help="JSONL file with one written document per row")
    check.set_defaults(handler=cmd_check)
    build = dsub.add_parser("build", help="validate the texts, render the PDF and Excel files, write the dataset")
    build.set_defaults(handler=cmd_build)
    blind = dsub.add_parser("blind", help="write the documents and the label rules for the blind annotator")
    blind.set_defaults(handler=cmd_blind)
    review = dsub.add_parser("review", help="compare a blind annotator's readings with the labels")
    review.add_argument("annotations", nargs="+", help="JSONL files with one blind reading per document")
    review.set_defaults(handler=cmd_review)
