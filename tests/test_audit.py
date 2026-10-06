"""C10: the audit report passes with at most two wrong labels in the sample."""

import csv

import pytest

from purchase_cycle.evaluation import audit


def write_audit(path, wrong_sample: int, wrong_contrast: int = 0, pending: int = 0):
    rows = []
    for kind, size, wrong in (("sample", audit.AUDIT_SIZE, wrong_sample), ("contrast", 60, wrong_contrast)):
        for i in range(size):
            verdict = "wrong" if i < wrong else "" if i >= size - pending else "ok"
            rows.append(dict.fromkeys(audit.COLUMNS, "") | {"id": f"{kind}-{i}", "set": kind, "verdict": verdict})
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=audit.COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.mark.parametrize(("wrong", "code"), [(0, 0), (audit.MAX_WRONG, 0), (audit.MAX_WRONG + 1, 1)])
def test_report_passes_up_to_the_allowed_wrong_labels(tmp_path, capsys, wrong, code):
    assert audit.MAX_WRONG == 2
    assert audit.report_audit(write_audit(tmp_path / "audit.csv", wrong, wrong_contrast=5)) == code
    out = capsys.readouterr().out
    assert f"sample    n=150  wrong labels={wrong:<3}" in out
    assert "95% CI" in out


def test_report_refuses_rows_without_verdict(tmp_path, capsys):
    assert audit.report_audit(write_audit(tmp_path / "audit.csv", 0, pending=1)) == 2
    assert "still without a verdict" in capsys.readouterr().out
