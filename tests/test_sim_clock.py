"""C2, C3: the simulated clock table, `now`, `advance` and the `clock` commands."""

import subprocess
import sys
from datetime import datetime

import pytest

from purchase_cycle import cli, db, sim_clock

START = datetime(2026, 10, 12, 9, 0)


def _clock_row(path):
    conn = db.connect(path)
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM sim_clock")]
    finally:
        conn.close()


def test_seed_starts_the_clock_once_and_a_reseed_keeps_it(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    db.seed(conn)
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(sim_clock)")]
    assert columns == ["id", "now"]
    assert sim_clock.now(conn) == START
    sim_clock.advance(conn, days=2)
    db.seed(conn)
    assert sim_clock.now(conn) == datetime(2026, 10, 14, 9, 0)
    assert conn.execute("SELECT COUNT(*) FROM sim_clock").fetchone()[0] == 1


def test_advance_moves_the_clock_by_days_and_hours(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    db.seed(conn)
    assert sim_clock.advance(conn, days=3) == datetime(2026, 10, 15, 9, 0)
    assert sim_clock.advance(conn, hours=5) == datetime(2026, 10, 15, 14, 0)
    assert sim_clock.now(conn) == datetime(2026, 10, 15, 14, 0)


@pytest.mark.parametrize("step", [{"days": 0}, {"hours": 0}, {"days": -1}, {"hours": -3}, {}])
def test_a_zero_or_negative_step_fails_and_changes_nothing(tmp_path, step):
    conn = db.connect(tmp_path / "b.db")
    db.seed(conn)
    with pytest.raises(ValueError, match="forward"):
        sim_clock.advance(conn, **step)
    assert sim_clock.now(conn) == START


def test_clock_show_prints_the_simulated_time(tmp_path, capsys):
    path = tmp_path / "b.db"
    assert cli.main(["--db", str(path), "clock", "show"]) == 0
    assert "2026-10-12 09:00" in capsys.readouterr().out


def test_clock_advance_prints_the_new_time(tmp_path, capsys):
    path = tmp_path / "b.db"
    assert cli.main(["--db", str(path), "clock", "advance", "--days", "2"]) == 0
    assert "2026-10-14 09:00" in capsys.readouterr().out
    assert cli.main(["--db", str(path), "clock", "advance", "--hours", "6"]) == 0
    assert "2026-10-14 15:00" in capsys.readouterr().out


@pytest.mark.parametrize("option", [["--days", "0"], ["--hours", "-2"]])
def test_clock_advance_with_a_zero_or_negative_step_fails_with_a_message(tmp_path, capsys, option):
    path = tmp_path / "b.db"
    cli.main(["--db", str(path), "clock", "show"])
    before = _clock_row(path)
    capsys.readouterr()
    assert cli.main(["--db", str(path), "clock", "advance", *option]) == 1
    assert "forward" in capsys.readouterr().err
    assert _clock_row(path) == before


def test_clock_advance_needs_exactly_one_step_option(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--db", str(tmp_path / "b.db"), "clock", "advance"])
    with pytest.raises(SystemExit):
        cli.main(["--db", str(tmp_path / "b.db"), "clock", "advance", "--days", "1", "--hours", "1"])


def test_the_advanced_time_persists_across_processes(tmp_path):
    path = tmp_path / "b.db"
    base = [sys.executable, "-m", "purchase_cycle.cli", "--db", str(path), "clock"]
    advanced = subprocess.run([*base, "advance", "--days", "1"], capture_output=True, text=True, timeout=120)
    assert advanced.returncode == 0, advanced.stderr
    shown = subprocess.run([*base, "show"], capture_output=True, text=True, timeout=120)
    assert shown.returncode == 0, shown.stderr
    assert "2026-10-13 09:00" in shown.stdout
