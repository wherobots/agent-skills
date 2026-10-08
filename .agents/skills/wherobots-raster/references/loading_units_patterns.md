# Loading, probing, units and core patterns

Entry points and formats, a probe that works on any raster, the generic units check, and the
copy-ready patterns the rest of the skill builds on.

> Validated on Wherobots Cloud 2026-09-26 to 2026-10-08 (Spark 4.1.3). The probe was run on
> 2026-10-08 against a Copernicus GLO-30 file and a Sentinel-2 L2A band (outputs below).

## Probe a source

Run on one file (or one `RS_BandPath` from a catalog table) before choosing a pipeline:

```sql
SELECT RS_MetaData(r)               AS md,          -- grid, scale, srid (0 = unrecognised), tileWidth/Height = block size
       RS_NumBands(r)               AS bands,
       RS_BandPixelType(r, 1)       AS dtype,
       RS_BandNoDataValue(r, 1)     AS nodata,      -- NULL = no tag: choose nodata yourself
       RS_UpperLeftX(r) / RS_ScaleX(r) - FLOOR(RS_UpperLeftX(r) / RS_ScaleX(r)) AS ul_frac_px,  -- 0 grid-aligned, 0.5 half-pixel origin
       RS_SummaryStatsAll(RS_Clip(r, 1, ST_Envelope(ST_Buffer(ST_Centroid(RS_Envelope(r)), 30 * RS_ScaleX(r)))), 1, true) AS sample_tagged,
       RS_SummaryStatsAll(RS_Clip(RS_FromPath(p, 'raster.reader.auto-rescale=false'), 1,
                          ST_Envelope(ST_Buffer(ST_Centroid(RS_Envelope(r)), 30 * RS_ScaleX(r)))), 1, true) AS sample_raw
FROM (SELECT p, RS_FromPath(p) AS r FROM (SELECT '<s3a:// or https:// path>' AS p))
```

`sample_tagged != sample_raw` means the file carries scale/offset tags: SQL functions on
`RS_FromPath` see rescaled values, an out-db UDF read sees raw values. The window is a rectangle
(`ST_Envelope`), so only a small window is read. For many files, also list them (glob or catalog)
and read their names: the naming/grid rule decides how to select files for an AOI.

*Example outputs, 2026-10-08:* Copernicus GLO-30 `N37_00_W122_00`: float32, nodata NULL,
`ul_frac_px` 0.5 (UL -122.000139), block 1024, tagged = raw. Sentinel-2 L2A red
(`sentinel-cogs`): uint16, nodata 0, `ul_frac_px` 0, block 1024, tagged = raw (no tags: DN).

## Loading and formats

| Entry point | Result | Formats | Rescale default |
|-------------|--------|---------|-----------------|
| `sedona.read.format("raster").load("s3a://bucket/prefix/")` | one out-db tile per internal block (`tileWidth`/`tileHeight`), auto-repartitioned | GeoTIFF/COG, ArcGrid `.asc` | no (`autoRescale`) |
| `RS_FromPath(path)` | one lazy out-db raster; https S3 URLs rewritten to s3a (UDF reads of those tiles worked unchanged, 2026-10-07) | GeoTIFF/COG, `.asc` | **yes** (`'raster.reader.auto-rescale=false'`) |
| `format("stac")` | STAC items + `assets.<band>.rast` out-db | COG hrefs | yes |
| `RS_FromGeoTiff(content)` via `binaryFile` | in-db, whole file | GeoTIFF | yes |
| `RS_FromNetCDF(content, var)` | in-db | NetCDF | - |

GeoTools/imageio-ext underneath, not GDAL: **no JPEG2000, HDF, Zarr, GeoPackage rasters**;
convert with `rio cogeo create --blocksize 1024` or `gdal_translate -of COG`. The raster reader
is DataFrame-only (``FROM raster.`path` `` fails) and needs `s3a://`, not https. Striped files
(block 10812 x 1) work but tile explode is 14x slower (measured): re-tile before heavy use.

## Units check (any source)

1. **Read the file's tags** with the probe; never infer units from STAC metadata or a collection
   name. SQL on `RS_FromPath` applies tags; out-db UDF reads return raw DN.
2. **Convert** with the scale/offset you decided (`s2_reflectance(dn, scale, offset, nodata)` is
   generic despite its name).
3. **Verify on known targets chosen by what is on the ground**, not by a land-use label: a
   land-cover layer class (forest, open water, barren), or a stated spectral pre-filter. A land-use
   polygon is not a cover label: Overture `orchard` polygons in July had median NDVI 0.63 because
   many orchards were young or bare (measured 2026-10-07).

Typical surface-reflectance ranges (literature values used as acceptance bands, not measured here;
the vegetation row matched 98 of 99 vegetated polygons on 2026-10-07):

| Target | Red reflectance | NIR reflectance | NDVI |
|--------|-----------------|-----------------|------|
| dense green vegetation | 0.02-0.08 | 0.25-0.5 | 0.6-0.9 |
| open water | < 0.1 | < 0.05 | < 0 |
| bare soil, dry | 0.1-0.3 | 0.15-0.35 | 0.05-0.25 |

A spectral pre-filter on NDVI (> 0.6) tests the **scale** through the reflectance magnitudes but
cannot test the **offset**, because NDVI moves with it: check the offset on water (NIR near 0,
never negative) and against the signatures below.

| Signature | Cause |
|-----------|-------|
| every pixel or polygon NDVI ~1.0 | an offset applied that the file does not need (measured: -0.1 on `sentinel-cogs` DN) |
| reflectance > 1 everywhere | scale not applied (DN read as reflectance) |
| negative reflectance over land | wrong offset sign or magnitude |
| values 1000x or 10000x off | DN vs reflectance mix-up between SQL and UDF reads |

## Core patterns

Tile and filter (free):

```sql
SELECT x, y, tile FROM (SELECT RS_TileExplode(RS_FromPath('<path>'), 1024, 1024) AS (x, y, tile))
WHERE RS_Intersects(tile, ST_GeomFromText('<aoi wkt>', 4326))
```

Zonal, single band, built-in statistic (`geom_r` is the polygon in the raster CRS; see
`tile_vs_zonal.md` for ratios, reliability flags and the boundary rule):

```sql
SELECT id, sum(st.sum) / sum(st.count) AS mean_v, sum(st.count) AS n_px,
       sum(st.count) >= 10 AS reliable, 'centroid-in' AS boundary_rule FROM (
  SELECT f.id, RS_ZonalStatsAll(RS_Clip(t.r, 1, ST_Envelope(f.geom_r)), f.geom_r, 1) AS st
  FROM fields f JOIN tiles t ON RS_Intersects(t.r, f.geom_r)) WHERE st.count > 0 GROUP BY id
```

Sum and count per pair, divide once per polygon: a polygon split over tiles gets the exact mean.
*Measured* 2026-10-05 (247,613 California fields, 273 k pairs, identical per-field results):
bbox `RS_Clip` first 273 s vs 296 s without (-8 %). The argument after `stat` in
`RS_ZonalStats(r, g, band, stat, ...)` is `allTouched`, not `excludeNoData`: set it on purpose.

Focal rasters (slope, TPI, any kernel): the parameterised template in `recipes_terrain.md`
("Generic focal pipeline") and `code_pipeline.md`.

Export (distributed, one file per row; naming, index and manifest in `export_and_render.md`):

```python
# ndvi_tiles from recipes_indexes.md "Call pattern 1" (raster column ndvi); TILES_DIR from the driver
(ndvi_tiles.persist()
   .selectExpr("RS_AsCOG(ndvi) AS raster_binary", f"{tile_name_expr('ndvi', 'ndvi', 'v1', '10m')} AS path")
   .write.format("raster").option("rasterField", "raster_binary").option("pathField", "path")
   .option("fileExtension", ".tif").save(TILES_DIR))
```

## Is this raster still out-db? (cast check)

Cast the raster to a string and read the **class**, not the coverage name (from
wherobots/agent-skills#7; re-verified 2026-10-07 on `wherobots_open_data.copernicus_dem.glo_30m`):

| `CAST(rast AS STRING)` starts with | Meaning | Seen for |
|---|---|---|
| `LazyLoadOutDbGridCoverage2D[not loaded]` | out-db, nothing read | `RS_FromPath(...)` |
| `OutDbGridCoverage2D[...` | out-db reference | stored catalog tiles, rectangle `RS_Clip` in the raster CRS |
| `GridCoverage2D["genericCoverage", ...` | **materialized** | `RS_AsInDB(...)` |

The coverage name varies (`"havasu-raster"` on catalog tiles) and a lowercase `outDb` can appear
inside an in-db name, so match the class prefix. Reliable on a stored column; casting an inline
expression forces it to evaluate, so judge mid-pipeline expressions by timing instead.

## `RS_MapAlgebra` traps

Verified 2026-10-07 on an `RS_MakeEmptyRaster` input (from wherobots/agent-skills#7):

- The script is **Jiffle, not C**: `double x = rast[0]; out = x;` fails with "Failed to run map
  algebra"; `x = rast[0] + 7; out = x;` works.
- `COUNT(*)` does **not** evaluate it: `SELECT COUNT(*) FROM (SELECT RS_MapAlgebra(..., 'double x = rast[0]; ...'))`
  succeeds over the broken script. Test a script with `RS_Value` or a zonal stat, never a count.
