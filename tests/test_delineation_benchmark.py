from scripts.measure_delineation import measure


def test_open_is_in_cold_total_but_validation_is_not() -> None:
    ticks = iter([10.0, 12.0, 17.0, 20.0, 23.0])
    calls: list[str] = []

    def opening() -> str:
        calls.append("open")
        return "fabric"

    def query(fabric: str) -> str:
        assert fabric == "fabric"
        calls.append("query")
        return "complete"

    def validate(result: str) -> None:
        assert result == "complete"
        calls.append("validate")

    result = measure(opening, query, validate, repeats=1, clock=lambda: next(ticks))
    assert result == {"opening_seconds": 2.0, "cold_total_seconds": 7.0, "repeated_seconds": [3.0]}
    assert calls == ["open", "query", "validate", "query", "validate"]


def test_comparison_uses_source_keys_not_dataset_ids() -> None:
    from scripts.compare_delineation import compare

    python = {
        "target": {"source_id": "20"},
        "validation": [
            {
                "ids": [100, 101],
                "source_keys": [
                    ["grit:1.0:EU:reach_catchments:reach_catchments__1", "10"],
                    ["grit:1.0:EU:reach_catchments:reach_catchments__1", "20"],
                ],
            }
        ],
    }
    rust = {"validation": [{"ids": [9, 8], "terminal_unit_id": 8}]}
    rows = [{"id": 9, "level": 1, "source_id": "reach:10"}, {"id": 8, "level": 1, "source_id": "reach:20"}]
    result = compare(python, rust, rows)
    assert result["membership_equal"] and result["outlet_source_equal"]
    rows[0]["source_id"] = "reach:30"
    result = compare(python, rust, rows)
    assert not result["membership_equal"]
    assert result["python_only"] == ["10"]
    assert result["rust_only"] == ["30"]


def test_comparison_rejects_missing_source_rows() -> None:
    import pytest

    from scripts.compare_delineation import compare

    with pytest.raises(AssertionError, match="missing or extra"):
        compare({"validation": [{}]}, {"validation": [{"ids": [1]}]}, [])


def test_search_bounds_cover_snap_circle() -> None:
    from pyproj import Geod

    from scripts.measure_delineation import search_bounds

    geod = Geod(ellps="WGS84")
    for lon, lat in [(129.68, -14.5), (-62.68, -7.62), (1.83, 41.61)]:
        west, south, east, north = search_bounds(lon, lat)
        for bearing in range(360):
            x, y, _ = geod.fwd(lon, lat, bearing, 1000)
            assert west <= x <= east and south <= y <= north
