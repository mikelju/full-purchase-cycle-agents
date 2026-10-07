"""Model client for catalog tasks with live, record and replay modes.

Each task (phase 01 order line extraction, phase 02 web form matching, phase 03
email intake and email order extraction) has its own prompt, tool, answer schema and recordings file. The model answers through
one forced tool call. Its arguments are validated
by Pydantic in every mode, so a recorded answer goes through the same checks
as a live one.
"""

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from purchase_cycle.config import (
    EMAIL_EXTRACTION_MAX_TOKENS,
    EMAIL_EXTRACTION_RECORDINGS_PATH,
    EMAIL_INTAKE_RECORDINGS_PATH,
    MATCHING_RECORDINGS_PATH,
    MAX_TOKENS,
    MODEL_ID,
    MODES,
    RECORDINGS_PATH,
)

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


MATCHING_INSTRUCTIONS = """You match one product description typed by a customer in the order form of a medical supplies distributor to the catalog.

Return the catalog SKU of the product the customer means, or null when the catalog does not carry it.

Rules:
- The description may be a short or informal name, contain typos or omit words of the catalog name.
- Pick the SKU whose name and variant (size, volume, pack size, latex or latex-free, sterile or non-sterile) match the description.
- When the description names a variant the catalog does not carry, or a product the catalog does not carry, return null.
- Never guess a different size, volume or variant.

Catalog (SKU | name | sale unit):
"""


class MatchedProduct(BaseModel):
    """Structured answer of the web form matching node."""

    model_config = ConfigDict(extra="forbid")

    sku: str | None = Field(description="Catalog SKU, or null when the product is not in the catalog")


EMAIL_INTAKE_INSTRUCTIONS = """You read one email received by the orders mailbox of a medical supplies distributor and decide whether it is an order.

An email is an order when the customer asks to buy or to be sent one or more products, in the body or in an attached order form or spreadsheet.
It is not an order when it only asks a question, makes a complaint, asks for a quote or a catalog, confirms or chases a previous delivery, or is a newsletter or advertising.
It is still an order when some or all requested products are not in the catalog: whether a product is in the catalog never changes the decision.
It is still an order when the customer asks to check availability and then to process the request as an order, or adds a question or remark next to the order.

Return is_order and a short reason of one sentence naming what the email asks for.
The catalog below only helps you recognise product names.

Catalog (SKU | name | sale unit):
"""


class IntakeDecision(BaseModel):
    """Structured answer of the email intake node."""

    model_config = ConfigDict(extra="forbid")

    is_order: bool = Field(strict=True, description="True when the email places an order")
    reason: str = Field(min_length=1, max_length=500, description="One sentence naming what the email asks for")


EMAIL_EXTRACTION_INSTRUCTIONS = """You extract the order lines of one customer email sent to a medical supplies distributor.

The email text has a subject, a body and, for each attachment, a line "Attachment: <file name>" followed by its text.
Spreadsheet rows are tab-separated cells.

Return one line per product the customer orders, with:
- source: "body" when the line is in the body, or the attachment file name exactly as written after "Attachment: ".
- source_text: the text of that line exactly as the customer wrote it.
- sku: the catalog SKU whose name and variant (size, volume, pack size, material such as latex, silicone-coated latex (a latex product) or 100% silicone, sterile or non-sterile) match the request, or null when the catalog does not carry that product or variant. Never guess a different variant.
- quantity: a positive whole number of catalog sale units.

Quantity rules:
- Write number words as digits; "a dozen" is 12, "half a dozen" is 6, "four dozen X" is 48 X and "3 dozen boxes" is 36 boxes.
- A number followed by a container word (box, pack, roll, refill, canister, bottle, tube) or written as "N x <product>" already counts sale units: use it unchanged, even when it is large. "17 x FFP3 respirator mask" is 17 and "80 x wipes refill" is 80.
- In a table, the number in the quantity column is counted in the unit written in the unit column. When that unit is the catalog sale unit, such as "box of 100" or "box of 20", use the number unchanged: "150 | box of 100" is 150 and "120 | box of 20" is 120.
- Divide by the pack size only when the customer counts individual items of a product sold in packs or boxes: "80 foam dressings" sold in boxes of 10 is 8, "10 masks" sold in boxes of 10 is 1, "100 pairs" of gloves sold in boxes of 50 pairs is 2, and a table row whose unit column says "units" or "pairs" for a product sold in packs counts items, so "10 | units" of a pack of 10 is 1.

Rules:
- Return every ordered product, including the ones the catalog does not carry (sku null); never drop a requested line.
- Ignore greetings, signatures, questions and any text that is not an ordered product.
- When the same order appears in the body and in an attachment, take it from the attachment and do not repeat it.

Catalog (SKU | name | sale unit):
"""


class EmailLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, description='"body" or the attachment file name')
    source_text: str = Field(min_length=1, description="The line as the customer wrote it")
    sku: str | None = Field(description="Catalog SKU, or null when the product is not in the catalog")
    quantity: int = Field(gt=0, strict=True, description="Quantity in catalog sale units")


class EmailLines(BaseModel):
    """Structured answer of the email extraction node."""

    model_config = ConfigDict(extra="forbid")

    lines: list[EmailLine] = Field(description="Every ordered product, in the order the customer wrote them")


@dataclass(frozen=True)
class Task:
    """What the model is asked to do and where its recorded answers live."""

    name: str
    instructions: str
    tool_name: str
    tool_description: str
    answer: type[BaseModel]
    recordings_path: Path
    max_tokens: int = MAX_TOKENS


EXTRACTION = Task(
    "order_line_extraction",
    INSTRUCTIONS,
    TOOL_NAME,
    "Record the single order line found in the customer message.",
    ExtractedLine,
    RECORDINGS_PATH,
)
MATCHING = Task(
    "web_form_matching",
    MATCHING_INSTRUCTIONS,
    "record_catalog_match",
    "Record the catalog product that matches the description, or null.",
    MatchedProduct,
    MATCHING_RECORDINGS_PATH,
)
EMAIL_INTAKE = Task(
    "email_intake",
    EMAIL_INTAKE_INSTRUCTIONS,
    "record_email_intake",
    "Record whether the email is an order and why.",
    IntakeDecision,
    EMAIL_INTAKE_RECORDINGS_PATH,
)
EMAIL_EXTRACTION = Task(
    "email_order_extraction",
    EMAIL_EXTRACTION_INSTRUCTIONS,
    "record_email_order_lines",
    "Record every order line found in the email.",
    EmailLines,
    EMAIL_EXTRACTION_RECORDINGS_PATH,
    EMAIL_EXTRACTION_MAX_TOKENS,
)


class InvalidModelOutput(ValueError):
    """The model answer does not fit the schema; nothing downstream runs."""

    def __init__(self, error: ValidationError, label: str):
        self.fields = [".".join(str(p) for p in e["loc"]) or "<root>" for e in error.errors()]
        details = "; ".join(f"field '{'.'.join(str(p) for p in e['loc'])}': {e['msg']}" for e in error.errors())
        super().__init__(f"Model output for {label} rejected by schema: {details}")


class MissingRecording(LookupError):
    """Replay mode found no stored answer for this exact prompt."""


def tool_definition(task: Task = EXTRACTION) -> dict:
    return {
        "name": task.tool_name,
        "description": task.tool_description,
        "input_schema": task.answer.model_json_schema(),
    }


def build_system_prompt(catalog: list, task: Task = EXTRACTION) -> str:
    lines = [f"{r['sku']} | {r['name']} | {r['sale_unit']}" for r in catalog]
    return task.instructions + "\n".join(lines)


def recording_key(system_prompt: str, sentence: str, task: Task = EXTRACTION) -> str:
    payload = json.dumps(
        {"model": MODEL_ID, "system": system_prompt, "tool": tool_definition(task), "sentence": sentence},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ModelClient:
    def __init__(self, mode: str, catalog: list, recordings_path: Path | None = None, task: Task = EXTRACTION):
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'; use one of {', '.join(MODES)}")
        self.mode = mode
        self.task = task
        self.system_prompt = build_system_prompt(catalog, task)
        self.recordings_path = Path(recordings_path or task.recordings_path)
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

            llm = ChatAnthropic(model=MODEL_ID, max_tokens=self.task.max_tokens, temperature=0, max_retries=6)
            self._llm = llm.bind_tools(
                [tool_definition(self.task)], tool_choice={"type": "tool", "name": self.task.tool_name}
            )
        return self._llm

    def _call_model(self, sentence: str) -> tuple[dict, dict]:
        from langchain_core.messages import HumanMessage, SystemMessage

        system = SystemMessage(
            content=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}]
        )
        message = self._model().invoke([system, HumanMessage(content=sentence)])
        args = message.tool_calls[0]["args"] if message.tool_calls else {}
        # Raw API usage: input_tokens excludes the cached part, unlike LangChain's usage_metadata.
        raw = message.response_metadata.get("usage") or {}
        usage = {
            "input_tokens": raw.get("input_tokens", 0),
            "output_tokens": raw.get("output_tokens", 0),
            "cache_read": raw.get("cache_read_input_tokens", 0),
            "cache_creation": raw.get("cache_creation_input_tokens", 0),
        }
        return args, usage

    def extract(self, sentence: str, case_id: str | None = None) -> BaseModel:
        """Ask the model about one text and return its answer validated by the task schema."""
        label = f"case {case_id}" if case_id else f"sentence {sentence!r}"
        key = recording_key(self.system_prompt, sentence, self.task)
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
            return self.task.answer.model_validate(answer)
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
