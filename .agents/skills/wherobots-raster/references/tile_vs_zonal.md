# Full-tile operations vs zonal (polygon) operations vs points

Two very different access patterns. Full-tile work touches every pixel once and wants few,
large reads; zonal work touches a small window per polygon and wants the engine to read as
little as possible per row. Mixing them up is the most expensive mistake on the platform.
Numbers marked *measured* are from Wherobots Cloud job runs 2026-09-26 to 2026-10-05 (`small` runtime, one Sentinel-2 scene, 200 to 595 California field polygons, NAIP
2022 quads).

## Full-tile operations (index rasters, terrain, filters)

Pattern:

1. Reference the source (`RS_FromPath`, raster reader, STAC), `RS_TileExplode(rast, B, B)` at the
   COG block size `B` (`RS_MetaData` -> `tileWidth`). Out-db, no I/O: 121 tiles from a
   10980 x 10980 band in 0.27 s (*measured*).
2. Footprint filter `RS_Intersects(tile, aoi)` before anything reads pixels. Free.
3. Join per-band tile sets on `(x, y)` so one row carries the red tile, the NIR tile, the SWIR
   tile as **separate out-db columns**. No stack.
4. **Number out**: multi-argument scalar UDF on the out-db columns (`ndvi_sum_count_udf`),
   aggregate in Spark. Zero JVM pixel copies.
5. **Raster out**: the empty template (`empty_template_sql`) plus the bands out-db into a
   raster-returning UDF; then `RS_SetBandNoDataValue(r, 1, CAST('NaN' AS DOUBLE))`.
6. Export per tile with the distributed writer (see `export_and_render.md`), or persist the
   tiles to a Havasu table.

*Measured*, whole-scene NDVI, 121 tiles of 1024 px, 2 bands (241 M px in):

| Path | Wall | Notes |
|------|------|-------|
| C: two out-db tiles into one scalar UDF | **18.2 s** | rasterio window reads in Python, nothing copied on the JVM |
| D: `RS_AsInDB(red)` template + out-db NIR, raster out | 24.0 s | half the JVM copy of B |
| B: `RS_StackTileExplode` then UDF | 34.5 s | stack -> Arrow -> numpy, two JVM copies per tile |
| A: `RS_StackTileExplode` then `RS_MapAlgebra` | 43.2 s | slowest measured |

3-band stack with a 20 m band: no-stack multi-arg UDF 24.9 s; `RS_StackTileExplode` at the
20 m reference 32.0 s (90 M px); `RS_Union_Aggr` 30.5 s; `RS_StackTileExplode` at the 10 m
reference 41.3 s (362 M px). Stack only when the stack itself must be persisted.

Terrain (focal): the same pattern plus the halo read; see `edge_effects.md` and
`recipes_terrain.md`. 25 tiles of 512 px, slope/aspect/hillshade with halo: 10.8 s
(*measured*, export job).

Rules of thumb for full-tile work:

- Few large tiles into Python (out-db). The first UDF call on a cluster costs 15 to 20 s
  (worker start, rasterio import, S3 session); warm up before timing.
- Sort or partition by source path so a core reuses its block cache; raise
  `spark.wherobots.raster.outdb.readahead` to `4m` for whole-tile reads (default 64 KB).
- Never pass an untiled scene to a UDF, `RS_AsInDB`, a stack or MapAlgebra: one Sentinel-2
  band is 241 MB as UInt16 and 964 MB as doubles; `RS_AsInDB` on the whole scene then a UDF
  took 6.9 s for one row and 120 M px through Arrow (*measured*), and it does not scale.
- In-db template only where a raster must come out. Everything else stays out-db.

## Zonal operations (one number per polygon)

**Polygons:** any polygon table works (field boundaries, parcels, admin units, buffers; e.g. an
Overture land-use layer). Check `ST_SRID` (a table tagged EPSG:4269 against EPSG:4326 geometry
makes predicates return false silently), `ST_SetSRID` to the true SRID, then `ST_Transform` into
the raster CRS. Compare polygon size with the cell size before choosing the grid
(`decisions_resampling.md` item 2).

**Reliability flags (every zonal output):** carry `n_px`, `reliable = n_px >= MIN_PX`
(`MIN_PX = 10` by default: below about 10 cells one nodata pixel or the boundary rule dominates)
and the boundary rule, as a column or in the manifest. Measured 2026-10-07: golf-fairway polygons
on 10 m Sentinel-2 had 1 to 4 cells at the low end; without the flag nothing marks those values.

```python
MIN_PX, ALL_TOUCHED = 10, False      # unattended defaults; record both in the manifest
per_poly = sedona.read.format("geoparquet").load(f"{OUT}/polygons_ndvi.parquet")   # any result with n_px
per_poly = (per_poly.withColumn("reliable", F.col("n_px") >= MIN_PX)
                    .withColumn("boundary_rule", F.lit("all-touched" if ALL_TOUCHED else "centroid-in")))
```

Pattern:

1. Fix the vector side: `ST_SetSRID` to the true SRID, `ST_Transform` into the **raster CRS**
   (`geom_r`), `ST_Envelope(geom_r)` as `bbox_r`. `cache()` it; it is small.
2. `LEFT SEMI JOIN` tiles to polygons on `RS_Intersects(tile, geom_r)` so tiles touching no
   polygon are never read (41 of 121 scene tiles kept for 200 fields, *measured*).
3. Per (polygon, tile) pair: `RS_Clip(tile, band, bbox_r)` is the **out-db fast path** (same
   SRID, rectangle, default crop, no nodata argument, unskewed): a header rewrite, no I/O.
   Check with `RS_BandPath(result) IS NOT NULL`.
4. `RS_ZonalStats(window, geom_r, band, 'mean')` (argument order: raster, zone, band, stat,
   then optional `allTouched`, `excludeNoData`, `lenient`), or `RS_ZonalStatsAllBands` to read
   the window once for every band, or a masked UDF (`rasterio.features.geometry_mask` on the
   window) when you need the pixel rule under your control.
5. Aggregate across tiles: `sum(mean * count) / sum(count)` grouped by polygon id, or return
   `[sum, count]` from the UDF and sum both.

Single band (here a precomputed NDVI tile set; for an index from raw bands use the per-pixel
UDF path, `recipes_indexes.md` "Call pattern 2"):

```sql
SELECT s.id, sum(s.st.sum) / sum(s.st.count) AS mean_ndvi, sum(s.st.count) AS n_px,
       sum(s.st.count) >= 10 AS reliable, 'centroid-in' AS boundary_rule
FROM (
  SELECT f.id, RS_ZonalStatsAll(RS_Clip(t.ndvi, 1, f.bbox_r), f.geom_r, 1, false) AS st
  FROM fields f JOIN ndvi_tiles t ON RS_Intersects(t.ndvi, f.geom_r)
) s WHERE s.st.count > 0 GROUP BY s.id
```

*Measured*, 200 fields, 219 (field, tile) pairs, one NIR band:

| Path | Wall | Pixels in |
|------|------|-----------|
| A: `RS_Clip(tile, 1, polygon)` straight on the tile | 14.2 s | 215 M |
| B: `RS_Clip(bbox)` out-db, then polygon clip | 3.4 s | 335 k |
| C: `RS_ZonalStats(tile, polygon)` (engine does the bbox trick when the bbox is < 25 % of the coverage) | **2.9 s** | 335 k |
| D: UDF on the full tile + geometry, mask in numpy | 13.6 s | 215 M |
| E: `RS_AsInDB(RS_Clip(bbox))` into a masking UDF, raster out | 3.1 s | 335 k |

Window-first is 4 to 5x faster here and 100x on a single scene-level clip (0.12 s / 10.8 k px
vs 14.7 s / 120.6 M px for a polygon in EPSG:4326 against a UTM scene: the CRS mismatch alone
forces the whole-coverage in-db path).

NDVI per field, 41 tiles, 211 pairs (*measured*): bbox-clip each band then UDF **17.6 s on
1 M px**; NDVI tile then zonal stats 20.3 s on 83 M px; UDF on full tile windows 26.6 s.

Executor JVM peak memory per job run tracks the largest in-db raster in flight: 2.9 GB for
the window-first run, 8.9 GB for the run clipping whole tiles, 15.7 GB for the run computing
whole-tile NDVI rasters (*measured*).

### Many small rows: let the JVM read

*Measured*, NAIP 60 cm, 200 fields over 198 quads (103,950 tiles, 1,398 pairs, two UTM
zones, requester-pays bucket through a storage integration):

| Path | Wall |
|------|------|
| B: single-band bbox clips then `RS_ZonalStats` per band, NDVI of the means | 6.1 s, **but a different estimator** (brightness-weighted; see below) |
| D: both bbox windows `RS_AsInDB` into a raster UDF | 6.5 s |
| C2: JVM reads the 4-band tile (`RS_AsInDB`), Arrow to Python, mask | 27.0 s |
| A: `RS_MapAlgebra` on whole tiles then zonal | 38.3 s |
| C: Python reads the tile window out-db per row (rasterio, requester-pays) | 233 s |

Per-row GDAL open in Python is the cost: 6x slower than letting the JVM read. Rule: **many
small rows -> JVM reads (`RS_AsInDB` on the window); few large tiles -> Python reads
(out-db)**. Requester-pays buckets need `rasterio.Env(AWS_REQUEST_PAYER="requester")` in
the UDF or Python gets Access Denied even though SQL works (`open_source(..., requester_pays=True)`).

Other zonal gotchas (*measured*):

- `RS_Clip(raster, band, geom)` output has **one band**, the one you pass. Clip band 1 and
  band 4 separately for NAIP NDVI; `RS_Band` on the clipped window fails.
- Agreement between `RS_ZonalStats` and the masked UDF: 9.9e-5 max on 595 Sentinel-2 fields;
  0.031 mean on NAIP until nodata (zero collar) and centroid vs all-touched were pinned.
- Fields tagged EPSG:4269 vs EPSG:4326 geometry: predicate silently false. `ST_SetSRID` first.
- `df.count()` on a projection prunes the UDF and the stats; benchmark with `count(col)` on
  every column or `collect()`.
- 1585 H3 cells (resolution 9) against one small COG: 8.8 s with `RS_ZonalStats` on the
  out-db reference, no tiling needed when the raster is one small file.

## Point extraction

- `ST_Transform` the points into the raster CRS once; `RS_Value(rast, point, band)` or
  `RS_Values(rast, array(points), band)` on the **out-db** raster reads only the COG blocks
  under the points. Leave `readahead` at the 64 KB default for point sampling.
- Many points: footprint-join points to tiles first so each tile is opened once per task, then
  sort by tile so the per-core block cache hits.
- No precomputed index needed: sample each band and compute from the numbers. NDVI at 30
  points from two out-db bands agreed with the exported NDVI COG to 7e-8 (*measured*).
- Points near cell edges on coarse bands: buffer and use `RS_ZonalStats` (see
  `decisions_resampling.md`, item 2).

## Choosing

| Question | Do this |
|----------|---------|
| Every pixel of an area, index or terrain, raster out | full-tile: out-db bands into a UDF, one in-db template |
| Every pixel, one number for the area | full-tile: scalar UDF `[sum, count]`, aggregate |
| One number per polygon, polygons small relative to tiles | zonal: bbox `RS_Clip` (out-db) + `RS_ZonalStats` |
| One number per polygon, needs a custom pixel rule or a masked raster out | zonal: `RS_AsInDB(RS_Clip(bbox))` into a masking UDF |
| Thousands of tiny polygons on high-res imagery | zonal with JVM reads; never Python per-row out-db reads |
| Polygons larger than a tile | index the tiles once (full-tile), then zonal on the index tiles with `sum(mean*count)/sum(count)` |
| Values at points | `RS_Value(s)` on out-db, points transformed into the raster CRS |

## Ratio indexes and zonal statistics: index first, then aggregate

Never compute a ratio index (NDVI, NDMI, ...) from the zonal means of its bands: it is a
brightness-weighted mean, off by up to 0.12 NDVI (*measured* 2026-09-27, Sentinel-2 10 m). The
measurement and the correct SQL and UDF paths are in `recipes_indexes.md`.
