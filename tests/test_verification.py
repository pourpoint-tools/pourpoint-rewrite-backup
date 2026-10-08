from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pytest


def verification() -> ModuleType:
    spec = spec_from_file_location("verify_dataset", Path(__file__).parents[1] / "scripts/verify_dataset.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_independent_frontier_on_shuffled_dag(hfx: Path) -> None:
    oracle = verification().upstream_oracle
    assert oracle(hfx / "graph.parquet", 40).tolist() == [10, 20, 30, 40]
    with pytest.raises(KeyError, match="99"):
        oracle(hfx / "graph.parquet", 99)


def test_integrated_measurement(hfx: Path, tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    record = verification().run(hfx, cache_dir=tmp_path / "cache", output_dir=output, unit_id=40, window_degrees=1.0)
    assert record["status"] == "passed"
    assert record["topology_check"] == {"selected_count": 4, "exact_membership": True}
    phases = {phase["name"]: phase for phase in record["phases"]}
    assert phases["open"]["dataset_buffers"]["topology"] == 0
    assert phases["topology:first"]["selected_count"] == 4
    assert phases["records:batched"]["selected_count"] == 3
    assert phases["terminal_geometry:first"]["selected_count"] == 1
    assert {phase["snap_set"] for phase in record["phases"] if "snap_set" in phase} == {"coarse", "fine"}
    assert all(p["started_utc"] <= p["finished_utc"] and p["seconds"] >= 0 for p in record["phases"])
    assert all(p["memory_at_ready"]["process_lifetime_peak_rss_bytes"] > 0 for p in record["phases"])
    with pa.ipc.open_file(output / "selected.arrow") as reader:
        assert set(reader.read_all()["id"].to_pylist()) == {10, 20, 30, 40}
    assert (output / "measurement.json").is_file()
