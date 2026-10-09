"""Fault injection for the crash and resume checks (phase 05, D6, C10).

`PURCHASE_CYCLE_CRASH_AT` names one point; the process exits there at once with code 70, with no
cleanup, as a crash would. Nothing happens when the variable is unset.
"""

import os

CRASH_AT = "PURCHASE_CYCLE_CRASH_AT"
EXIT_CODE = 70
AFTER_CHANNEL_STEPS = "after_channel_steps"  # the channel steps are checkpointed, `store` has not run
AFTER_STORE_COMMIT = "after_store_commit"  # the order is committed, its checkpoint is not written
IN_REPLY = "in_reply"  # the reply is built (and written to the WhatsApp outbox), its checkpoint is not written


def crash_at(point: str) -> None:
    """Exit the process with EXIT_CODE when `point` is the configured crash point."""
    if os.environ.get(CRASH_AT) == point:
        os._exit(EXIT_CODE)
