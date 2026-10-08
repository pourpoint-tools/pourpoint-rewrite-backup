"""Focused enclosure checks independent of remote selection work."""

from select_windows import pixel_window


def test_enclosure_preserves_projection_roundoff() -> None:
    assert pixel_window((-14999999.999, 8399939.999, -14999939.999, 8399969.999)) == [0, 1, 3, 2]


def test_aligned_bounds_do_not_add_pixels() -> None:
    assert pixel_window((-15000000.0, 8399940.0, -14999940.0, 8400000.0)) == [0, 0, 2, 2]
