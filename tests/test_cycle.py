"""C2 (phase 12): the purchase_cycle graph and its stages."""

from purchase_cycle import cycle
from purchase_cycle.graph import sqlite_checkpointer


def _folders(tmp_path):
    return cycle.Folders(
        tmp_path / "orders",
        tmp_path / "replies",
        tmp_path / "documents",
        tmp_path / "signed",
        tmp_path / "outbox",
        tmp_path / "supplier_outbox",
        tmp_path / "notes",
        tmp_path / "invoices",
    )


def test_graph_has_the_stages_in_order_with_the_sqlite_checkpointer(tmp_path):
    checkpointer = sqlite_checkpointer(tmp_path / "cp.sqlite")
    graph = cycle.build_cycle_graph(tmp_path / "b.db", checkpointer, _folders(tmp_path), lambda task, catalog: None)
    nodes = [n for n in graph.get_graph().nodes if not n.startswith("__")]
    assert nodes == ["orders", "quotes", "reconcile", "receipts", "sales"]
    assert graph.checkpointer is checkpointer
    checkpointer.conn.close()


def test_a_pass_over_empty_folders_counts_nothing_and_the_next_pass_is_a_new_run(tmp_path):
    class NoCalls:
        def extract(self, *args, **kwargs):
            raise AssertionError("no model call expected")

    def client(task, catalog):
        return NoCalls()

    args = (tmp_path / "b.db", tmp_path / "cp.sqlite", _folders(tmp_path), client)
    first, second = cycle.run_cycle(*args), cycle.run_cycle(*args)
    assert (first["thread_id"], second["thread_id"]) == ("cycle-run-0001", "cycle-run-0002")
    assert list(first["counts"]) == list(cycle.STAGES)
