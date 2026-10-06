"""Model client for order line extraction with live, record and replay modes.

The model answers through one forced tool call. Its arguments are validated
by Pydantic in every mode, so a recorded answer goes through the same checks
as a live one.
"""

import hashlib
import json
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from purchase_cycle.config import MAX_TOKENS, MODEL_ID, MODES, RECORDINGS_PATH

TOOL_NAME = "record_order_line"

INSTRUCTIONS = """You extract one order line from a customer message sent to a medical supplies distributor.

Return the catalog SKU of the product the customer asks for and the quantity in that product's sale unit.

Rules:
- Pick the SKU whose name and variant (size, volume, pack size, latex or latex-free, sterile or non-sterile) match the request.
- If the product the customer asks for is not in the catalog, return sku null and still return the quantity they asked for.
- Quantity is a positive whole number of sale units. Convert words and expressions such as "a dozen" or "half a dozen" to numbers.
- If the customer counts individual items and the sale unit is a pack or box, convert to the number of sale units.
- Ignore greetings, signatures and any text that is not part of the order.

Catalog (SKU | name | sale unit):
"""


class ExtractedLine(BaseModel):
    """Structured answer of the extraction node."""

    model_config = ConfigDict(extra="forbid")

    sku: str | None = Field(description="Catalog SKU, or null when the product is not in the catalog")
    quantity: int = Field(gt=0, strict=True, description="Quantity in catalog sale units")


class InvalidModelOutput(ValueError):
    """The model answer does not fit the schema; nothing downstream runs."""

    def __init__(self, error: ValidationError, label: str):
        self.fields = [".".join(str(p) for p in e["loc"]) or "<root>" for e in error.errors()]
        details = "; ".join(f"field '{'.'.join(str(p) for p in e['loc'])}': {e['msg']}" for e in error.errors())
        super().__init__(f"Model output for {label} rejected by schema: {details}")


class MissingRecording(LookupError):
    """Replay mode found no stored answer for this exact prompt."""


def tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": "Record the single order line found in the customer message.",
        "input_schema": ExtractedLine.model_json_schema(),
    }


def build_system_prompt(catalog: list) -> str:
    lines = [f"{r['sku']} | {r['name']} | {r['sale_unit']}" for r in catalog]
    return INSTRUCTIONS + "\n".join(lines)


def recording_key(system_prompt: str, sentence: str) -> str:
    payload = json.dumps(
        {"model": MODEL_ID, "system": system_prompt, "tool": tool_definition(), "sentence": sentence},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ModelClient:
    def __init__(self, mode: str, catalog: list, recordings_path: Path | None = None):
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'; use one of {', '.join(MODES)}")
        self.mode = mode
        self.system_prompt = build_system_prompt(catalog)
        self.recordings_path = Path(recordings_path or RECORDINGS_PATH)
        self.calls = 0
        self.usage: list[dict] = []
        self._lock = threading.Lock()
        self._recordings = self._load() if mode == "replay" else {}
        self._new: dict[str, dict] = {}
        self._llm = None

    def _load(self) -> dict[str, dict]:
        if not self.recordings_path.exists():
            return {}
        with self.recordings_path.open(encoding="utf-8") as fh:
            return {r["key"]: r for r in (json.loads(line) for line in fh if line.strip())}

    def _model(self):
        if self._llm is None:
            from langchain_anthropic import ChatAnthropic

            llm = ChatAnthropic(model=MODEL_ID, max_tokens=MAX_TOKENS, temperature=0, max_retries=6)
            self._llm = llm.bind_tools([tool_definition()], tool_choice={"type": "tool", "name": TOOL_NAME})
        return self._llm

    def _call_model(self, sentence: str) -> tuple[dict, dict]:
        from langchain_core.messages import HumanMessage, SystemMessage

        system = SystemMessage(
            content=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}]
        )
        message = self._model().invoke([system, HumanMessage(content=sentence)])
        args = message.tool_calls[0]["args"] if message.tool_calls else {}
        meta = message.usage_metadata or {}
        details = meta.get("input_token_details") or {}
        usage = {
            "input_tokens": meta.get("input_tokens", 0),
            "output_tokens": meta.get("output_tokens", 0),
            "cache_read": details.get("cache_read", 0),
            "cache_creation": details.get("cache_creation", 0),
        }
        return args, usage

    def extract(self, sentence: str, case_id: str | None = None) -> ExtractedLine:
        label = f"case {case_id}" if case_id else f"sentence {sentence!r}"
        key = recording_key(self.system_prompt, sentence)
        with self._lock:
            self.calls += 1
        if self.mode == "replay":
            record = self._recordings.get(key)
            if record is None:
                raise MissingRecording(
                    f"No recording for {label} in {self.recordings_path.name}. "
                    "Re-run the same command with --mode record and ANTHROPIC_API_KEY set to store it."
                )
            answer = record["answer"]
        else:
            answer, usage = self._call_model(sentence)
            with self._lock:
                self.usage.append(usage)
                if self.mode == "record":
                    self._new[key] = {"key": key, "case_id": case_id, "sentence": sentence, "answer": answer}
        try:
            return ExtractedLine.model_validate(answer)
        except ValidationError as error:
            raise InvalidModelOutput(error, label) from error

    def save_recordings(self) -> int:
        """Merge new answers into the recordings file, sorted for stable diffs."""
        if not self._new:
            return 0
        existing = self._load()
        existing.update(self._new)
        self.recordings_path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(existing.values(), key=lambda r: (r.get("case_id") or "", r["key"]))
        with self.recordings_path.open("w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        saved = len(self._new)
        self._new.clear()
        return saved
