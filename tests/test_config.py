"""Environment overrides of the default data paths."""

import pytest

from purchase_cycle import config


@pytest.mark.parametrize(
    ("variable", "default", "name"),
    [
        ("PURCHASE_CYCLE_DB", config.default_db_path, "purchase_cycle.db"),
        ("PURCHASE_CYCLE_CHECKPOINTS", config.default_checkpoint_path, "checkpoints.db"),
    ],
)
def test_empty_variable_falls_back_to_the_default_path(monkeypatch, tmp_path, variable, default, name):
    monkeypatch.setenv(variable, "")
    assert default() == config.DATA_DIR / name
    monkeypatch.setenv(variable, str(tmp_path / "x.db"))
    assert default() == tmp_path / "x.db"
