from typing import Literal

import numpy as np
import pyarrow as pa
import pytest

from pourpoint.topology import Topology


def graph(ids: list[int], levels: list[int], edges: list[list[int]]) -> pa.Table:
    return pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "level": pa.array(levels, type=pa.int16()),
            "upstream_ids": pa.array(edges, type=pa.list_(pa.int64())),
        }
    )


def test_sparse_dag_selection_and_identity() -> None:
    table = graph([800, 3, 91, 42, 2**62], [0, 0, 0, 0, 1], [[91, 42], [], [3], [3], []])
    topology = Topology(table, topology="dag")
    result = topology.upstream(800)
    np.testing.assert_array_equal(np.sort(result.ids), [3, 42, 91, 800])
    np.testing.assert_array_equal(topology.ids[result.positions], result.ids)
    np.testing.assert_array_equal(topology.positions([2**62, 3, 3]), [4, 1, 1])
    np.testing.assert_array_equal(topology.upstream(2**62).ids, [2**62])
    result.require_source(topology)
    with pytest.raises(ValueError, match="source"):
        result.require_source(Topology(table, topology="dag"))
    with pytest.raises(KeyError, match="999"):
        topology.upstream(999)


@pytest.mark.parametrize(
    "ids,levels,edges,kind,message",
    [
        ([0, 3], [0, 0], [[], []], "dag", "positive"),
        ([3, 3], [0, 0], [[], []], "dag", "duplicate"),
        ([3, 4], [-1, 0], [[], []], "dag", "level"),
        ([3, 4], [0, 1], [[4], []], "dag", "level"),
    ],
)
def test_invalid_structure(
    ids: list[int], levels: list[int], edges: list[list[int]], kind: Literal["tree", "dag"], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Topology(graph(ids, levels, edges), topology=kind)


def test_invalid_reference() -> None:
    with pytest.raises(KeyError, match="99"):
        Topology(graph([3], [0], [[99]]), topology="dag")


def test_readonly_compact_results_and_repeat_queries() -> None:
    topology = Topology(graph([9, 2, 1], [0, 0, 0], [[2], [], []]), topology="tree")
    result = topology.upstream(2)
    for array in (topology.ids, topology.levels, topology.offsets, topology.edges, result.ids, result.positions):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array.setflags(write=True)
    assert result.buffer_bytes == 12
    assert topology.query_workspace_bytes == 15
    assert topology.buffer_bytes == 3 * (8 + 2 + 4 + 8) + 4 * 4 + 4
    np.testing.assert_array_equal(topology.upstream(9).ids, [9, 2])
    np.testing.assert_array_equal(topology.upstream(2).ids, [2])


@pytest.mark.parametrize("identities", [[3.5], [True], [True, 3], [[3]], [2**64]])
def test_lookup_does_not_coerce_invalid_ids(identities: list[int]) -> None:
    topology = Topology(graph([3], [0], [[]]), topology="tree")
    with pytest.raises((ValueError, TypeError), match="ID"):
        topology.positions(identities)


def test_chunks_slices_and_empty_graph() -> None:
    table = graph([99, 9, 2], [0, 0, 0], [[99], [2], []]).slice(1)
    topology = Topology(pa.concat_tables([table.slice(0, 1), table.slice(1)]), topology="tree")
    np.testing.assert_array_equal(topology.upstream(9).ids, [9, 2])
    empty = Topology(graph([], [], []), topology="tree")
    assert empty.positions([]).size == 0
    with pytest.raises(KeyError):
        empty.upstream(9)


@pytest.mark.parametrize(
    "column,values",
    [
        ("id", pa.array([None], type=pa.int64())),
        ("level", pa.array([0], type=pa.int64())),
        ("upstream_ids", pa.array([None], type=pa.list_(pa.int64()))),
        ("upstream_ids", pa.array([[None]], type=pa.list_(pa.int64()))),
    ],
)
def test_invalid_schema_and_nulls(column: str, values: pa.Array) -> None:
    table = graph([3], [0], [[]])
    table = table.set_column(table.column_names.index(column), column, values)
    with pytest.raises(ValueError, match=column):
        Topology(table, topology="tree")


def test_selection_preserves_requests_without_pinning_input() -> None:
    identities = np.array([9, 2], dtype=np.int64)
    table = pa.table(
        {
            "id": identities,
            "level": pa.array([0, 0], type=pa.int16()),
            "upstream_ids": pa.array([[2], []], type=pa.list_(pa.int64())),
        }
    )
    topology = Topology(table, topology="tree")
    identities[0] = 100
    selected = topology.selection([2, 9, 2])
    np.testing.assert_array_equal(selected.ids, [2, 9, 2])
    np.testing.assert_array_equal(selected.positions, [1, 0, 1])
    assert selected.source is topology.source
    with pytest.raises(KeyError, match="100"):
        topology.selection([2, 100])


def test_producer_validity_is_not_globally_audited() -> None:
    # Traversal terminates on repeated edges, but does not certify acyclicity.
    topology = Topology(graph([3, 4], [0, 0], [[4], [3]]), topology="dag")
    np.testing.assert_array_equal(topology.upstream(3).ids, [3, 4])
    declared_tree = Topology(graph([3, 4, 5], [0, 0, 0], [[], [3], [3]]), topology="tree")
    np.testing.assert_array_equal(declared_tree.upstream(5).ids, [5, 3])
