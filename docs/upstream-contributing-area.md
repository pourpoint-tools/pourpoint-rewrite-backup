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

## Notation

Write $`x \to^{*} y`$ when a downstream drainage path connects location $`x`$
to location $`y`$. As in the point-delineation formulation, a path may have
zero length, so $`x \to^{*} x`$.

The watershed of outlet $`p`$ is

```math
W(p) = \lbrace x \in L \mid x \to^{*} p \rbrace,
```

where $`L`$ is the land domain. For a region $`R`$, including its boundary,
the upstream contributing area is

```math
U(R) = \lbrace x \in L \mid \exists p \in R : x \to^{*} p \rbrace
     = \bigcup_{p \in R} W(p).
```

Thus $`U(\lbrace p \rbrace) = W(p)`$: point watershed delineation is the single-point
case. Land within the region is included, and upstream contributions do
not stop at political boundaries. A river forming the region's boundary
counts as a receiving part of the region.

## Representation in Pourpoint

Regional delineation represents this area using whole hydrofabric
sub-basin polygons and their upstream connectivity, without raster
refinement. The hydrofabric's resolution limits the spatial detail of
that representation; it does not change the conceptual definition above.
