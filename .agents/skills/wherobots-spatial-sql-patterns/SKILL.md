---
name: wherobots-spatial-sql-patterns
description: Use when writing, reviewing, or debugging WherobotsDB/Sedona Spatial SQL — point-in-polygon, distance (ST_DWithin), spatial joins, KNN, aggregation, CRS transforms, flattening nested Overture structs (EXPLODE). Covers Sedona-vs-PostGIS dialect gotchas and Spark semantics.
---

# Spatial SQL Patterns (WherobotsDB / Sedona)

WherobotsDB is Apache Sedona on Spark. Spatial functions are `ST_*` and mostly *look* like PostGIS
but signatures and behavior differ — **don't guess from PostGIS memory.** When unsure a function
exists or how it behaves, check the Wherobots function reference (MCP `search_documentation_tool`)
or validate on a bounded sample before trusting it.

## Use the validated pattern library

**Read [`references/query-patterns.md`](references/query-patterns.md)** for the 8 core patterns,
each with intent, a copy-pasteable template validated against live compute, CRS/units notes, and the
gotchas actually hit while validating:

1. Point-in-polygon (POI within an admin area via `divisions_division_area`)
2. Distance / proximity (`ST_DWithin`, degrees-vs-meters)
3. Bounded region window (bbox scalar prefilter + `ST_PolygonFromEnvelope`)
4. Spatial join (`ST_Contains`, bound both sides)
5. Nearest-neighbor / KNN join (`ST_KNN`)
6. Aggregation by admin region (`GROUP BY` division + count)
7. Geometry transform & metric area/distance (`ST_Transform` → projected CRS)
8. Nested struct / array handling (`EXPLODE` Overture taxonomy/transportation lists)

## Non-negotiable rules (why they exist)

- **Always prefilter on `bbox.xmin/xmax/ymin/ymax` scalars before any `ST_*` predicate.** Cheap
  range-pruning shrinks spatial-join input by orders of magnitude and keeps exploration affordable.
- **CRS is EPSG:4326 (degrees) across the catalog.** `ST_Distance`/`ST_Area` on raw geometry return
  degrees, not meters. Use `ST_DWithin(..., useSpheroid=true)` / `ST_DistanceSphere` for meters, or
  `ST_Transform` to a projected CRS (UTM zone per AOI) before metric measures. `ST_Point(x, y)` is (lon, lat).
- **A clean query returning 0 rows is a failure to investigate, not an answer.** Verify which admin
  `subtype` actually covers your AOI (e.g. SF is a `county`, not a `locality`) before trusting a filter.
- **`EXPLODE()` can't be nested in another expression** (Spark) — put it alone in a subquery, then aggregate.
- **Suspect Spark semantics before the spatial function** when a query fails oddly: `SELECT` before
  `HAVING`, `COLLECT_LIST` unordered, `inferSchema` corrupting timestamps.
- **Two failed attempts = stop.** Re-read the schema (`describe_table`), reconsider the table choice;
  never loop blindly on a failing query. Validate cheap (`LIMIT`, `COUNT`) before expensive full joins.

## Related skills

- `wherobots-open-data-catalog` — schemas, join keys, and quirks for `wherobots_open_data` (see its
  `references/catalog-map.md`). These templates target those tables.
- `wherobots-explore` — the MCP tool sequence and exploration discipline for running these queries.
- `wherobots-develop` — runtime sizing and cost hygiene once a pattern goes into a job.
