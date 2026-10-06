from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from read_graph import Graph, read_graph


def table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array([90, 20, 70, 11, 300], type=pa.int64()),
            "level": pa.array([2, 2, 2, 2, 1], type=pa.int16()),
            "upstream_ids": pa.array([[20, 70], [11], [11], [], []], type=pa.list_(pa.int64())),
        }
    )


def test_non_row_ids_reconvergence_and_missing() -> None:
    graph = Graph(table())
    assert graph.upstream(90).tolist() == [11, 20, 70, 90]
    assert graph.upstream(300).tolist() == [300]
    with pytest.raises(KeyError):
        graph.upstream(1)


@pytest.mark.parametrize("mode", ["serial", "prefetch"])
def test_reader_preserves_projection(tmp_path: Path, mode: str) -> None:
    source = tmp_path / "graph.parquet"
    pq.write_table(
        table().append_column("irrelevant", pa.array([None] * 5)), source, row_group_size=2
    )
    graph, _, _ = read_graph(str(source), mode, None)
    assert graph.table.equals(table())
    assert graph.upstream(90).tolist() == [11, 20, 70, 90]
    assert graph.buffers()["lookup_owned_bytes"] == 80


@pytest.mark.parametrize("ids", [[90, 20, 70, 11, 90], [0, 20, 70, 11, 300]])
def test_invalid_id(ids: list[int]) -> None:
    with pytest.raises(ValueError):
        Graph(table().set_column(0, "id", pa.array(ids, type=pa.int64())))


def test_invalid_edge() -> None:
    for edges in ([[300], [11], [11], [], []], [[999], [11], [11], [], []]):
        graph = Graph(
            table().set_column(2, "upstream_ids", pa.array(edges, type=pa.list_(pa.int64())))
        )
        with pytest.raises((ValueError, KeyError)):
            graph.upstream(90)


def test_slices_count_retained_backing() -> None:
    graph = Graph(table().slice(1, 3))
    buffers = graph.buffers()
    assert buffers["graph_retained_backing_bytes"] >= buffers["graph_referenced_bytes"]
    np.testing.assert_array_equal(graph.ids, [20, 70, 11])
