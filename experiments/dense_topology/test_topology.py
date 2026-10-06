from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from topology import Graph, convert, traverse


def table(ids: list[int], levels: list[int], edges: list[list[int]]) -> pa.Table:
    return pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "level": pa.array(levels, type=pa.int16()),
            "upstream_ids": pa.array(edges, type=pa.list_(pa.int64())),
        }
    )


@pytest.mark.parametrize("dense", [False, True])
def test_reconvergent_sparse_shuffled_identity(dense: bool) -> None:
    graph = Graph(
        table([800, 3, 91, 42, 999], [0, 0, 0, 0, 1], [[91, 42], [], [3], [3], []])
    )
    edges = convert(graph) if dense else None
    np.testing.assert_array_equal(
        np.sort(traverse(graph, 800, edges)), [3, 42, 91, 800]
    )
    np.testing.assert_array_equal(traverse(graph, 999, edges), [999])
    with pytest.raises(KeyError, match="1000"):
        traverse(graph, 1000, edges)


@pytest.mark.parametrize("dense", [False, True])
@pytest.mark.parametrize(
    "edges,levels,error",
    [([[99], []], [0, 0], KeyError), ([[2], []], [0, 1], ValueError)],
)
def test_invalid_references(
    dense: bool, edges: list[list[int]], levels: list[int], error: type[Exception]
) -> None:
    graph = Graph(table([1, 2], levels, edges))
    with pytest.raises(error):
        positions = convert(graph) if dense else None
        traverse(graph, 1, positions)


@pytest.mark.parametrize("ids", [[0, 3], [-1, 3], [3, 3]])
def test_bad_identity(ids: list[int]) -> None:
    with pytest.raises(ValueError):
        Graph(table(ids, [0, 0], [[], []]))


def test_null_rejected() -> None:
    with pytest.raises(ValueError):
        Graph(
            pa.table(
                {
                    "id": pa.array([1], type=pa.int64()),
                    "level": pa.array([None], type=pa.int16()),
                    "upstream_ids": pa.array([[]], type=pa.list_(pa.int64())),
                }
            )
        )


def test_multiple_chunks() -> None:
    graph = Graph(pa.concat_tables([table([9], [0], [[2]]), table([2], [0], [[]])]))
    np.testing.assert_array_equal(np.sort(traverse(graph, 9, convert(graph))), [2, 9])


def test_oracle_reconvergence() -> None:
    from measure import oracle

    graph = Graph(table([800, 3, 91, 42], [0, 0, 0, 0], [[91, 42], [], [3], [3]]))
    np.testing.assert_array_equal(oracle(graph, 800), [3, 42, 91, 800])


def test_measurement_record(tmp_path: "Path", monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import sys

    import pyarrow.parquet as pq

    from measure import main

    pq.write_table(
        table([800, 3, 91, 42], [0, 0, 0, 0], [[91, 42], [], [3], [3]]),
        tmp_path / "graph.parquet",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"format_version": "0.3.0", "unit_count": 4})
    )
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "objects": [
                    {"relative_path": "graph.parquet"},
                    {"relative_path": "manifest.json"},
                ]
            }
        )
    )
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "measure.py",
            "--data",
            str(tmp_path),
            "--receipt",
            str(receipt),
            "--commit",
            "synthetic-test",
            "--mode",
            "dense",
            "--ids",
            "800",
            "--repeats",
            "1",
            "--concurrent-activity",
            "pytest",
            "--output",
            str(output),
        ],
    )
    main()
    record = json.loads(output.read_text())
    assert record["status"] == "ok"
    assert record["rows"] == record["edges"] == 4
    assert record["queries"][0]["selected_count"] == 4
    assert len(record["queries"][0]["timings"]) == 2
