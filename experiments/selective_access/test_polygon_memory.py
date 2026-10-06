from pathlib import Path

import pyarrow as pa
import pytest
from pyarrow import ipc
from shapely.geometry import box

from polygon_memory import compare


def test_exact_witness_equality_and_mismatches(tmp_path: Path) -> None:
    geometry = box(0, 0, 1, 1).wkb
    first = pa.table(
        {
            "id": [901, 3],
            "geometry": [geometry, geometry],
            "decoded_wkb": [geometry, geometry],
        }
    )
    paths = [tmp_path / "first.arrow", tmp_path / "second.arrow"]

    def write(path: Path, table: pa.Table) -> None:
        with ipc.new_file(path, table.schema) as writer:
            writer.write_table(table)

    write(paths[0], first)
    write(paths[1], first.take([1, 0]))
    assert compare(paths)["status"] == "passed"
    for column, values in [
        ("id", [902, 3]),
        ("geometry", [geometry, box(1, 1, 2, 2).wkb]),
        ("decoded_wkb", [geometry, box(1, 1, 2, 2).wkb]),
    ]:
        changed = first.set_column(
            first.schema.get_field_index(column), column, pa.array(values)
        )
        write(paths[1], changed)
        with pytest.raises(AssertionError, match="differ"):
            compare(paths)
