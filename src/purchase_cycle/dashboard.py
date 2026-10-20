"""Static HTML dashboard over the per-suite evaluation summaries (standard library only)."""

import html
import json
from pathlib import Path

from purchase_cycle.config import DATA_DIR
from purchase_cycle.evaluation.harness import ALL_SUITES, SUMMARIES_DIR

DEFAULT_OUTPUT = DATA_DIR / "dashboard.html"

CSS = """
:root{--bg:#fafafa;--fg:#1c1c1e;--muted:#5c5c63;--card:#fff;--line:#d8d8de;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700;--bar:#3b6fd4;--track:#e6e8ee}
@media (prefers-color-scheme:dark){:root{--bg:#16171b;--fg:#ececf0;--muted:#a0a0aa;--card:#1f2026;--line:#363843;--ok:#4cc26b;--bad:#ff7b72;--warn:#e3b341;--bar:#7aa2f7;--track:#33353f}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 6px}h3{font-size:14px;margin:12px 0 4px;color:var(--muted)}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:12px 0}
table{border-collapse:collapse;width:100%;margin:4px 0}th,td{text-align:left;padding:3px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--muted);font-weight:600}.meta{color:var(--muted)}.passed,.PASS,.met{color:var(--ok)}.failed,.FAIL,.not_met{color:var(--bad)}.missing{color:var(--warn)}
.badge{font-weight:700;text-transform:uppercase;font-size:12px}svg .track{fill:var(--track)}svg .bar{fill:var(--bar)}svg .dot{fill:var(--fg)}
nav{display:flex;flex-wrap:wrap;gap:4px 12px}nav a{color:var(--bar)}
body,section,td,th,p,div{overflow-wrap:anywhere}.scroll{overflow-x:auto;max-width:100%}svg{max-width:100%}
"""


def _e(value) -> str:
    return html.escape("-" if value is None else str(value), quote=True)


def _x(value) -> float:
    return 2 + 1.16 * max(0.0, min(100.0, float(value)))


def _bar(metric: dict) -> str:
    """Inline SVG interval bar on a 0 to 100 scale; a dash when the metric has no percentage interval."""
    low, high, value = metric.get("ci_low"), metric.get("ci_high"), metric.get("value")
    if low is None or high is None or value is None or not str(metric.get("value_text", "")).endswith("%"):
        return "-"
    return (
        '<svg width="120" height="14" viewBox="0 0 120 14" role="img" '
        f'aria-label="interval {_e(low)} to {_e(high)}">'
        '<rect class="track" x="2" y="5" width="116" height="4"/>'
        f'<rect class="bar" x="{_x(low):.1f}" y="3" width="{max(_x(high) - _x(low), 1):.1f}" height="8"/>'
        f'<circle class="dot" cx="{_x(value):.1f}" cy="7" r="3"/></svg>'
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>'


def _metrics(summary: dict) -> str:
    rows = [
        [
            _e(m.get("name")),
            _e(m.get("value_text")),
            _e(m.get("ci_low")),
            _e(m.get("ci_high")),
            _bar(m),
            _e(m.get("n")),
            _e(m.get("unit")),
            _e(m.get("target")),
            f'<span class="{_e(m.get("target_met"))}">{_e(m.get("target_met"))}</span>',
            _e(m.get("note")),
        ]
        for m in summary.get("metrics") or []
    ]
    headers = ["metric", "value", "ci low", "ci high", "95% interval", "n", "unit", "target", "target met", "note"]
    return "<h3>Metrics</h3>" + _table(headers, rows) if rows else ""


def _gates(summary: dict) -> str:
    rows = [
        [
            _e(g.get("metric")),
            _e(g.get("threshold_text")),
            f'<span class="{_e(g.get("verdict"))}">{_e(g.get("verdict"))}</span>',
        ]
        for g in summary.get("gates") or []
    ]
    return "<h3>Gates</h3>" + _table(["metric", "threshold", "verdict"], rows) if rows else ""


def _critical(summary: dict) -> str:
    crit = summary.get("critical_errors")
    if not crit:
        return ""
    kinds = ", ".join(f"{_e(k)}={_e(v)}" for k, v in (crit.get("by_kind") or {}).items())
    return f"<h3>Critical errors</h3><p>total {_e(crit.get('total'))}; by kind: {kinds or '-'}</p>"


def _categories(summary: dict) -> str:
    out = []
    for block, entries in (summary.get("categories") or {}).items():
        rows = []
        for entry in entries:
            counts = ", ".join(f"{_e(k)}={_e(v)}" for k, v in (entry.get("counts") or {}).items())
            figures = "; ".join(
                f"{_e(f.get('name'))} {_e(f.get('value'))} [{_e(f.get('low'))}, {_e(f.get('high'))}] n={_e(f.get('n'))}"
                for f in entry.get("figures") or []
            )
            rows.append([_e(entry.get("label")), counts or "-", figures or "-"])
        out.append(f"<h3>Per {_e(block)}</h3>" + _table(["label", "counts", "figures"], rows))
    return "".join(out)


def _section(name: str, summary: dict | None) -> str:
    if summary is None:
        return (
            f'<section id="{_e(name)}"><h2>{_e(name)} <span class="badge missing">missing</span></h2>'
            '<p class="meta">No summary file: the suite was not run.</p></section>'
        )
    status = summary.get("status")
    counts = ", ".join(f"{k}={v}" for k, v in (summary.get("counts") or {}).items()) or None
    meta = [
        ("exit code", summary.get("exit_code")),
        ("mode", summary.get("mode")),
        ("split", summary.get("split")),
        ("model", summary.get("model")),
        ("cases", summary.get("case_count")),
        ("counts", counts),
        ("baseline date", summary.get("baseline_date")),
        ("regression", summary.get("regression")),
        ("failed cases", summary.get("failed_cases")),
    ]
    meta_html = "; ".join(f"{_e(k)}: {_e(v)}" for k, v in meta)
    error = f'<p class="failed">error: {_e(summary["error"])}</p>' if summary.get("error") else ""
    gate_failures = summary.get("gate_failures") or []
    failures = f'<p class="failed">gate failures: {_e(", ".join(map(str, gate_failures)))}</p>' if gate_failures else ""
    return (
        f'<section id="{_e(name)}"><h2>{_e(name)} <span class="badge {_e(status)}">{_e(status)}</span></h2>'
        f'<p class="meta">{meta_html}</p>{error}{failures}'
        f"{_metrics(summary)}{_gates(summary)}{_critical(summary)}{_categories(summary)}</section>"
    )


def _load(directory: Path, name: str) -> dict | None:
    path = directory / f"{name}.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"suite": name, "status": "failed", "error": f"unreadable summary: {exc}"}
    if not isinstance(loaded, dict):
        return {
            "suite": name,
            "status": "failed",
            "error": f"unreadable summary: expected an object, got {type(loaded).__name__}",
        }
    return loaded


def build_html(directory: Path = SUMMARIES_DIR) -> str:
    summaries = {name: _load(directory, name) for name in ALL_SUITES}
    nav = "".join(f'<a href="#{_e(n)}">{_e(n)}</a>' for n in ALL_SUITES)
    statuses = [(v.get("status") if v else "missing") for v in summaries.values()]
    overview = (
        f"{statuses.count('passed')} passed, {statuses.count('failed')} failed, "
        f"{statuses.count('missing')} missing of {len(ALL_SUITES)} suites"
    )
    sections = "".join(_section(n, summaries[n]) for n in ALL_SUITES)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Purchase cycle evaluation dashboard</title><style>{CSS}</style></head><body>"
        f'<h1>Purchase cycle evaluation dashboard</h1><p class="meta">{_e(overview)}</p>'
        f"<nav>{nav}</nav>{sections}</body></html>"
    )


def write_dashboard(directory: Path = SUMMARIES_DIR, output: Path = DEFAULT_OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(directory), encoding="utf-8")
    return output
