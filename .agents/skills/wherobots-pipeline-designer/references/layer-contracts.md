# Medallion Architecture — Layer Contracts (Wherobots)

What each layer in a Wherobots medallion pipeline **must guarantee**. Use these contracts when
designing a pipeline and when auditing generated jobs. Adapted for WherobotsDB (Apache Sedona on
Spark) and this repo's ground truths.

> **Grain first.** These contracts assume you have declared the pipeline's *grain* — what one row of
> the final output represents (an asset, a trip, a tile, an asset×week). The common case is
> **one row per entity** (buildings, parcels, assets); the "per-entity" language below is that case.
> For non-entity pipelines (trip construction, tiling, temporal rollups) read "entity" as "the
> declared grain key." Two worked Gold shapes are given at the end: entity risk-scoring and
> trip aggregation.

---

## General Principles

1. **Each layer is independently materializable.** Reprocessing Silver does not require reprocessing Bronze.
2. **Each table is independently materializable.** Reprocessing one Silver enrichment does not touch others.
3. **No circular dependencies.** Data flows strictly: `Raw → Bronze → Silver → Gold → Output`.
4. **Every intermediate table is an Apache Iceberg / Havasu table** (final outputs like GeoParquet/JDBC excepted). Havasu = Iceberg + native GEO type + spatial indexing; prefer it for every stage.
5. **Geometry column is always named `geometry`** (raster: `raster`).
6. **All timestamps are UTC.** All vector data is EPSG:4326 unless a source proves otherwise.
7. **Writable catalog is `org_catalog`** (placeholder for your org's managed catalog). `wherobots_open_data` is **read-only** — never write to it.
8. **Idempotent writes.** Use `CREATE OR REPLACE TABLE …` (SQL) / `df.writeTo(TABLE).createOrReplace()` (DataFrame). Re-running a stage reproduces its output exactly.

---

## Bronze Layer — Cataloged Raw

### Purpose
Convert raw source data (S3 files, external catalogs) into queryable, schema-enforced Iceberg tables.
Close to raw — no analytics or joins here.

### Guarantees

| Guarantee | What it means |
|-----------|---------------|
| **Schema enforcement** | Every column has an explicit type — **never `inferSchema`** (Spark's inference silently corrupts timestamps). Geometry columns are proper GEOMETRY type, not STRING. |
| **Geometry validity** | Geometry is constructed via `ST_Point`, `ST_GeomFromWKT`, `ST_GeomFromGeoJSON`, or raster loaders. No raw WKT/WKB left as STRING. Never ingest shapefiles as a pipeline format. |
| **CRS tagging** | All vector data is EPSG:4326. For raster, Sedona auto-detects CRS from GeoTIFF metadata — do **not** blanket-apply `RS_SetSRID(raster, 4326)`. Record source CRS in a `crs` STRING column per raster table (e.g. `EPSG:32611`). |
| **Deduplication** | If the source has a natural key, dedupe and document the key. |
| **Provenance** | Every row traceable to its source; include an `ingested_at` TIMESTAMP. |
| **Idempotency** | Re-running Bronze reproduces the table (`CREATE OR REPLACE` / `createOrReplace`). |

### What Bronze does NOT do
No spatial joins/analytics · no AOI filtering (keep the full/broad source extent) · no normalization or scoring · no cross-source joins.

### Table naming
```
org_catalog.<data_domain>.<source_dataset>
```
Examples: `org_catalog.fleet.gps_pings_raw`, `org_catalog.noaa.storm_warnings`, `org_catalog.regrid.parcels`.

### Required metadata columns
| Column | Type | Description |
|--------|------|-------------|
| `geometry` | GEOMETRY (or `raster` RASTER) | Spatial column |
| `ingested_at` | TIMESTAMP | Ingest time (UTC) |

### Partitioning
Use Iceberg hidden partitioning aligned to how you reprocess. Daily ingest → `PARTITIONED BY (days(ingested_at))` or an explicit `ingest_date`. Do not over-partition tiny tables.

### Skeleton
```sql
CREATE TABLE IF NOT EXISTS org_catalog.fleet.gps_pings_raw (
  vehicle_id string, ts timestamp, lon double, lat double,
  geometry geometry, ingested_at timestamp, ingest_date date
) USING iceberg PARTITIONED BY (ingest_date);

INSERT INTO org_catalog.fleet.gps_pings_raw
SELECT vehicle_id, ts, lon, lat, ST_Point(lon, lat) AS geometry,
       current_timestamp() AS ingested_at, date('{{ ds }}') AS ingest_date
FROM ...;                       -- explicit-schema read of s3://…/{{ ds }}/*.parquet (NO inferSchema)
```

---

## Silver Layer — Spatial Analytics

### Purpose
Spatial joins, zonal statistics, KNN proximity, buffer/aggregation — turn Bronze into per-grain
enrichment metrics. The heavy compute layer.

### Guarantees

| Guarantee | What it means |
|-----------|---------------|
| **Declared grain** | Each Silver table holds exactly one row per grain key (e.g. per asset). Aggregation is complete — no accidental fan-out. **Exception:** temporal tables (below). |
| **AOI-filtered** | Only features within the declared AOI polygon appear. Prefer joining to Overture `divisions_division_area` for named boundaries over hard-coded WKT/bbox. |
| **Temporally bounded** | Event/observation data filtered to declared windows, recorded in the table. Use **per-source** windows — sources rarely share an observation period. |
| **NULL semantics** | Missing data is NULL, never 0/-1. NULL = "no data available", not "no risk / zero value". |
| **Coverage flags** | The unified table carries boolean `has_<source>_data` flags to distinguish "no data" from a genuine zero. |
| **Independent enrichments** | Each source has its own Silver table; reprocessing one does not touch others. |
| **Unified conflation** | The final 1:1 Silver table (`asset_enriched` / `<grain>_enriched`) LEFT JOINs all 1:1 enrichments onto the grain base. Temporal (multi-row) tables stay separate. |

### Temporal (multi-row) Silver tables
Sources that yield multiple rows per grain key (weekly observations, per-ping matches) must **not**
be joined into the 1:1 enriched table — that fans it out and breaks downstream 1:1 assumptions.
Write them to their own Silver table (one row per key×bucket), derive the bucket in a single-pass
`GROUP BY` (e.g. `DATE_TRUNC('WEEK', acq_date)`), and let Gold aggregate + LEFT JOIN them.

### What Silver does NOT do
No [0,1] normalization · no business/industry weighting · no tier classification · no derived business metrics. All of that is Gold.

### Table naming
```
org_catalog.silver.<grain>_<source>_<operation>
org_catalog.silver.<grain>_enriched          # the final unified 1:1 table
```
Examples: `org_catalog.silver.asset_wildfire_exposure` (1:1), `org_catalog.silver.asset_flood_exposure` (temporal), `org_catalog.silver.trip_pings_matched` (temporal: 1 row per ping).

### Spatial Operation Selection Guide

| Source type | Question | Operation | Sedona function |
|-------------|----------|-----------|-----------------|
| Raster (single-band) | value at this location? | Zonal stats (3-arg) | `RS_ZonalStats(raster, geometry, 'mean')` |
| Raster (multi-band) | band N's value? | Zonal stats (5-arg) | `RS_ZonalStats(raster, geometry, band_idx, 'max', true)` |
| Raster | intersects a hazard zone? | Raster–vector filter | `RS_Intersects(raster, geometry)` |
| Vector (events, points) | how many events near here? | KNN join | `ST_KNN(a.geom, b.geom, k, true, radius)` |
| Vector (events, points) | nearest event? | KNN, k=1 | `ST_KNN(a.geom, b.geom, 1, true, radius)` |
| Vector (polygons) | falls within a zone? | Spatial join | `ST_Intersects(a.geometry, b.geometry)` |
| Vector (polygons) | how much overlap? | Intersection area | `ST_Area(ST_Intersection(a.geometry, b.geometry))` |
| Vector (lines) | **snap a point to nearest road/line?** | point-to-line distance | `ST_DistanceSphere(pt, line)` + `ST_DWithin`; position via `ST_LineLocatePoint` |

> **Validate the exact signature before shipping.** Sedona signatures differ from PostGIS and evolve
> across versions. When MCP is available, confirm with `search_documentation_tool`; otherwise mark
> the SQL unverified. These notes were last cross-checked against the Wherobots function reference on
> 2026-07-10 but are version-sensitive.

**`RS_ZonalStats` notes:**
- 3-arg `RS_ZonalStats(raster, geometry, 'mean')` — single-band only. Do **not** add a 4th arg; Sedona then reads the stat name as a band index.
- 5-arg `RS_ZonalStats(raster, geometry, 1, 'max', true)` — 1-based band index, stat, allTouched/excludeNoData. Use for multi-band or sub-pixel features.
- Sub-pixel features (building footprints < raster pixel) → prefer `allTouched=true` over `ST_Buffer` (buffering forces a full geometry shuffle; allTouched is free).

**Raster/vector CRS (Sedona 0.12+):** `RS_ZonalStats` and `RS_Intersects` auto-reproject the raster
to the geometry's CRS — no manual `ST_Transform` when both sides have a known CRS. Only call
`RS_SetSRID` when a raster genuinely lacks embedded CRS metadata, never as a routine step.

**`ST_KNN` critical notes (this repo's findings):**
- Signature `ST_KNN(a.geom, b.geom, k, use_sphere, radius)` — used as a **join predicate**, `a` = query side, `b` = object side. `radius` (5th) is optional.
- On EPSG:4326 inputs `use_sphere` **must be `TRUE`**, else `radius` is interpreted in **degrees** — silently wrong (a 25 km radius becomes ~0.0002°, matching almost nothing; or a raw `25000` matches the globe).
- **`ST_KNN`/`ST_DWithin` reduce non-point geometries to their centroid.** Perfect for point↔point events; **wrong for snapping a point to a line/polygon** — a long road's centroid is nowhere near the vehicle. For map-matching use the point-to-line row of the guide, not KNN.

### Required columns in the enriched (1:1) table
| Column | Type | Source |
|--------|------|--------|
| `<grain>_id` | STRING | Grain identifier from the base table |
| `geometry` | GEOMETRY | Grain geometry from the base table |
| `<entity_attributes>` | varies | Key base attributes (e.g. height, num_floors, class) |
| `<metrics>` | DOUBLE/INT | Raw metric columns from each 1:1 enrichment |
| `has_<source>_data` | BOOLEAN | Coverage flag per source |
| `<source>_window_start/end` | STRING | Per-source observation window (temporal metadata lives with its own table) |

---

## Gold Layer — Business-Level Output

### Purpose
Turn enriched Silver into the final business deliverable: scored/ranked entities, aggregated trips,
tiled summaries, serving tables. **No new spatial operations and no ingestion at Gold** — all spatial
work and data loading happen in Silver; Gold reads `*_enriched` + temporal Silver tables.

### Guarantees (shape-independent)
| Guarantee | What it means |
|-----------|---------------|
| **Grain preserved** | Row count out of Gold equals the count in its Silver input (no silent drops), for 1:1 pipelines. |
| **Geometry preserved** | No NULL `geometry` in the output. |
| **NULL-safe** | Missing inputs remain NULL / are handled explicitly; no accidental 0-fills that change meaning. |
| **Temporal aggregation** | Multi-row temporal Silver tables are aggregated to the grain **before** the LEFT JOIN onto enriched. |
| **Explainable** | Derived outputs carry provenance — a `score_explanation` JSON for scores, or window/source metadata for aggregates. |

### Table naming
```
org_catalog.gold.<usecase>_<metric_type>
```
Examples: `org_catalog.gold.insurance_exposure`, `org_catalog.gold.fleet_trips`.

### Worked Gold shape A — entity risk scoring (5-step)
The classic entity-enrichment Gold. Additional guarantees:
- **Per-industry source metrics:** each industry maps its own raw columns → genuinely different rankings.
- **Normalize once, in [0,1]** via min-max over the current AOI (scores are **AOI-relative**, not cross-AOI comparable).
- **Weights per industry sum to exactly 1.0**; composite `risk_score` in [0,1].
- **Quantile risk tiers** via `percent_rank()` percentile cuts (target ~5/15/30/30/20% split), **not** fixed score thresholds — guarantees a balanced map regardless of score skew.
- **`score_explanation`** JSON records each factor's value, weight, and source column.

Loading pattern (aggregate temporal, then LEFT JOIN):
```python
enriched  = sedona.table(ENRICHED_TABLE)                       # 1:1 per grain
temporal  = sedona.table(TEMPORAL_TABLE)                       # many rows per grain
agg = temporal.groupBy("asset_id").agg(
    F.max("flood_max_wtr_class").alias("flood_max_wtr_class"),
    F.datediff(F.max("flood_week"), F.min("flood_week")).alias("flood_duration_days"),
)
gold = enriched.join(agg, on="asset_id", how="left")          # row count unchanged
```
Required columns: `<grain>_id`, `geometry`, per-factor normalized `*_factor` DOUBLE (0–1), `risk_score` DOUBLE (0–1), `risk_tier` STRING, `score_explanation` STRING(JSON), plus derived metrics.

### Worked Gold shape B — trip / trajectory aggregation
Non-entity Gold (e.g. fleet-trips eval). The grain is a trip, built from time-ordered pings.
- **Sessionize** pings into trips (gap > N min, or key change) with a window function.
- **Order before building the line** — `COLLECT_LIST` does not preserve order. Sort inside a CTE, then build the geometry.
- **Pre-filter to ≥ 2 points** before `ST_MakeLine` (a single point makes no line).
```sql
WITH ordered AS (                         -- CTE pre-filter for ST_MakeLine
  SELECT vehicle_id, trip_seq,
         sort_array(collect_list(struct(ts, geometry))) AS pts,   -- struct sorts by ts (1st field)
         min(ts) AS start_ts, max(ts) AS end_ts, count(*) AS n_pings
  FROM org_catalog.silver.trip_pings_matched
  GROUP BY vehicle_id, trip_seq
  HAVING count(*) >= 2
)
SELECT vehicle_id, trip_seq, start_ts, end_ts, n_pings,
       ST_MakeLine(transform(pts, p -> p.geometry)) AS geometry   -- points in ts order
FROM ordered;
```
> `ST_MakeLine(array<point>)` and the `transform(...)` extraction are **version-sensitive** — validate on a bounded sample before shipping.

---

## Output Destinations

Each Gold table is written to **at least** one destination.

| Destination | Format | When |
|-------------|--------|------|
| Wherobots Iceberg/Havasu | `org_catalog.gold.<table>` | Always — primary persistence for reprocessing/querying |
| S3 GeoParquet | `s3://<bucket>/gold/<table>/` | Downstream needs file-based access. **Preferred file format — never shapefiles.** |
| Aurora/Postgres (JDBC) | `<schema>.<table>` | Serving to web apps / map platforms |

**Write order (fail-stop):** 1) Iceberg (primary — if it fails, stop), 2) GeoParquet (portable), 3) JDBC (serving, optional).

---

## Cross-Layer Validation Rules

After a full run, these invariants must hold (adapt the scoring-specific ones to your Gold shape):

| Rule | Check |
|------|-------|
| Grain count preserved | `COUNT(*)` in `<grain>_enriched` = `COUNT(*)` in each 1:1 Gold table |
| Geometry preserved | `SELECT COUNT(*) FROM gold WHERE geometry IS NULL` = 0 |
| Temporal table separate | `<grain>_enriched` is 1:1; multi-row data lives in its own table |
| Temporal aggregates joined | Gold count matches enriched count after the LEFT JOIN of aggregates |
| No NULL scores *(scoring Gold)* | `SELECT COUNT(*) FROM gold WHERE risk_score IS NULL` = 0 |
| Score bounds *(scoring Gold)* | `MIN`/`MAX(risk_score)` both within [0,1] |
| Weights sum *(scoring Gold)* | `score_explanation` weights sum to 1.0 |
| Tier distribution *(scoring Gold)* | ~5/15/30/30/20% split (quantile tiers), differing across industries |
| Source columns documented *(scoring Gold)* | `score_explanation` names each source column |
| Idempotent | Re-running any stage reproduces its table byte-for-row-count |

---

## Notes on adaptation
This document generalizes a hazard-risk-scoring reference into a shape-independent contract set. The
entity risk-scoring detail is preserved as **Worked Gold shape A**; **shape B** (trip aggregation)
was added from the fleet-trips eval so the contracts aren't overfit to one Gold. Function-signature
notes were cross-checked against the Wherobots reference on 2026-07-10 but are Sedona-version- and
MCP-availability-sensitive — validate on a bounded sample per the `spatial-sql-patterns` skill.
