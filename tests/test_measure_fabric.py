"""Measurement digest conventions use independently hand-worked bytes."""

import hashlib
import struct

import numpy as np

from scripts.measure_fabric import disabled_transport, ids_digest, source_grass


def test_ids_digest_is_sorted_little_endian_int64():
    assert ids_digest([9, 1, 4]) == hashlib.sha256(struct.pack("<qqq", 1, 4, 9)).hexdigest()


def test_normalized_direction_restores_source_grass_and_nodata():
    values = np.array([[1, 8, 0, 0]], dtype=np.int8)
    valid = np.array([[True, True, True, False]])
    assert source_grass(values, valid).tolist() == [[8, 1, 0, 255]]


def test_offline_measurement_blocks_transport_entrypoints():
    import obstore
    import pytest

    with disabled_transport(), pytest.raises(AssertionError, match="network transport disabled"):
        obstore.head(None, "manifest.json")
