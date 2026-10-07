"""C14 (phases 01 to 03): no secret is versioned and every variable is documented."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}|lsv2_[A-Za-z0-9_]{10,}|x-api-key|authorization:\s*bearer", re.I
)
EXAMPLE = ROOT / (".env" + ".example")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_env_file_is_not_tracked():
    tracked = _tracked()
    assert ".env" not in tracked
    assert EXAMPLE.name in tracked


def test_example_lists_every_variable_without_values():
    lines = [line for line in EXAMPLE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    names = {}
    for line in lines:
        name, _, value = line.partition("=")
        names[name] = value
    assert all(value == "" for value in names.values())
    # Variables read by our code, plus the ones the Anthropic and LangSmith clients read.
    used = set()
    for path in (ROOT / "src").rglob("*.py"):
        used |= set(re.findall(r"environ(?:\.get)?[\[(]\s*\"([A-Z_]+)\"", path.read_text(encoding="utf-8")))
    used -= {"LANGCHAIN_TRACING_V2"}  # legacy name, only cleared
    expected = {"ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_PROJECT"} | used
    assert expected <= set(names)


def test_tracked_files_and_recordings_hold_no_keys():
    for name in _tracked():
        path = ROOT / name
        if path.suffix in {".png", ".jpg", ".ico"} or not path.is_file() or path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not SECRET_PATTERNS.search(text), f"possible secret in {name}"


def test_phase_02_data_files_are_tracked_and_scanned():
    tracked = set(_tracked())
    for name in (
        "evals/recordings/web_form_matching.jsonl",
        "evals/datasets/web_form_matching/dataset.jsonl",
        "evals/baselines/web_form_matching.json",
        "examples/web_form_submission.json",
    ):
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"


def test_phase_03_files_are_tracked_and_their_decoded_text_holds_no_keys():
    from purchase_cycle.email_order import model_text, parse_email

    tracked = set(_tracked())
    dataset = "evals/datasets/email_order_extraction"
    fixed = [
        "evals/recordings/email_intake.jsonl",
        "evals/recordings/email_order_extraction.jsonl",
        f"{dataset}/plan.jsonl",
        f"{dataset}/dataset.jsonl",
        f"{dataset}/second_pass_review.jsonl",
        "evals/baselines/email_order_extraction.json",
        "evals/audit/email_order_extraction-audit-v1.0.csv",
    ]
    texts = sorted(str(p.relative_to(ROOT).as_posix()) for p in (ROOT / dataset / "texts").iterdir())
    emails = sorted(
        str(p.relative_to(ROOT).as_posix())
        for folder in (ROOT / dataset / "emails", ROOT / "examples" / "email_orders")
        for p in folder.glob("*.eml")
    )
    assert len(emails) == 316 and texts
    for name in fixed + texts + emails:
        assert name in tracked, f"{name} is not versioned, so the secret scan skips it"
        assert not SECRET_PATTERNS.search((ROOT / name).read_text(encoding="utf-8", errors="ignore")), name
    attachments = 0
    for name in emails:
        email = parse_email((ROOT / name).read_bytes())
        attachments += sum(
            a["name"].endswith((".pdf", ".xlsx")) and bool(a["text"].strip()) for a in email["attachments"]
        )
        decoded = "\n".join([email["sender"], model_text(email)])
        assert not SECRET_PATTERNS.search(decoded), f"possible secret in the decoded text of {name}"
    assert attachments >= 104, "the PDF and Excel attachments were not decoded"
