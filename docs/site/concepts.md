# Upstream contributing area

## From a watershed to a region

A **watershed** (also called a catchment or drainage basin) of a specified
outlet is the area of land from which water drains to that outlet.
Here, “watershed” means the contributing area, not the divide surrounding it.

Generalizing the destination from a point to a region:

> The **upstream contributing area of a region** is the area of land from
> which water drains to any part of that region.

Informally, it is the union of every watershed whose outlet lies in the
region, including its boundary. A region is any geometry-defined spatial
target, not necessarily a political or administrative area.

“Drains to” describes hydrological connectivity: water could follow a
downstream drainage path to the target. It does not require water to be
flowing at a particular moment, or all water to reach the target when flow
splits. Sharing a river system farther downstream is not sufficient.

## Point and regional targets

A point's watershed is the set of land locations with a downstream drainage path
to that point. A region's upstream contributing area is the union of those
watersheds for every point in the region, including its boundary. A path may have
zero length, so land within the region is included.

A river forming the region's boundary counts as a receiving part of the region.
Upstream contributions do not stop at political boundaries.

## Representation in Pourpoint

Regional delineation represents this area using whole hydrofabric
sub-basin polygons and their upstream connectivity, without raster
refinement. The hydrofabric's resolution limits the spatial detail of
that representation; it does not change the conceptual definition above.

## Whole units and raster refinement

Whole-subbasin selection follows supplied upstream connectivity at one chosen
catchment level (default 1). It approximates contributing area using complete
source polygons; it is not exact-point delineation. River levels are independent
of catchment levels. Missing connectivity is not evidence of a natural outlet.

Point refinement traces native D8 flow through the entire selected vector mask,
not independently within each sub-basin. It assumes that the vector union equals
the raster watershed at the terminal unit's outlet, and that the requested point's
watershed lies inside that union. Successful HFX packaging and successful execution
do not establish this scientific equivalence.

The initial outlet search uses a 3 × 3 native-cell window. This engineering default
is overridable, not a universal scientific optimum. A larger window can switch
branches near a confluence. Vector snapping and raster outlet adjustment are
separate operations; neither is implicit for a regional target. Direction pointers
are never warped. See [operation conventions](operations.md) for cell admission,
validity and output boundaries.

Source attributes remain source quantities after clipping. A contributing portion
does not acquire a recomputed source area or stream order. Raster accumulation
retains its declared units and source meaning; area values are not automatically
scientifically interchangeable.
