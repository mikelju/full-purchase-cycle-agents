"""Phase 13 C1 and C2: the CI workflow runs the check on Linux, uses no secret and can incur no API cost."""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "check.yml"


def violations(text: str) -> list[str]:
    found = []
    if not re.search(r"^on:\n\s+pull_request:\n\s+push:\n\s+branches: \[main\]$", text, re.M):
        found.append("triggers are not exactly pull_request and push to main")
    if not re.search(r"^permissions:\n\s+contents: read\n", text, re.M):
        found.append("permissions is not contents: read")
    if "secrets." in text:
        found.append("references a secret")
    if re.search(r"ANTHROPIC|LANGSMITH|LANGCHAIN", text, re.I):
        found.append("sets an Anthropic or LangSmith variable")
    if "--mode live" in text or "--mode record" in text:
        found.append("runs a live or record mode")
    uses = re.findall(r"uses:\s*(\S+)", text)
    if not uses or not all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses):
        found.append("an action is not pinned to a full commit SHA")
    if "runs-on: ubuntu-latest" not in text:
        found.append("not ubuntu-latest")
    if "node-version: 22" not in text or "uv python install 3.13" not in text:
        found.append("not Node 22 and Python 3.13")
    if "fetch-depth: 0" not in text:
        found.append("checkout is shallow, so compare-baselines cannot read revision 636711e")
    if "run: npm ci --ignore-scripts" not in text:
        found.append("does not install with npm ci --ignore-scripts")
    if "run: npm run check" not in text:
        found.append("does not run npm run check")
    return found


def test_workflow_meets_every_rule():
    assert violations(WORKFLOW.read_text(encoding="utf-8")) == []


def test_each_rule_fails_on_a_workflow_that_breaks_it():
    text = WORKFLOW.read_text(encoding="utf-8")
    sha = re.search(r"uses: (\S+@)[0-9a-f]{40}", text)
    broken = {
        "triggers": text.replace("branches: [main]", "branches: [main, other]"),
        "permissions": text.replace("contents: read", "contents: write"),
        "secret": text + "      - run: echo ${{ secrets.TOKEN }}\n",
        "variable": text.replace("node-version: 22", "node-version: 22\n        env:\n          ANTHROPIC_API_KEY: x"),
        "live": text + "      - run: uv run purchase-cycle cycle-demo --mode live\n",
        "pin": text.replace(sha.group(0), "uses: " + sha.group(1) + "v4"),
        "runner": text.replace("ubuntu-latest", "windows-latest"),
        "versions": text.replace("node-version: 22", "node-version: 20"),
        "depth": text.replace("fetch-depth: 0", "fetch-depth: 1"),
        "check": text.replace("npm run check", "npm test"),
        "install": text.replace("npm ci --ignore-scripts", "npm ci"),
    }
    for rule, content in broken.items():
        assert violations(content), f"a workflow that breaks the {rule} rule passed"
