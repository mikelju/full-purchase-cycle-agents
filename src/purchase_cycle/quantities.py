"""Quantity helpers shared by the runtime and the dataset tooling."""

import re

PACK_SIZE = re.compile(r"\bof (\d+)\b")


def has_number(text: str, number: int) -> bool:
    """True when the number appears in the text as a standalone figure."""
    return re.search(rf"(?<![\d.,]){number}(?![\d]|[.,]\d)", text) is not None


def pack_size(sale_unit: str) -> int | None:
    match = PACK_SIZE.search(sale_unit)
    return int(match.group(1)) if match else None


_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
NUMBER_WORDS = {
    **{w: n for n, w in enumerate(_UNITS)},
    **{w: 20 + 10 * n for n, w in enumerate(_TENS)},
    "hundred": 100,
    "thousand": 1000,
}
FIGURE = re.compile(r"(?<![\d.,])\d+(?![\d]|[.,]\d)")


def _word_numbers(tokens: list[str]) -> dict[int, int]:
    """Value of every run of number words, keyed by the index of its last word."""
    found, total, current, last = {}, 0, 0, None
    for i, token in enumerate(tokens + [""]):
        value = NUMBER_WORDS.get(token)
        if value is None and not (token == "and" and last is not None):
            if last is not None:
                found[last] = total + current
            total, current, last = 0, 0, None
            continue
        if value is None:  # "and" inside a run, as in "one hundred and twenty"
            continue
        if value == 100:
            current = (current or 1) * 100
        elif value == 1000:
            total, current = total + (current or 1) * 1000, 0
        else:
            current += value
        last = i
    return found


def stated_numbers(text: str) -> set[int]:
    """Every quantity the text states: figures, number words, dozens, a couple, a or an."""
    numbers = {int(f) for f in FIGURE.findall(text)}
    tokens = re.findall(r"[a-z]+|\d+", text.lower())
    words = _word_numbers(tokens)
    numbers |= set(words.values())
    for i, token in enumerate(tokens):
        if token in ("a", "an", "single"):
            numbers.add(1)
        elif token == "couple":
            numbers.add(2)
        elif token in ("dozen", "dozens"):
            before = tokens[max(0, i - 2) : i]
            if "half" in before:
                numbers.add(6)
            elif before and before[-1].isdigit():
                numbers.add(int(before[-1]) * 12)
            elif i - 1 in words:
                numbers.add(words[i - 1] * 12)
            else:
                numbers.add(12)
    return numbers


def supports_quantity(text: str, quantity: int, sale_unit: str | None = None) -> bool:
    """True when the text states the quantity, as written or as items converted by the pack size."""
    numbers = stated_numbers(text)
    pack = pack_size(sale_unit) if sale_unit else None
    return quantity in numbers or (pack is not None and quantity * pack in numbers)
