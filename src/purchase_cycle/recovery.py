"""Recovery from invalid model answers and exhausted retries (phase 05, D5).

An invalid answer is re-asked once with its validation error; a second invalid answer, or a transient
error on the last retry attempt, parks the thread in the `failures` table as needs_review. The error is
re-raised, so the checkpoint stays before the node and `failures resume` can run it again.
"""

import inspect
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from purchase_cycle import db, llm
from purchase_cycle.llm import InvalidExtraction, InvalidModelOutput

INVALID = (InvalidModelOutput, InvalidExtraction)


class NeedsReview(RuntimeError):
    """The model answered invalidly twice; the thread is parked for a person to review."""


def reask(ask, invalid=INVALID):
    """Run `ask(correction)` with no correction; on an invalid answer run it once more with the error text."""
    try:
        return ask(None)
    except invalid as error:
        first = str(error)
    try:
        return ask(first)
    except invalid as error:
        raise NeedsReview(f"Second invalid model answer: {error}") from error


def correction_kwargs(correction: str | None) -> dict:
    """Keyword arguments for `ModelClient.extract`: none on a first ask, so the call stays as in phases 02 to 04."""
    return {} if correction is None else {"correction": correction}


def parking(node, step: str, channel: str, db_path: Path | str, source, notice=None):
    """Wrap a model-calling node: NeedsReview, or a transient error on its last attempt, parks the thread.

    `source(state)` gives the source reference; `notice(state)` runs after the row is written, for the
    channels that can receive a reply. Every other error passes through unchanged.
    """
    params = inspect.signature(node).parameters

    def parked(state: dict, config: RunnableConfig, runtime: Runtime):
        extra = {name: value for name, value in (("config", config), ("runtime", runtime)) if name in params}
        try:
            return node(state, **extra)
        except Exception as error:
            last_attempt = runtime.execution_info.node_attempt >= llm.MODEL_RETRY.max_attempts
            if isinstance(error, NeedsReview):
                reason = str(error)
            elif llm.transient_model_error(error) and last_attempt:
                reason = f"{type(error).__name__}: {error}"
            else:
                raise
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id is not None:
                conn = db.connect(db_path)
                try:
                    db.park_failure(conn, thread_id, channel, source(state), step, reason)
                finally:
                    conn.close()
                if notice is not None:
                    notice(state)
            raise

    return parked
