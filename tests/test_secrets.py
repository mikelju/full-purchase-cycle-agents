"""C14: no secret is versioned and every variable is documented."""

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
