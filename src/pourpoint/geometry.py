"""Query-owned selected geometry. Decoding does not populate a dataset cache."""

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import shapely

from pourpoint._selection import compact_dictionaries


@dataclass(frozen=True)
class DecodedGeometry:
    """IDs and geometries in the same order; snap IDs are not unit IDs.

    ``attributes`` excludes WKB and retains snap unit references/weights/roles.
    Native GEOS allocation size is not provided by Shapely.
    """

    ids: npt.NDArray[np.int64]
    geometries: npt.NDArray[np.object_]
    attributes: pa.Table


@dataclass(frozen=True)
class GeometryBatch:
    """Compact Arrow selected rows with identity and WKB, in documented query order.

    ``decode`` returns independent decoded results, without caching both forms.
    Each explicit call decodes once and validates the selected geometry types.
    Attributes in the decoded result are compact copies and exclude WKB.
    """

    table: pa.Table
    kind: Literal["polygon", "snap"]
    artifact: str

    def decode(self) -> DecodedGeometry:
        if self.table["geometry"].null_count:
            raise ValueError(f"{self.artifact}: null geometry")
        try:
            geometries = shapely.from_wkb(self.table["geometry"].to_numpy(zero_copy_only=False), on_invalid="raise")
        except (ValueError, shapely.errors.GEOSException) as exc:
            raise ValueError(f"{self.artifact}: malformed selected geometry") from exc
        allowed = [3, 6] if self.kind == "polygon" else [0, 1]
        valid = np.isin(shapely.get_type_id(geometries), allowed) & ~shapely.is_empty(geometries)
        if not np.all(valid):
            row = int(np.flatnonzero(~valid)[0])
            raise ValueError(f"{self.artifact}: geometry type/empty at id {self.table['id'][row].as_py()}")
        coordinates = shapely.get_coordinates(geometries)
        if (
            np.any(~np.isfinite(coordinates))
            or np.any(np.abs(coordinates[:, 0]) > 180)
            or np.any(np.abs(coordinates[:, 1]) > 90)
        ):
            raise ValueError(f"{self.artifact}: geometry coordinates outside EPSG:4326")
        geometries.flags.writeable = False
        # Arrow slices can pin discarded rows. Take after dropping WKB so the
        # result owns only selected attributes, without copying geometry bytes.
        attributes = self.table.drop(["geometry"]).take(pa.array(np.arange(self.table.num_rows, dtype=np.int64)))
        attributes = compact_dictionaries(attributes)
        ids = attributes["id"].combine_chunks().to_numpy()
        ids.flags.writeable = False
        return DecodedGeometry(ids, geometries, attributes)

    @property
    def buffer_bytes(self) -> int:
        return self.table.get_total_buffer_size()
