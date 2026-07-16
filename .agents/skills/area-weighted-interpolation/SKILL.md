---
name: area-weighted-interpolation
description: Use when transferring a statistic between non-matching polygon layers on Wherobots — areal/area-weighted interpolation, spatial enrichment, apportionment (census population to zip codes), dasymetric weighting. Covers extensive-vs-intensive, dissolve-first, and CRS gotchas. Sedona, not PostGIS.
---

# Area-Weighted (Areal) Interpolation

Move a value measured on one polygon layer (**source**) onto a different, non-matching polygon layer
(**target**) by weighting each source polygon by how much of it falls inside the target. Classic case:
Census Block Group population → arbitrary neighborhoods/zip codes.

**Read [`references/interpolation-patterns.md`](references/interpolation-patterns.md)** for the
validated SQL templates (extensive + intensive), the dissolve/CRS/validity gotchas, and the
dasymetric refinement.

## Do these in order

1. **Classify each variable: extensive or intensive.** This *determines the formula* and is the most
   common source of silently-wrong results.
   - **Extensive** (counts/totals: population, households, $ total) → apportion by source-area
     fraction and **SUM**: `Σ value · area(∩)/area(source)`.
   - **Intensive** (rates/averages: median income, median age, density) → **area-weighted mean**:
     `Σ(value · area(∩)) / Σ(area(∩))`. Never divide an intensive variable by source area.
2. **Make the source one row per entity.** If the source has multipart or duplicate polygons per
   entity (Overture `divisions_division_area` does — parts for land/territorial water/islands),
   **dissolve first** with `ST_Union_Aggr(geometry) GROUP BY <entity_id>`. The denominator must be the
   entity's *total* area, or weights are wrong.
3. **Validate geometries.** Wrap source and target in `ST_MakeValid(...)` before `ST_Intersection` —
   invalid rings throw or mis-measure area.
4. **Decide CRS.** The ratio `area(∩)/area(source)` approximately cancels units, so it works on raw
   EPSG:4326 degrees² — but is only *exact* under an equal-area projection. For large/high-latitude
   polygons or any absolute area, `ST_Transform` to an equal-area CRS (e.g. `EPSG:5070`, local UTM) first.
5. **Prefilter, then intersect.** Keep an `ST_Intersects` join predicate + bbox/AOI filter so only
   overlapping pairs reach the expensive `ST_Intersection`.
6. **Refine if uniformity is too coarse.** Plain area weighting assumes people/values spread
   uniformly. Weight by an ancillary layer (Overture buildings footprint / land use) for dasymetric
   interpolation — see the reference.

## Wherobots specifics (not PostGIS)

- This is **Sedona SQL**: `ST_Union_Aggr` (dissolve), `ST_MakeValid`, `ST_Intersection`, `ST_Area`,
  `ST_Transform`. No `::geography` casts, no `<->` KNN operator, no `cross join lateral` needed —
  use a plain spatial `JOIN … ON ST_Intersects` + `GROUP BY target`.
- **Don't ingest shapefiles/GeoPackage** as a pipeline format. Land source layers as Havasu/Iceberg
  (GeoParquet for files); project only the join key + variables you need. Geometry column named
  `geometry`, stored EPSG:4326. Never `inferSchema`. See `wherobots-pipeline-designer`.
- Prefer Overture `divisions_division_area` for named target/source boundaries (with the
  `is_land` filter for land-based quantities) over importing your own — see `open-data-catalog`.

## Sibling skills

- `spatial-sql-patterns` — the join/CRS/aggregation primitives (Pattern 7 = `ST_Transform`).
- `open-data-catalog` — divisions schema, `is_land`, population join key, buildings/land-use layers.
- `wherobots-pipeline-designer` — productionize enrichment as reprocessable Bronze→Silver→Gold.
