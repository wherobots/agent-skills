---
name: wherobots-pipeline-designer
description: Use when designing, productionizing, or auditing geospatial pipelines on Wherobots as medallion (Bronze/Silver/Gold) architectures over Iceberg/Havasu — layer contracts, table naming, partitioning, quality gates, idempotency, job submission. For ETL, enrichment, and map-matching pipelines.
---

# Wherobots Pipeline Designer

Design geospatial pipelines as **medallion architectures**: `Raw → Bronze → Silver → Gold → Output`,
every intermediate stage an **Apache Iceberg (Havasu)** table. Havasu adds native GEO type + spatial
indexing on top of Iceberg — prefer it for every intermediate. This skill supplies the *judgment*:
what each layer must guarantee, how to name/partition tables, which spatial operation fits, and how
to keep the whole thing reprocessable and cheap.

**Read [`references/layer-contracts.md`](references/layer-contracts.md)** for the full contract each
layer must satisfy, the spatial-operation selection guide, output destinations, and the cross-layer
validation invariants. Consult it when designing (below) and when auditing a generated job.

## Design workflow

1. **Restate the grain and the AOI.** What is one row of the final output (an asset? a trip? a tile?
   a week×asset?) and what spatial/temporal extent is in scope. The grain drives every layer.
2. **Explore before designing.** Use the `open-data-catalog` skill (and, when MCP is available, the
   Wherobots MCP tools) to confirm the real schemas, join keys, and CRS of every source table
   before writing SQL against it. Never design against assumed columns.
3. **Draw the layer graph.** List the Bronze tables (one per source), the Silver enrichment/analytic
   tables (one per independent operation), and the Gold output(s). Confirm strict Raw→Bronze→
   Silver→Gold flow with **no circular or cross-Silver dependencies**.
4. **Pick the spatial operation per Silver table** from the selection guide in the contracts.
5. **Validate cheap, then productionize.** Prototype each stage's SQL on a **bounded, `LIMIT`ed**
   sample (see `spatial-sql-patterns`) before running full-table. Two failed attempts = stop and
   re-read the schema.
6. **Write the job to a file and submit it** with `wherobots job-runs create` (see `wherobots-ops`
   / `references/cli-recipes.md`). Anything destined for production lives in a repo file, not chat.

## Non-negotiable Wherobots rules (why they exist)

- **Every intermediate is an Iceberg/Havasu table; final outputs may be GeoParquet/JDBC.** Enables
  independent reprocessing, time travel, and schema evolution.
- **Geometry column is always named `geometry`** (raster: `raster`). Downstream code and the sibling
  skills assume it.
- **All timestamps UTC. All vector data EPSG:4326 (degrees)** unless a source proves otherwise.
  Distances in degrees are meaningless as meters — use spheroid/`ST_DistanceSphere` or `ST_Transform`.
- **Never `inferSchema`.** It silently corrupts timestamp columns (Spark). Enforce explicit types at
  Bronze — that is the layer's whole job.
- **Writable catalog is `org_catalog` (placeholder for your org's catalog); `wherobots_open_data`
  is read-only.** Never try to write into the open-data catalog.
- **Suspect Spark semantics before the spatial function** when SQL misbehaves: `SELECT` evaluates
  before `HAVING`; `COLLECT_LIST` does **not** preserve order (fatal for `ST_MakeLine` — order in a
  CTE first); `EXPLODE()` can't be nested in another expression.
- **`ST_KNN`/`ST_DWithin` reduce non-point geometries to their centroid.** Fine for point↔point;
  **wrong for snapping a point to a line/polygon** (a road's centroid is not near the vehicle). For
  map-matching use point-to-line distance (`ST_DistanceSphere(point, linestring)` / `ST_ClosestPoint`).
  When using `ST_KNN` on EPSG:4326 inputs, `use_sphere` **must be `TRUE`** or the radius is degrees.
- **Cost is dominated by unbounded spatial joins.** Prefilter both sides to the AOI (bbox scalars /
  H3 / partition), keep distance thresholds tight, and size the runtime to the workload — the CLI
  default `tiny` is too small for real spatial joins. Watch spend with `wherobots api usage costs`.

## Sibling skills

- `open-data-catalog` — source schemas, join keys, CRS, and quirks (`references/catalog-map.md`).
- `spatial-sql-patterns` — validated, bounded query templates for the Silver spatial operations.
- `wherobots-ops` — MCP discipline, the `wherobots` CLI (`references/cli-recipes.md`), runtimes, cost.
