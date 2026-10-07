---
name: wherobots-raster
description: Use for raster analysis on WherobotsDB - tiling, out-db vs in-db reads, Python raster UDFs (sedona_vectorized_udf), NDVI and other indexes, zonal stats, point sampling, slope/hillshade/TPI with tile halos, resampling and CRS choices, COG export. Sedona RS_ functions, not GDAL.
---

# Raster analysis on WherobotsDB

How to get correct numbers out of rasters on Wherobots without reading pixels you do not need:
which pattern fits the question, who should read the pixels (JVM or Python), how to keep tiled
focal operations seam-free, and which decisions belong to the analyst.

> Validated on Wherobots Cloud (Spark 4.1.3, `small` runtime) job runs **2026-09-26 to
> 2026-10-05**; reference code unit tests last run 2026-10-07 against Sentinel-2 L2A, Copernicus GLO-30, USGS 3DEP 1/3 arc-second and NAIP
> COGs. Numbers are from those runs or from local unit tests of the reference code; anything not
> measured is marked *untested*. Re-check on runtime upgrades.

**Read the reference file the task needs, in full:**

| File | When |
|------|------|
| [`references/decisions_resampling.md`](references/decisions_resampling.md) | before any pipeline with mixed resolutions, small polygons, geographic DEMs, unknown units: the questions to ask the analyst and what to recommend |
| [`references/tile_vs_zonal.md`](references/tile_vs_zonal.md) | choosing between full-tile work and per-polygon/per-point work; measured costs |
| [`references/edge_effects.md`](references/edge_effects.md) | any focal/neighbourhood operation or scene-wide statistic on tiles: halo widths, two-pass, verification |
| [`references/recipes_terrain.md`](references/recipes_terrain.md) | slope, aspect, hillshade, TRI, TPI, landforms, roughness, curvature, generic focal |
| [`references/recipes_indexes.md`](references/recipes_indexes.md) | NDVI, GNDVI, EVI, SAVI, MSAVI, NDWI, MNDWI, NDMI, NBR, NBR2, NDBI, BSI, CIre; SCL cloud mask; units |
| [`references/export_and_render.md`](references/export_and_render.md) | GeoTIFF/COG export, tile naming and index, reading back, `wherobots_gl.Map`, QGIS caveat |
| [`references/code_terrain_focal.md`](references/code_terrain_focal.md) | numpy/scipy terrain and focal functions (Horn slope, aspect, hillshade, TRI, TPI, landforms, curvature, focal stats) |
| [`references/code_indexes.md`](references/code_indexes.md) | numpy spectral index, SCL mask and in-memory COG functions |
| [`references/code_sedona_udfs.md`](references/code_sedona_udfs.md) | the `@sedona_vectorized_udf` wrappers, halo reader, neighbour-file halo, empty template |
| [`references/code_writer_layout.md`](references/code_writer_layout.md) | tile naming expression, flattening the raster writer's `part-*` folders, rewriting the tile index |

The `code_*` files are one Python module split by topic. Paste the sections you need into a
notebook cell, or above the driver of a job run (a job run uploads a single script).

## 1. Mental model: a raster is a reference until something asks for pixels

| Kind | The row holds | Produced by |
|------|---------------|-------------|
| **out-db** | path + pixel window + band list + georeferencing | `RS_FromPath`, raster reader, STAC reader, `RS_TileExplode`, `RS_Clip` with a rectangle in the raster CRS |
| **in-db** | the pixel array | `RS_AsInDB`, `RS_StackTileExplode`, polygon `RS_Clip`, `RS_Band`, `RS_Resample`, `RS_MapAlgebra`, `RS_FromGeoTiff`, any raster-returning UDF |

Out-db reads fetch only the COG blocks under the requested window (HTTP range requests,
64 KB read-ahead, per-core disk cache). **Anything that produces an in-db raster reads its
whole input extent.** Tile first, then transform.

What reads pixels:

| Reads nothing | Header only | A window | Whole coverage |
|---------------|-------------|----------|----------------|
| `RS_FromPath`, `RS_TileExplode`, `RS_Envelope`, `RS_Intersects`, `RS_Width`, `RS_NumBands`, `RS_SRID`, rectangle `RS_Clip` (same SRID, default crop, no nodata arg) | `RS_MetaData` | `RS_ZonalStats*` (bbox + 1 px when the bbox is < 25 % of the coverage), `RS_Value(s)` (blocks under the points), a UDF on an **out-db** tile (rasterio window read in Python) | polygon `RS_Clip` or any CRS mismatch, `RS_SummaryStats*`, `RS_Band`, `RS_Resample`, `RS_AsInDB`, `RS_StackTileExplode` (per output tile), `RS_Union_Aggr`, `RS_AsGeoTiff`/`RS_AsCOG`, `RS_MapAlgebra` |

Measured on one Sentinel-2 scene: polygon clip in EPSG:4326 against the UTM scene 14.7 s and
120.6 M pixels; rectangle clip in the scene CRS then polygon clip 0.12 s and 10.8 k pixels.
Same statistics. The difference is wasted I/O.

## 2. The Python raster UDF

```python
from sedona.spark.raster import SedonaRaster
from sedona.spark.sql.functions import sedona_vectorized_udf
from sedona.spark.sql.types import RasterType
from shapely.geometry.base import BaseGeometry

@sedona_vectorized_udf(return_type=RasterType())
def ndvi_raster_udf(red_indb: SedonaRaster, nir_outdb: SedonaRaster) -> SedonaRaster:
    out = ...                                          # numpy from red_indb.as_numpy(), nir_outdb.as_numpy()
    return red_indb.with_bands(out[np.newaxis])        # CHW, any band count/dtype, same H x W

df.selectExpr("RS_AsInDB(red) AS red_indb", "nir").select(ndvi_raster_udf(F.col("red_indb"), F.col("nir")))
```

- Any mix of raster (`SedonaRaster`), geometry (`BaseGeometry`) and scalar arguments
  (`F.lit(...)`). Annotate or they arrive as bytes. Column API only, not `spark.udf.register`.
- Rows run one at a time inside an Arrow batch. First call per cluster costs 15 to 20 s.
- **Out-db argument** = cheap path: JVM ships a reference, Python reads the window through
  rasterio. Values are **raw DN** (scale/offset not applied). Zero JVM pixel copies.
- **In-db argument** = expensive path: JVM reads, serialises to Arrow, Python decodes.
- **Raster out needs a template** for `with_bands()` (no `from_numpy` exists). Use an **empty**
  template, not `RS_AsInDB(tile)`: `RS_MakeEmptyRaster(1, 'F', RS_Width(r), RS_Height(r),
  RS_UpperLeftX(r), RS_UpperLeftY(r), RS_ScaleX(r), RS_ScaleY(r), RS_SkewX(r), RS_SkewY(r),
  RS_SRID(r))` reads no pixels (`empty_template_sql` in `code_sedona_udfs.md`). *Measured*
  2026-10-05: 46 s vs 79 s on 313 M cells, identical output. `RS_AsInDB` only when the UDF
  actually needs those pixels from the JVM (many small rows, see 3).
- `as_numpy()` (bands, H, W); `as_numpy_masked()` turns per-band nodata into NaN;
  `bands_meta[i].nodata`, `affine_trans`, `crs_wkt`, `width`, `height`, `path`,
  `outdb_meta.band_indices`.
- Runtime has numpy, scipy, rasterio, scikit-learn; xarray-spatial does not import (numba
  CUDA); libpysal/esda install as PyPI dependencies.
- Requester-pays buckets (NAIP `s3://naip-analytic`): SQL works through a storage
  integration, Python needs `rasterio.Env(AWS_REQUEST_PAYER="requester")`.
- **Vectorized vs legacy styles (measured 2026-09-30).** Same function, three styles, identical
  results: on 121 large out-db tiles all tie (12.9 to 13.1 s, rasterio reads dominate); on 1,849
  small in-db tiles the vectorized UDF and `pandas_udf` take 1.4 s against 3.5 s for the legacy
  row `udf`. Always use the vectorized decorator: it adds SedonaRaster decoding, typed geometry
  arguments and raster returns on top of the `pandas_udf` transport.

## 3. Decision trees

### Which pattern?

```text
What do you need?
├─ a number per POLYGON ........................ zonal: footprint join -> bbox RS_Clip (out-db) -> RS_ZonalStats
│    ├─ custom pixel rule / masked raster out .. RS_AsInDB(RS_Clip(bbox)) into a masking UDF
│    └─ thousands of tiny polygons ............. JVM reads (RS_AsInDB on windows); never Python per-row reads
├─ a value per POINT ........................... RS_Value / RS_Values on the out-db raster, points in the raster CRS
├─ a NUMBER for a whole area ................... scalar UDF on out-db band tiles -> [sum, count] -> aggregate
├─ a new RASTER (index, mask) .................. empty template + bands out-db -> raster UDF
├─ a new RASTER from a NEIGHBOURHOOD ........... same, plus the halo read (edge_effects.md, recipes_terrain.md)
├─ a scene-wide STATISTIC (Moran, LISA, Gi*) ... two-pass: [sum,count] then halo partial sums (edge_effects.md)
└─ a persisted multi-band STACK ................ RS_StackTileExplode(ARRAY(...), refIdx, w, h); otherwise no stack
```

### Who reads the pixels?

```text
How many rows, how big?
├─ few large tiles (scene at 1024 px) ......... Python reads out-db (18 s/scene vs 34 s stack-UDF vs 43 s MapAlgebra)
└─ many small rows (field windows on NAIP) .... JVM reads: RS_AsInDB / bbox windows (6 s vs 233 s Python per-row)
```

### Which grid? (details and the questions to ask in decisions_resampling.md)

```text
Bands at different resolutions?
├─ objects small (fields < 5 acres, footprints) ... finest grid, nearest-neighbour up (16 small fields: 93 px at 10 m vs 22 at 20 m; NDMI differs up to 0.10)
├─ objects large and the coarse band carries the signal ... coarse grid (pixels / 4; 32 s vs 41 s for a 3-band stack)
└─ unsure .......................................... ask; state the grid in the pipeline header
Raster geographic (EPSG:4326/4269)?
└─ cell sizes in metres per row from the latitude (8.1 x 10.3 m for USGS 1/3" at 37.9 N) or reproject first
```

### Focal operation on tiles?

```text
Kernel radius r (3x3 -> 1, 5x5 -> 2, TPI radius r -> r, chained kernels -> sum)
└─ pass a template AND the out-db tile; read window + r ring from the source COG; compute; trim
   Without it: seams up to 19 deg of slope, mean tile slope off by 0.68 deg (256-px tiles). With it: 0.
Source split over many files (Copernicus 1-degree COGs, 3DEP tiles, quads)?
└─ add neighbour files per tile (with_neighbour_files), *_nb_udf(t, rast, neighbours)
   Per-file halo on Copernicus (no nodata tag): 82.6 deg fake cliffs on 1-degree lines. Neighbour halo: 0.
```

## 4. Loading and formats

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

## 5. Units: DN vs reflectance

Depends on the **file's** scale/offset tags, not on STAC metadata or the collection name.
`wherobots_open_data.sentinel2.l2a_source_items` -> `sentinel-cogs` files: no tags, DN in SQL
and in Python, DN = reflectance x 10000 with **no** offset (lush field red DN ~550, NIR ~5800).
Use `DN / 10000`, DN 0 = nodata. Earth Search Collection-1: SQL returns rescaled doubles, a UDF
on out-db still sees DN. **Verify on a known target** (vegetation red 0.03-0.08, NIR 0.3-0.5);
a wrong offset pushed every field NDVI to 1.0 with no error. See `recipes_indexes.md`.

## 6. Core SQL patterns

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

## 7. Top rules

1. Tile at the COG block size, footprint-filter with `RS_Intersects`, only then touch pixels.
2. Never pass an untiled scene to a UDF, `RS_AsInDB`, a stack or MapAlgebra (241 MB per
   UInt16 Sentinel-2 band, 964 MB as doubles).
3. Window before polygon: rectangle `RS_Clip` in the raster CRS, or let `RS_ZonalStats` do it.
   4 to 100x faster, 2.9 GB executor peak instead of 8.9 to 15.7 GB.
4. Few large tiles: out-db into Python. Many small rows: JVM reads (`RS_AsInDB`).
5. Raster out: empty template, never `RS_AsInDB` just for georeferencing; read pixels out-db in
   the UDF. Metadata on the JVM (`RS_BandPath`: 0.5 s vs 6.2 s Python UDF, 1584 rows).
6. Focal kernels need a halo of the kernel radius from the source COG (and from the neighbouring
   files when the source is split; fill the ring with NaN, never the file nodata); scene-wide
   statistics need two passes. Verify against a single-pass computation on a subset.
7. Choose the grid by the size of the objects measured, not by cost; state it in the pipeline.
8. Verify units and nodata on a known target; set nodata on every raster you write
   (`RS_SetBandNoDataValue`) or `RS_SummaryStats` returns NaN.
9. Check SRIDs on both sides (`RS_SRID`, `ST_SRID`); `ST_SetSRID` then `ST_Transform` into the
   raster CRS; a mismatch (EPSG:4269 polygons against an EPSG:4326 raster) returns false silently.
10. `RS_Clip` output has one band; clip bands separately. Geometry in the same CRS as the raster.
11. Ratio indexes per polygon: index first, then aggregate. NDVI of the zonal band means is a
    brightness-weighted mean, off by up to 0.12 NDVI on heterogeneous fields.
12. Aspect is an angle: aggregate with a circular mean (sin/cos), never `RS_ZonalStats` mean.
13. Benchmark with `count(col)` on every column or `collect()`; `df.count()` prunes UDFs.
14. Sort by source path; `spark.wherobots.raster.outdb.readahead=4m` for whole-tile reads,
    64 KB default for point sampling. Cache the vector side; persist stage outputs to Havasu.
15. Name exported tiles `<product>_<variant>_<crs>_<cell>_<ulx>_<uly>.tif` from the tile's
    upper-left corner (never bare grid indices), one product per folder, one run per stamp, and
    write a tile index plus manifest beside them; the writer nests files under `part-*` and that
    cannot be turned off.

## 8. Open data gotchas

- `wherobots_open_data.sentinel2.l2a_source_items`: STAC items linking DN files with 1024-px
  blocks. Filter scenes by `eo:cloud_cover` before reading pixels.
- `wherobots_open_data.copernicus_dem.glo_30m`: 256-px out-db tiles of the 1-degree COGs,
  EPSG:4326. For focal work use it as a **file index**: filter `name`, take `RS_BandPath`,
  re-tile `RS_FromPath` at 2048 px (6.4x faster than the stored tiles; lookup by name 2 to 4 s vs
  26 s for a bucket glob or an `RS_Intersects` filter over the global table).
- Copernicus GLO-30 and ASTER GDEM (`aster_gdem.v3_30m`) are **surface** models (canopy,
  buildings), not bare earth: say so when the product is slope or terrain. Bare-earth
  alternatives outside the catalog: USGS 3DEP (US), GEDTM30 (global, CC-BY-4.0), FABDEM
  (non-commercial).

## Decisions to ask the analyst

These change the numbers, not the runtime. Ask, then record the answer in the pipeline header.

- **Zonal boundary rule** (before zonal statistics): centroid-in (default) or all-touched.
  All-touched gives small polygons a value but pulls edge values in and double-counts cells
  shared by neighbours. Fix it for the whole table. Details in `decisions_resampling.md` item 8.
- **Output format** (before writing): who reads the result next, and how many times? COG tiles
  plus a GeoParquet index (GIS deliverable, default), merged COG (small areas), Havasu table with
  out-db references (queryable raster catalogue), Havasu table with in-db chips (ML samples),
  GeoParquet (per polygon/point numbers), PNG (preview). State the cost of the choice. Table in
  `export_and_render.md`.
- **Output location** (before the first write): who needs to find the result and with which
  identity? Shared managed storage for team-visible results, a storage integration bucket for
  customer-owned deliverables, a Havasu table for queryable libraries. Never the caller's
  private folder for job runs: a service-principal key writes where no human can browse.
- **After any tiled write**: keep the writer's `part-*` layout (Spark-only consumers) or flatten
  into one folder with the index updated (deliverables a person opens). See
  `code_writer_layout.md`.

## Sibling skills

- `wherobots-raster-outdb` (if installed; proposed in wherobots/agent-skills#7) — diagnosing whether a stored raster column is still out-db, and the
  traps on the `RS_MapAlgebra` path (Jiffle syntax, `COUNT(*)` not forcing evaluation).
- `wherobots-open-data-catalog` — the raster tables in `wherobots_open_data` as data: coverage,
  snapshots, join keys.
- `wherobots-pipeline-designer` — where raster stages sit in a Bronze/Silver/Gold pipeline.
- `wherobots-develop` — submitting the job runs these patterns run in.
