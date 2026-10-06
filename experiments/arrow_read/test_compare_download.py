import pyarrow as pa
import pytest

from compare_download import assert_same_graph
from read_graph import Graph
from test_read_graph import table


def test_full_graph_identity() -> None:
    source = table()
    assert_same_graph(Graph(source), Graph(source))
    changed_level = source.set_column(1, "level", pa.array([3, 2, 2, 2, 1], type=pa.int16()))
    # Same flattened edges, different list boundaries.
    changed_offsets = source.set_column(
        2, "upstream_ids", pa.array([[20], [70, 11], [11], [], []], type=pa.list_(pa.int64()))
    )
    for changed in (changed_level, changed_offsets):
        with pytest.raises(AssertionError, match="graph differs"):
            assert_same_graph(Graph(source), Graph(changed))
