"""`order_scenarios` evaluation (phase 05, deviation 05.1, C21, C22).

Each scenario is one versioned JSON file: the initial database state, the inbox message, the scripted customer
answers (written from the customer's intent before seeing any question), the steps (deliver, answer, re-deliver,
resume after a crash), the allowed effects and the expected final state.

Build the versioned scenario files with `uv run python -m purchase_cycle.evaluation.scenarios_eval`.
"""

import json
from pathlib import Path

from purchase_cycle.catalog import CUSTOMERS, PRODUCTS
from purchase_cycle.config import EVALS_DIR

SUITE = "order_scenarios"
DATASET_VERSION = "1.0"
DATASET_DIR = EVALS_DIR / "datasets" / "order_scenarios"
CHANNELS = ("whatsapp", "email", "web_form")
TAGS = ("clarification", "two_answers", "unknown_product", "crash_resume", "redelivery")
# C21 minimums: scenarios in all, per channel and per tag.
MINIMUMS = {"scenarios": 30, "per_channel": 8, "clarification": 10, "two_answers": 5, "unknown_product": 3,
            "crash_resume": 3, "redelivery": 3}  # fmt: skip
INITIAL_STATE = {"seed": "db.seed: catalog, stock and customers; no orders, clarifications or failures"}
PRICES = {p.sku: p.price_eur for p in PRODUCTS}

# name: (unit word, text the customer writes, SKU the customer means; None when the catalog does not hold it)
PRODUCTS_WRITTEN = {
    "gloves_m": ("boxes", "nitrile gloves M", "GLV-NIT-M"),
    "saline": ("bottles", "saline 500 ml", "SAL-500"),
    "tape": ("boxes", "paper tape 2.5 cm", "TAPE-PAP-25"),
    "bandage": ("packs", "cohesive bandage 7.5 cm", "BND-COH-7"),
    "sharps": ("units", "sharps container 5 litres", "SHARPS-5"),
    "lumbar": ("units", "lumbar support belt", "SUP-LUMBAR"),
    "spot": ("boxes", "spot plasters", "PLST-SPOT"),
    "cushion": ("units", "wheelchair cushion", "WHEELCHAIR-CUSH"),
    # Ambiguous texts: the catalog holds several candidates; the SKU is the one the scripted answer names.
    "gloves": ("boxes", "nitrile gloves", "GLV-NIT-L"),
    "syringe": ("boxes", "syringe 10 ml", "SYR-LL-10"),
    "strips": ("boxes", "blood glucose test strips", "GLU-STRIP-100"),
    "cream": ("tubes", "hand cream", "CREAM-HAND-50"),
    "thermo": ("units", "digital thermometer", "THERM-DIG-FLEX"),
    "oxi": ("units", "pulse oximeter", "OXI-FING"),
    # Unknown products: out of the catalog.
    "oxygen": ("units", "oxygen concentrator", None),
}
ANSWERS = {
    "gloves": "Size L please, all of them.",
    "syringe": "The luer lock ones.",
    "strips": "The boxes of 100 strips.",
    "cream": "The 50 ml tubes.",
    "thermo": "The ones with the flexible tip.",
    "oxi": "The adult ones.",
    "oxygen": "Then leave the oxygen concentrator out, we will buy it elsewhere.",
}
VAGUE = "Let me check with the nurse in charge and I will get back to you."

# (channel, lines as (product, quantity), tags, steps, answers, crash point, expected lines as (product, quantity))
_SAME = None  # expected lines equal the requested ones
SCENARIOS = [
    ("whatsapp", [("gloves_m", 20), ("saline", 12)], [], ["deliver"], [], None, _SAME),
    ("whatsapp", [("tape", 5), ("bandage", 8), ("spot", 10)], [], ["deliver"], [], None, _SAME),
    ("whatsapp", [("gloves", 20), ("sharps", 6)], ["clarification"], ["deliver", "answer"], ["gloves"], None, _SAME),
    ("whatsapp", [("syringe", 10), ("lumbar", 2)], ["clarification"], ["deliver", "answer"], ["syringe"], None, _SAME),
    ("whatsapp", [("strips", 4), ("saline", 24)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "strips"], None, _SAME),
    ("whatsapp", [("cream", 6), ("gloves_m", 10)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "cream"], None, _SAME),
    ("whatsapp", [("oxygen", 1), ("gloves_m", 10)], ["clarification", "unknown_product"], ["deliver", "answer"],
     ["oxygen"], None, [("gloves_m", 10)]),
    ("whatsapp", [("cushion", 2), ("tape", 4)], ["crash_resume"], ["deliver", "resume"], [], "after_store_commit",
     _SAME),
    ("whatsapp", [("bandage", 6), ("spot", 5)], ["redelivery"], ["deliver", "redeliver"], [], None, _SAME),
    ("whatsapp", [("thermo", 3), ("sharps", 2)], ["clarification", "redelivery"], ["deliver", "redeliver", "answer"],
     ["thermo"], None, _SAME),
    ("email", [("gloves_m", 20), ("saline", 12)], [], ["deliver"], [], None, _SAME),
    ("email", [("tape", 5), ("bandage", 8), ("spot", 10)], [], ["deliver"], [], None, _SAME),
    ("email", [("gloves", 20), ("sharps", 6)], ["clarification"], ["deliver", "answer"], ["gloves"], None, _SAME),
    ("email", [("syringe", 10), ("lumbar", 2)], ["clarification"], ["deliver", "answer"], ["syringe"], None, _SAME),
    ("email", [("strips", 4), ("saline", 24)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "strips"], None, _SAME),
    ("email", [("oxi", 2), ("lumbar", 1)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "oxi"], None, _SAME),
    ("email", [("oxygen", 1), ("gloves_m", 10)], ["clarification", "unknown_product"], ["deliver", "answer"],
     ["oxygen"], None, [("gloves_m", 10)]),
    ("email", [("cushion", 2), ("tape", 4)], ["crash_resume"], ["deliver", "resume"], [], "after_channel_steps", _SAME),
    ("email", [("bandage", 6), ("spot", 5)], ["redelivery"], ["deliver", "redeliver"], [], None, _SAME),
    ("email", [("sharps", 10)], [], ["deliver"], [], None, _SAME),
    ("web_form", [("gloves_m", 20), ("saline", 12)], [], ["deliver"], [], None, _SAME),
    ("web_form", [("tape", 5), ("bandage", 8), ("spot", 10)], [], ["deliver"], [], None, _SAME),
    ("web_form", [("gloves", 20), ("sharps", 6)], ["clarification"], ["deliver", "answer"], ["gloves"], None, _SAME),
    ("web_form", [("syringe", 10), ("lumbar", 2)], ["clarification"], ["deliver", "answer"], ["syringe"], None, _SAME),
    ("web_form", [("strips", 4), ("saline", 24)], ["clarification", "two_answers"], ["deliver", "answer", "answer"],
     [VAGUE, "strips"], None, _SAME),
    ("web_form", [("oxygen", 1), ("gloves_m", 10)], ["clarification", "unknown_product"], ["deliver", "answer"],
     ["oxygen"], None, [("gloves_m", 10)]),
    ("web_form", [("cushion", 2), ("tape", 4)], ["crash_resume"], ["deliver", "resume"], [], "in_reply", _SAME),
    ("web_form", [("bandage", 6), ("spot", 5)], ["redelivery"], ["deliver", "redeliver"], [], None, _SAME),
    ("web_form", [("thermo", 5)], ["clarification"], ["deliver", "answer"], ["thermo"], None, _SAME),
    ("web_form", [("gloves_m", 600), ("saline", 6)], ["clarification"], ["deliver", "answer"],
     ["Sorry, that was a typo: 60 boxes of the gloves, not 600."], None, [("gloves_m", 60), ("saline", 6)]),
]  # fmt: skip


def _written(product: str, quantity: int) -> str:
    unit, text, _ = PRODUCTS_WRITTEN[product]
    return f"{quantity} {unit} of {text}"


def _message(channel: str, lines: list, customer) -> dict:
    """The inbox message as the customer sends it; the runner writes it in the channel file format."""
    if channel == "web_form":
        return {"lines": [{"product": PRODUCTS_WRITTEN[p][1], "quantity": q} for p, q in lines]}
    written = [_written(p, q) for p, q in lines]
    if channel == "whatsapp":
        return {"body": "hi, pls send " + " and ".join(written) + " thx"}
    body = "Hello,\nPlease send:\n" + "".join(f"- {w}\n" for w in written) + f"Thanks,\n{customer.contact_name}"
    return {"subject": "Order", "body": body}


def _expected_lines(lines: list) -> list[dict]:
    rows = [(PRODUCTS_WRITTEN[p][2], q) for p, q in lines]
    return [{"sku": sku, "quantity": q, "price_eur": PRICES[sku]} for sku, q in rows if sku is not None]


def build_items() -> list[dict]:
    items = []
    for n, (channel, lines, tags, steps, answers, crash_at, expected) in enumerate(SCENARIOS, start=1):
        customer = CUSTOMERS[n % len(CUSTOMERS)]
        asks = "clarification" in tags
        effects = ["store_order", "reply"] + (["ask_question"] if asks else [])
        effects += ["skip_duplicate"] if "redelivery" in tags else []
        items.append(
            {
                "id": f"OS-{n:03d}",
                "dataset_version": DATASET_VERSION,
                "channel": channel,
                "customer_code": customer.code,
                "tags": tags,
                "initial_state": INITIAL_STATE,
                "message": _message(channel, lines, customer),
                "answers": [ANSWERS.get(a, a) for a in answers],
                "steps": steps,
                "crash_at": crash_at,
                "requested": [{"text": PRODUCTS_WRITTEN[p][1], "sku": PRODUCTS_WRITTEN[p][2]} for p, _ in lines],
                "allowed_effects": effects,
                "expected": {
                    "orders": 1,
                    "lines": _expected_lines(lines if expected is None else expected),
                    "clarification": "answered" if asks else None,
                    "needs_review": 0,
                },
            }
        )
    return items


def write_items(items: list[dict], folder: Path = DATASET_DIR) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        text = json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def load_dataset(folder: Path = DATASET_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("OS-*.json"))]


def main() -> None:
    items = build_items()
    write_items(items)
    print(f"Built {len(items)} order scenarios -> {DATASET_DIR}")


if __name__ == "__main__":
    main()
