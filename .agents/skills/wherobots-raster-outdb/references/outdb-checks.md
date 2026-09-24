# Diagnostics for out-db raster pipelines

Copy-paste checks. All are cheap; run them before committing to a long raster job.

## 1. Is this raster still a reference?

```sql
SELECT CAST(rast AS STRING) AS kind FROM my_view LIMIT 1
```

Read the CLASS, not the coverage name:

| class | name | meaning |
|---|---|---|
| `LazyLoadOutDbGridCoverage2D` | `[not loaded]` | out-db, unread |
| `OutDbGridCoverage2D` | `"outDbCoverage"` | out-db |
| `GridCoverage2D` | `"outDbCoverage"` | in-db class, out-db provenance (`RS_Union`) |
| `GridCoverage2D` | `"genericCoverage"` | materialized |

A substring test for `OutDb` misreads `"outDbCoverage"` (lowercase first letter), and the name
alone does not tell you the class. **Only meaningful on a stored column** — casting an inline
expression to a string forces it to evaluate, so the class you see may be the probe's doing.

## 2. Which step broke it?

Cast after each stage. The first stage showing `genericCoverage` is the culprit.

```sql
CREATE OR REPLACE TEMP VIEW s1 AS SELECT RS_FromPath(p) AS rast FROM paths;
CREATE OR REPLACE TEMP VIEW s2 AS
  SELECT t.rast FROM s1 LATERAL VIEW RS_TileExplode(rast, 512, 512) t AS x, y, rast;
SELECT 's1' step, CAST(rast AS STRING) k FROM s1 LIMIT 1
UNION ALL
SELECT 's2', CAST(rast AS STRING) FROM s2 LIMIT 1
```

## 3. Is my zone-to-raster ratio sane?

```sql
SELECT
  SUM(ST_Area(geom)) / 1e6                       AS zone_km2,
  (SELECT SUM(ST_Area(footprint)) / 1e6 FROM items) AS raster_km2,
  100 * SUM(ST_Area(geom)) /
        (SELECT SUM(ST_Area(footprint)) FROM items) AS pct
FROM zones
```

A low percentage suggests tiling before reducing is wasted work, but **do not convert that
ratio into a speedup estimate**. Measured on one scene and 1,087 zones covering under 1% of it,
the actual gap between reducing raw bands out-db and tiling with per-pixel algebra was **4.9x**
(11.6 s vs 56.6 s), not the ~100x the area ratio implies. Per-zone overhead dominates once the
read is windowed. Time both on a bounded sample.

## 4. Does this map-algebra script actually run?

`COUNT(*)` will pass over a script that cannot parse. Force evaluation:

```sql
SELECT RS_NumBands(RS_MapAlgebra(rast, 'D', '<script>')) AS bands,
       RS_Value(RS_MapAlgebra(rast, 'D', '<script>'),
                ST_Centroid(RS_Envelope(rast)), 1) AS sample
FROM tiles LIMIT 1
```

A Jiffle parse error surfaces here and nowhere else.

## 5. Is masking suppressing or fabricating?

On a known nodata pixel, an unguarded expression returns a real-looking number:

| script | value at a nodata pixel |
|---|---|
| `red = rast[0]; out[0] = red * 0.0001 + (-0.1);` | `-0.1` — fabricated |
| `red = rast[0]; out[0] = con(red == 0, NULL, red * 0.0001 + (-0.1));` | `NULL` — suppressed |

Only the second is excluded by `RS_ZonalStats(..., excludeNoData => TRUE)`.

## 6. Which is faster, on YOUR data

```sql
-- A: reduce raw bands out-db, then compute the index  (NDVI(mean))
SELECT AVG((nn-rr)/NULLIF(nn+rr,0)) FROM (
  SELECT RS_ZonalStats(red_rast, z.geom, 1,'mean',TRUE)*scale+offset rr,
         RS_ZonalStats(nir_rast, z.geom, 1,'mean',TRUE)*scale+offset nn
  FROM items, zones z)

-- B: per-pixel index, tiled, then reduce  (mean(NDVI))
--    RS_Union -> RS_StackTileExplode -> RS_MapAlgebra -> RS_ZonalStats
```

Time both. They return **different numbers**, so the choice is a specification decision;
the timing only tells you what it costs.

Put the raster expression OUTSIDE the row-wise join. `FROM items i, zones z` with
`RS_MapAlgebra(...)` in the SELECT recomputes the whole scene once PER ZONE. That mistake made
a one-item query fail to finish in 77 minutes.

## Not yet verified

- Whether `RS_Clip` preserves out-db. A test using
  `RS_Clip(RS_FromPath(...), geom, TRUE)` failed with `DATATYPE_MISMATCH`, which is a
  **signature** error, not evidence about laziness. Check the current `RS_Clip` signature in
  the function reference before drawing a conclusion.
- `RS_ReprojectMatch`, `RS_Resample`, `RS_Union`, `RS_AddBand`: unknown. Cast and look.
