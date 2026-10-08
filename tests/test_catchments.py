import hashlib

import pyarrow as pa
import pytest
from test_metadata import manifest_data

from pourpoint.catchments import CatchmentReader
from pourpoint.metadata import Fingerprint, Manifest


class Source:
    def __init__(self, files: dict[str, bytes], directory: dict[str, object]) -> None:
        self.files = files
        self.directory = directory
        self.opened: list[tuple[str, bool]] = []

    def open_artifact(self, path: str, fingerprint: Fingerprint, *, bounded: bool = True) -> pa.BufferReader:
        self.opened.append((path, bounded))
        return pa.BufferReader(self.files[path])

    def read_json(self, path: str, fingerprint: Fingerprint | None = None) -> dict[str, object]:
        assert fingerprint is not None
        return self.directory


def ipc(schema: pa.Schema, batches: list[pa.RecordBatch]) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, schema) as writer:
        for batch in batches:
            writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


def fixture_reader(
    *,
    wrong_row: bool = False,
    dictionary_labels: bool = False,
    oversized: bool = False,
    directory_rows: int = 3,
    digest: bytes | None = None,
    row_address: int | None = None,
    block_address: int | None = None,
) -> tuple[CatchmentReader, Source]:
    schema = pa.schema(
        [
            pa.field("id", pa.int64(), False),
            pa.field("level", pa.int16(), False),
            pa.field("parent_id", pa.int64()),
            pa.field("area_km2", pa.float32(), False),
            pa.field("up_area_km2", pa.float32()),
            pa.field("outlet_lon", pa.float64(), False),
            pa.field("outlet_lat", pa.float64(), False),
            pa.field(
                "bbox", pa.struct([pa.field(k, pa.float32(), False) for k in ("xmin", "ymin", "xmax", "ymax")]), False
            ),
            pa.field("geometry", pa.binary(), False),
        ],
        metadata={b"source": b"preserved"},
    )
    rows = [
        {
            "id": i,
            "level": 0,
            "parent_id": None,
            "area_km2": 1,
            "up_area_km2": None,
            "outlet_lon": 0,
            "outlet_lat": 0,
            "bbox": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
            "geometry": b"wkb" + str(i).encode(),
        }
        for i in (90, 2, 700)
    ]
    if oversized:
        rows[2]["geometry"] = b"x" * (8388608 + 1)
    table = pa.Table.from_pylist(rows, schema=schema)
    if dictionary_labels:
        labels = pa.DictionaryArray.from_arrays(
            pa.array([0, 1, None], type=pa.int8()), pa.array(["unused" * 10000, "selected"])
        )
        table = table.append_column(pa.field("label", labels.type, metadata={b"meaning": b"source"}), labels)
        schema = table.schema
    catchments = ipc(schema, [table.slice(0, 2).to_batches()[0], table.slice(2).to_batches()[0]])
    data = manifest_data()
    data["artifacts"]["catchments.arrow"]["sha256"] = hashlib.sha256(catchments).hexdigest()
    lookup_schema = pa.schema(
        [pa.field("id", pa.int64(), False), pa.field("block", pa.int32(), False), pa.field("row", pa.int32(), False)],
        metadata={
            b"hfx:format_version": b"0.4.0",
            b"hfx:catchments_sha256": digest or hashlib.sha256(catchments).hexdigest().encode(),
        },
    )
    lookup = pa.RecordBatch.from_arrays(
        [
            pa.array([2, 90, 700]),
            pa.array([0 if block_address is None else block_address, 0, 1], type=pa.int32()),
            pa.array([(0 if wrong_row else 1) if row_address is None else row_address, 0, 0], type=pa.int32()),
        ],
        schema=lookup_schema,
    )
    source = Source(
        {"catchments.arrow": catchments, "lookup.arrow": ipc(lookup_schema, [lookup])},
        {"version": 1, "batches": [{"batch": 0, "min_id": 2, "max_id": 700, "rows": directory_rows}]},
    )
    return CatchmentReader(source, Manifest.parse(data)), source


def test_selective_order_duplicates_nulls_and_metadata() -> None:
    reader, source = fixture_reader()
    assert reader.last_access.lookup_batches == ()
    assert source.opened == [("catchments.arrow", True), ("lookup.arrow", True)]
    result = reader.read([700, 2, 700])
    assert result["id"].to_pylist() == [700, 2, 700]
    assert result["parent_id"].null_count == 3
    assert result.schema.metadata == {b"source": b"preserved"}
    assert reader.last_access.lookup_batches == (0,)
    assert reader.last_access.catchment_batches == (0, 1)
    assert reader.read([]).schema == result.schema


def test_exact_lookup_and_address_checks() -> None:
    reader, _ = fixture_reader()
    with pytest.raises(KeyError):
        reader.read([50])
    reader, _ = fixture_reader(wrong_row=True)
    with pytest.raises(ValueError, match="ID"):
        reader.read([2])


def test_oversized_singleton_and_compact_selected_buffers() -> None:
    reader, _ = fixture_reader(oversized=True)
    large = reader.read([700])
    assert len(large["geometry"][0].as_py()) == 8388609
    small = reader.read([2])
    assert small.get_total_buffer_size() < 1024


def test_metadata_only_startup_and_grouped_batch_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    actual_open = pa.ipc.open_file
    accesses: list[tuple[list[str], int]] = []

    class Reader:
        def __init__(self, stream: pa.BufferReader) -> None:
            self.delegate = actual_open(stream)
            self.schema = self.delegate.schema
            self.num_record_batches = self.delegate.num_record_batches

        def get_batch(self, batch: int) -> pa.RecordBatch:
            accesses.append((self.schema.names, batch))
            return self.delegate.get_batch(batch)

    monkeypatch.setattr(pa.ipc, "open_file", Reader)
    reader, _ = fixture_reader()
    assert accesses == []
    reader.read([2, 90, 2])
    assert len(accesses) == 2  # One lookup batch and one catchment batch.


@pytest.mark.parametrize("kwargs", [{"directory_rows": 2}, {"digest": b"0" * 64}])
def test_invalid_directory_or_binding(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        fixture_reader(**kwargs)


@pytest.mark.parametrize(
    "kwargs", [{"row_address": -1}, {"row_address": 2}, {"block_address": -1}, {"block_address": 2}]
)
def test_address_bounds(kwargs: dict[str, int]) -> None:
    reader, _ = fixture_reader(**kwargs)
    with pytest.raises(ValueError, match="bounds"):
        reader.read([2])


@pytest.mark.parametrize("unit_id", [0, -1, True, 2**63, 2.0])
def test_invalid_requested_ids(unit_id: object) -> None:
    reader, _ = fixture_reader()
    with pytest.raises(ValueError, match="requested ID"):
        reader.read([unit_id])


def test_lookup_batch_boundary_sparse_int64_ids() -> None:
    base, source = fixture_reader()
    count = 65537
    offset = 2**53
    ids = pa.array([offset + 2 * i for i in range(count)], type=pa.int64())
    table = base.read([2]).take(pa.array([0] * count, type=pa.int32()))
    table = table.set_column(0, table.schema.field("id"), ids)
    catchments = ipc(table.schema, table.to_batches(max_chunksize=4096))
    digest = hashlib.sha256(catchments).hexdigest()
    schema = pa.ipc.open_file(pa.BufferReader(source.files["lookup.arrow"])).schema.with_metadata(
        {b"hfx:format_version": b"0.4.0", b"hfx:catchments_sha256": digest.encode()}
    )
    lookup = pa.Table.from_arrays(
        [
            ids,
            pa.array([i // 4096 for i in range(count)], type=pa.int32()),
            pa.array([i % 4096 for i in range(count)], type=pa.int32()),
        ],
        schema=schema,
    )
    source.files = {"catchments.arrow": catchments, "lookup.arrow": ipc(schema, lookup.to_batches(max_chunksize=65536))}
    source.directory = {
        "version": 1,
        "batches": [
            {"batch": 0, "min_id": offset, "max_id": offset + 131070, "rows": 65536},
            {"batch": 1, "min_id": offset + 131072, "max_id": offset + 131072, "rows": 1},
        ],
    }
    data = manifest_data()
    data["unit_count"] = count
    data["artifacts"]["catchments.arrow"]["sha256"] = digest
    reader = CatchmentReader(source, Manifest.parse(data))
    requested = [offset + 131072, offset, offset + 131070, offset + 131072]
    assert reader.read(requested)["id"].to_pylist() == requested
    assert reader.last_access.lookup_batches == (0, 1)
    assert reader.last_access.catchment_batches == (0, 15, 16)
    with pytest.raises(KeyError):
        reader.read([offset + 131071])


def test_selected_dictionary_values_are_compact() -> None:
    reader, _ = fixture_reader(dictionary_labels=True)
    result = reader.read([2, 700, 2])
    assert result["label"].to_pylist() == ["selected", None, "selected"]
    assert result.schema.equals(reader.schema, check_metadata=True)
    assert result.get_total_buffer_size() < 1000
