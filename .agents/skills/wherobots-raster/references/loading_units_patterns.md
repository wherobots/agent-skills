# Loading, units and core patterns

Entry points and formats, the DN-vs-reflectance check, and the copy-ready SQL/PySpark patterns
the rest of the skill builds on.

> Validated on Wherobots Cloud job runs 2026-09-26 to 2026-10-05 (Spark 4.1.3, `small` runtime)
> against Sentinel-2 L2A, Copernicus GLO-30 and California field polygons.

## Loading and formats

| Entry point | Result | Formats | Rescale default |
|-------------|--------|---------|-----------------|
| `sedona.read.format("raster").load("s3a://bucket/prefix/")` | one out-db tile per internal block (`tileWidth`/`tileHeight`), auto-repartitioned | GeoTIFF/COG, ArcGrid `.asc` | no (`autoRescale`) |
| `RS_FromPath(path)` | one lazy out-db raster; https S3 URLs rewritten to s3a | GeoTIFF/COG, `.asc` | **yes** (`'raster.reader.auto-rescale=false'`) |
| `format("stac")` | STAC items + `assets.<band>.rast` out-db | COG hrefs | yes |
| `RS_FromGeoTiff(content)` via `binaryFile` | in-db, whole file | GeoTIFF | yes |
| `RS_FromNetCDF(content, var)` | in-db | NetCDF | - |

GeoTools/imageio-ext underneath, not GDAL: **no JPEG2000, HDF, Zarr, GeoPackage rasters**;
convert with `rio cogeo create --blocksize 1024` or `gdal_translate -of COG`. The raster
reader is DataFrame-only (``FROM raster.`path` `` fails) and needs `s3a://`, not https.
Check `RS_MetaData` first: `tileWidth`/`tileHeight` (tile at that size), `srid` (0 means an
unrecognised CRS such as NLCD Albers: polygon transforms silently assume WGS84), block shape
(striped files 10812 x 1 work but tile explode is 14x slower).

## Units: DN vs reflectance

Depends on the **file's** scale/offset tags, not on STAC metadata or the collection name.
`wherobots_open_data.sentinel2.l2a_source_items` -> `sentinel-cogs` files: no tags, DN in SQL
and in Python, DN = reflectance x 10000 with **no** offset (lush field red DN ~550, NIR ~5800).
Use `DN / 10000`, DN 0 = nodata. Earth Search Collection-1: SQL returns rescaled doubles, a UDF
on out-db still sees DN. **Verify on a known target** (vegetation red 0.03-0.08, NIR 0.3-0.5);
a wrong offset pushed every field NDVI to 1.0 with no error. See `recipes_indexes.md`.

## Core patterns

Tile and filter (free):

```sql
SELECT x, y, tile FROM (SELECT RS_TileExplode(RS_FromPath('<s3>'), 1024, 1024) AS (x, y, tile))
WHERE RS_Intersects(tile, ST_GeomFromText('<aoi>', 4326))
```

Zonal (window-first, one call per field-tile pair; `geom_r` is the polygon in the raster CRS):

```sql
SELECT id, sum(st.sum) / sum(st.count) AS mean_v, sum(st.count) AS n_px FROM (
  SELECT f.id, RS_ZonalStatsAll(RS_Clip(t.r, 1, ST_Envelope(f.geom_r)), f.geom_r, 1) AS st
  FROM fields f JOIN tiles t ON RS_Intersects(t.r, f.geom_r)) WHERE st.count > 0 GROUP BY id
```

Sum and count per pair, divide once per field: a field split over tiles gets the exact mean.
*Measured* 2026-10-05 (247,613 California fields, 273 k pairs, identical per-field results):
`RS_ZonalStatsAll` = two `RS_ZonalStats` calls (296 s both); bbox `RS_Clip` first 273 s (-8 %).
The boundary rule is an analyst decision (below). The argument after `stat` in
`RS_ZonalStats(r, g, band, stat, ...)` is `allTouched`, not `excludeNoData`: set it on purpose
and state it in the pipeline header.

Terrain at any scale (fastest measured, exact; full recipe and numbers in `recipes_terrain.md`
"Recommended pipeline", UDFs in `code_sedona_udfs.md`):

```python
paths = sedona.table("wherobots_open_data.copernicus_dem.glo_30m").where(F.col("name").isin(file_names)) \
    .selectExpr("regexp_replace(RS_BandPath(rast), '^s3://', 's3a://') AS p").distinct()
tiles = paths.selectExpr("RS_TileExplode(RS_FromPath(p), 2048, 2048) AS (x, y, rast)")   # or glob the bucket
tiles = with_neighbour_files(tiles, pad_deg=2 * pixel_size)          # path via RS_BandPath + neighbour files
out = (tiles.selectExpr("path", "x", "y", empty_template_sql("rast"), "rast", "neighbours")
       .select("path", "x", "y", slope_nb_udf(F.col("t"), F.col("rast"), F.col("neighbours")).alias("r"))
       .selectExpr("path", "x", "y", "RS_SetBandNoDataValue(r, 1, CAST('NaN' AS DOUBLE)) AS r"))
```

Export (distributed, one file per row; naming in `export_and_render.md`):

```python
df.selectExpr("RS_AsCOG(r) AS raster_binary", "<unique name expr> AS path") \
  .write.format("raster").option("rasterField", "raster_binary").option("pathField", "path") \
  .option("fileExtension", ".tif").save(out_dir)
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
