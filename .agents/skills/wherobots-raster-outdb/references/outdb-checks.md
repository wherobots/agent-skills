# Diagnostics for out-db raster pipelines

Copy-paste checks. All are cheap; run them before committing to a long raster job.

## 1. Is this raster still a reference?

```sql
SELECT CAST(rast AS STRING) AS kind FROM my_view LIMIT 1
```

`LazyLoadOutDbGridCoverage2D` / `OutDbGridCoverage2D` = out-db.
`GridCoverage2D["genericCoverage"` = materialized.

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

A low percentage means tiling before reducing is wasted work: go straight to
`RS_ZonalStats` on the out-db raster.

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

## Not yet verified

- Whether `RS_Clip` preserves out-db. A test using
  `RS_Clip(RS_FromPath(...), geom, TRUE)` failed with `DATATYPE_MISMATCH`, which is a
  **signature** error, not evidence about laziness. Check the current `RS_Clip` signature in
  the function reference before drawing a conclusion.
- `RS_ReprojectMatch`, `RS_Resample`, `RS_Union`, `RS_AddBand`: unknown. Cast and look.
