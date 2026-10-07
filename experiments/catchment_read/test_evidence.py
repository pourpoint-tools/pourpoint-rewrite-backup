from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from evidence import source_reference, upstream


def test_reference_uses_source_values_and_graph_ids(tmp_path: Path) -> None:
    graph = pa.table({"id": [9, 2, 70, 8], "upstream_ids": [[70, 2], [8], [], []]})
    assert upstream(graph, 9) == [2, 8, 9, 70]
    data = pa.table(
        {
            "id": [70, 8, 9, 2],
            "parent_id": [None, 9, None, 9],
            "geometry": [b"seventy", b"eight", b"nine", b"two"],
        }
    )
    pq.write_table(data, tmp_path / "catchments.parquet", row_group_size=2)
    actual = source_reference(tmp_path, [8, 9])
    assert actual.equals(data.take([1, 2]))
