"""Critical-error counter shared by `failure_recovery` and `order_scenarios` (phase 05, deviation 05.1, C22).

A critical error is one an average cannot compensate, so each suite gates their count at 0 with no averaging.
The counter reads the final state of one scenario: the business database, the replies sent (the WhatsApp outbox
files and the email and web form reply texts) and the final checkpoint state of each thread, never the node
sequence. The kinds, counted per scenario:

- `duplicate_order`: more orders stored than messages that may start one.
- `price_not_catalog`: a reply line whose unit price is not the catalog price of its product.
- `line_dropped`: with an order stored, a requested line that is neither stored nor still open (its text is in a
  clarification question not yet answered or in a thread's removed or unresolved lines) nor escalated (a
  `needs_review` failures row of one of the scenario threads, or the reply names it as left out of the order).
- `open_doubt`: an order stored by a thread whose clarification is still pending.
- `false_confirmation`: a reply confirming an order number that is not stored, or one other than the order
  stored for the message it answers.
- `source_outside_message`: a stored line whose source text is not in the message it came from.
"""

import re
from pathlib import Path

from purchase_cycle import db
from purchase_cycle.web_form import match_key

KINDS = (
    "duplicate_order",
    "price_not_catalog",
    "line_dropped",
    "open_doubt",
    "false_confirmation",
    "source_outside_message",
)
CONFIRMED = re.compile(r"registered as order (\d+)")
REPLY_LINE = re.compile(r"^- (.+): \d+ x .+ at (\d+(?:\.\d+)?) EUR = ", re.MULTILINE)


def _words(text: str) -> set[str]:
    return set(match_key(text).split())


def count(
    db_path: Path | str,
    threads: dict[str, dict],
    replies: list[dict],
    requested: list[dict],
    message_text: str,
    messages: int = 1,
) -> dict[str, int]:
    """Critical errors of one scenario by kind.

    `threads` maps each thread id to its final checkpoint values; `replies` holds {"in_reply_to", "text"} per reply
    sent (`in_reply_to` is the answered channel message id, or None); `requested` holds the {"text", "sku"} lines the
    customer asked for (`sku` None for a product out of the catalog); `message_text` is the text of the order
    message (with its attachments); `messages` is the number of messages that may start an order.
    """
    conn = db.connect(db_path)
    try:
        order_ids = {r["id"] for r in conn.execute("SELECT id FROM orders")}
        stored_skus = {r["sku"] for r in conn.execute("SELECT sku FROM order_lines")}
        prices = {r["name"]: r["price_eur"] for r in conn.execute("SELECT name, price_eur FROM products")}
        sources = {
            r["message_id"]: r["order_id"] for r in conn.execute("SELECT message_id, order_id FROM order_sources")
        }
        clarifications, questions = {}, []
        for r in conn.execute("SELECT thread_id, status, question FROM clarifications"):
            clarifications[r["thread_id"]] = r["status"]
            if r["status"] != "answered" and r["thread_id"] in threads:
                questions.append(r["question"])
        parked = any(
            r["thread_id"] in threads
            for r in conn.execute("SELECT thread_id FROM failures WHERE status = 'needs_review'")
        )
    finally:
        conn.close()
    counts = dict.fromkeys(KINDS, 0)
    counts["duplicate_order"] = max(0, len(order_ids) - messages)
    for reply in replies:
        for name, price in REPLY_LINE.findall(reply["text"]):
            if name not in prices or abs(prices[name] - float(price)) > 0.005:
                counts["price_not_catalog"] += 1
        for number in map(int, CONFIRMED.findall(reply["text"])):
            answered = sources.get(reply.get("in_reply_to"))
            if number not in order_ids or (answered is not None and number != answered):
                counts["false_confirmation"] += 1
    if order_ids:
        said = match_key(" ".join(r["text"] for r in replies))
        # A line is excused only while it is still open at the end: an unanswered question or a removed or
        # unresolved line names it; an answered question or a settled doubt no longer excuses it.
        doubtful = [
            d.get("text", "") for v in threads.values() for key in ("removed", "unresolved")
            for d in v.get(key) or []
        ]  # fmt: skip
        asked_about = f" {match_key(' '.join(questions + doubtful))} "
        for line in requested:
            stored = line.get("sku") is not None and line["sku"] in stored_skus
            asked = f" {match_key(line['text'])} " in asked_about or parked
            escalated = match_key(line["text"]) in said
            if not (stored or asked or escalated):
                counts["line_dropped"] += 1
    message_words = _words(message_text)
    for thread, values in threads.items():
        if values.get("order_id") is None:
            continue
        if clarifications.get(thread) == "pending":
            counts["open_doubt"] += 1
        for line in values.get("lines", []):
            text = line.get("source_text") or line.get("product") or ""
            if line.get("sku") is not None and not _words(text) <= message_words:
                counts["source_outside_message"] += 1
    return counts
