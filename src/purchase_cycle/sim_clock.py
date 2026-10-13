"""Simulated clock: one current time in the `sim_clock` table, moved only forward by `advance`.

The quotes module reads time only from here, so a wait of days runs in seconds and replays the same way.
"""

import sqlite3
from datetime import datetime, timedelta

START = datetime(2026, 10, 12, 9, 0)
FORMAT = "%Y-%m-%d %H:%M:%S"


def now(conn: sqlite3.Connection) -> datetime:
    row = conn.execute("SELECT now FROM sim_clock WHERE id = 1").fetchone()
    return datetime.strptime(row[0], FORMAT)


def advance(conn: sqlite3.Connection, days: int = 0, hours: int = 0) -> datetime:
    """Move the clock forward by days and hours; a zero or negative step raises ValueError and changes nothing."""
    if days < 0 or hours < 0 or days == hours == 0:
        raise ValueError(f"the clock only moves forward: got {days} days and {hours} hours")
    moved = now(conn) + timedelta(days=days, hours=hours)
    with conn:
        conn.execute("UPDATE sim_clock SET now = ? WHERE id = 1", (moved.strftime(FORMAT),))
    return moved


def text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def add_days(moment: datetime, days: int, working: bool = False) -> datetime:
    """Add days to a moment; working days count Monday to Friday with no holidays, calendar days all seven."""
    if not working:
        return moment + timedelta(days=days)
    left = days
    while left > 0:
        moment += timedelta(days=1)
        if moment.weekday() < 5:
            left -= 1
    return moment
