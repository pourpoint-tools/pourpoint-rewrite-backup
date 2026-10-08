from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pourpoint import Dataset


def test_integrated_selection_attributes_and_polygons(hfx: Path, tmp_path: Path) -> None:
    with Dataset.open(hfx, cache_dir=tmp_path / "cache") as data:
        selected = data.upstream(40)
        assert set(selected.ids) == {10, 20, 30, 40}
        attributes = data.attributes([40, 9000000000, 10, 40], ["id", "level", "parent_id", "up_area_km2", "source_id"])
        assert attributes.to_pydict() == {
            "id": [40, 9000000000, 10, 40],
            "level": [1, 0, 1, 1],
            "parent_id": [9000000000, None, 9000000000, 9000000000],
            "up_area_km2": [None, None, 1, None],
            "source_id": ["forty", None, "ten", "forty"],
        }
        polygons = data.polygons([40, 10, 40])
        assert polygons.table["id"].to_pylist() == [40, 10, 40]
        assert polygons.table["geometry"].to_pylist() == [box(3, 0, 4, 1).wkb, box(0, 0, 1, 1).wkb, box(3, 0, 4, 1).wkb]
        decoded = polygons.decode()
        assert decoded.ids.tolist() == [40, 10, 40]
        assert decoded.geometries[0].equals_exact(box(3, 0, 4, 1), 0)
        assert data.polygons([]).table.num_rows == 0
        assert data.levels == (0, 1)
        with pytest.raises(KeyError):
            data.polygons([40, 99])
    with pytest.raises(RuntimeError, match="closed"):
        data.polygons([10])
    # Selected data are owned by the query, not the closed reader.
    np.testing.assert_array_equal(selected.ids, [40, 20, 30, 10])


def test_selections_cannot_cross_dataset_lifetimes(hfx: Path, tmp_path: Path) -> None:
    with Dataset.open(hfx, cache_dir=tmp_path / "cache") as one, Dataset.open(hfx, cache_dir=tmp_path / "cache") as two:
        selection = one.upstream(40)
        with pytest.raises(ValueError, match="source|dataset"):
            two.attributes(selection)


def test_open_defers_graph_and_preserves_full_selected_schema(hfx: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    original = pq.ParquetFile.read
    reads: list[list[str] | None] = []

    def observed(reader: pq.ParquetFile, columns: list[str] | None = None, **kwargs: object) -> pa.Table:
        reads.append(columns)
        return original(reader, columns=columns, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "read", observed)
    with Dataset.open(hfx) as data:
        assert reads == []
        assert data.buffer_bytes == {"topology": 0, "query_workspace": 0}
        records = data.records([40, 10, 40])
        assert reads == [["id", "level", "upstream_ids"]]
        assert records.table["id"].to_pylist() == [40, 10, 40]
        assert records.table.schema.metadata is not None
        assert data.records([10]).table["source_id"].to_pylist() == ["ten"]
        assert len(reads) == 1
        assert data.last_access["catchment_batches"] == (1,)
        assert data.buffer_bytes["topology"] > 0
        assert data.buffer_bytes["query_workspace"] == 25


def test_geometry_is_decoded_only_explicitly(hfx: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shapely

    original = shapely.from_wkb
    counts: list[int] = []

    def observed(values: object, **kwargs: object) -> object:
        counts.append(len(values))
        return original(values, **kwargs)

    monkeypatch.setattr(shapely, "from_wkb", observed)
    with Dataset.open(hfx) as data:
        data.attributes([10])
        polygons = data.polygons([10, 20])
        assert counts == []
        polygons.decode()
        assert counts == [2]
        result = data.snap_candidates("fine", (0.4, 0.4, 0.7, 0.6))
        assert result.ids.tolist() == [101, 102]
        # Two candidates in group0 and the unknown-bounds candidate in group1.
        assert sum(counts[1:]) == 3
        assert "geometry" not in result.attributes.column_names
