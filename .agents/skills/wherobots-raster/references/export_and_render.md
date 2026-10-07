# Export and rendering

Everything here was run on Wherobots Cloud job runs (2026-09-27 to 2026-10-05) unless marked
untested.

## Per-tile GeoTIFF export (distributed)

One file per row, written by the executors with the session's storage credentials:

```python
(df.selectExpr("RS_AsGeoTiff(terrain) AS raster_binary",
               f"{tile_name_expr('terrain', 'terrain', 'halo', '10m')} AS path")   # code_writer_layout.md
   .write.format("raster")
   .option("rasterField", "raster_binary").option("pathField", "path").option("fileExtension", ".tif")
   .mode("overwrite").save(f"{OUT}/tiles_halo"))
```

- `RS_AsGeoTiff(r)` plain GeoTIFF; `RS_AsCOG(r)` Cloud Optimized (internal tiling and
  overviews, what map clients want); `RS_AsPNG` for previews. All three read the whole raster
  they are given, so call them on tiles or windows, never on the untiled scene.
- Files land under `<dir>/part-<n>-<uuid>-c000/<path>.tif`, one `part-*` folder per Spark
  partition. List them back with
  `sedona.read.format("binaryFile").option("pathGlobFilter", "*.tif").option("recursiveFileLookup", "true").load(dir).select("path")`.
- *Measured*: 6 NDVI tiles 3.4 s; 25 terrain tiles (3-band float32, 512 px) 14.8 s.
- Set nodata on the raster before export (`RS_SetBandNoDataValue`) so the tag is written.
- **Footgun: identical file names across sibling folders.** `pathField` is the file name
  inside the part folder, so `tiles_halo/part-*/<name>.tif` and
  `tiles_nohalo/part-*/<name>.tif` share a basename when the variant is not in the name. Downloading both into one
  directory overwrites silently, QGIS shows two layers with the same name, and a glob for
  the name matches both. Put the variant in the file name (the `variant` token below) or in the output directory *and* rename on download.

## Merged COG for a study area (driver side, small areas)

Collect the in-db tiles, paste by pixel offset (exact when the tiles were computed with a
halo, see `edge_effects.md`), build the COG bytes in memory with rasterio's `COG` driver,
and ship the bytes through the same writer so the JVM uploads them with the session's
credentials:

```python
arr, origin, crs_wkt = mosaic(df, "terrain")               # CHW float32, top-left affine, WKT
affine6 = (origin.scale_x, origin.skew_x, origin.ip_x, origin.skew_y, origin.scale_y, origin.ip_y)
cog = to_cog_bytes(arr, affine6, crs_wkt, descriptions=["slope_deg", "aspect_deg", "hillshade"])
(sedona.createDataFrame([(bytearray(cog), "terrain_halo_e4269_1as3_w122p0400_n37p9500")], ["raster_binary", "path"])
 .write.format("raster").option("rasterField", "raster_binary").option("pathField", "path")
 .option("fileExtension", ".tif").mode("overwrite").save(f"{OUT}/cog"))
```

`mosaic` is in `code_sedona_udfs.md`, `to_cog_bytes` in `code_indexes.md` (deflate, predictor 3, 512 blocks, average
overviews, NaN nodata, band descriptions). *Measured*: 1297 x 1513 x 3 float32 = 24 MB COG,
4 to 9 s including the write; 2167 x 2161 (25 tiles) merged in 14.8 s for three variants.
The merge holds the whole array on the driver: fine for a few thousand cells on a side, not
for a scene. At scale keep the per-tile files and a tile index (below), or write a GDAL VRT
over the tiles outside the platform (untested here).

Alternative without Python: `RS_AsCOG(RS_AsInDB(aoi))` on the JVM for one clipped raster
(*measured* 18 s for the 2167 x 2161 DEM clip), same writer.

## GeoParquet for vector results

```python
per_field.write.format("geoparquet").mode("overwrite").save(f"{OUT}/fields_ndvi.parquet")
```

Keep a `geometry` column in EPSG:4326 for the map; stats columns as doubles. `RS_Envelope`
of each tile gives a footprint polygon for a tile index.

## Tile index with footprints

```sql
SELECT x, y, RS_Envelope(terrain) AS geometry,
       RS_SummaryStats(terrain, 'mean', 1) AS mean_slope_deg, RS_SummaryStats(terrain, 'max', 1) AS max_slope_deg,
       CONCAT(<tile_name_expr>, '.tif') AS tile_file
FROM halo_tiles
```

written as GeoParquet (*measured* 9.5 s for 25 tiles including the stats). Join it back to the
`binaryFile` listing on `tile_file` to get full S3 paths, and to polygons on
`ST_Intersects(geometry, ...)` to find which files to open for an area.

## Reading back

- `RS_FromPath('<s3 path to .tif>')` gives an out-db reference: `RS_Value`, `RS_ZonalStats`,
  `RS_TileExplode` all work on it without loading (*measured*: 1585 H3 zonal stats in 8.8 s,
  25 point values in 1.4 to 9.8 s against the exported terrain COG).
- Many tiles: `sedona.read.format("raster").load("s3a://.../tiles_halo/")` gives one out-db
  row per internal block (or `tileWidth`/`tileHeight`), DataFrame API only, `s3a://` not https.
- `binaryFile` + `RS_FromGeoTiff(content)` loads whole files in-db: only for small chips.

## Rendering in a notebook

Static preview, band 1 or a chosen band, quick and safe:

```python
SedonaUtils.display_image(sedona.sql(f"SELECT RS_AsImage(RS_Band(RS_AsInDB(RS_FromPath('{COG_URI}')), array(3)), 600) AS hillshade"))
```

Interactive, `wherobots_gl.Map` (preinstalled in Wherobots notebooks; layers load **by URL in
the browser**, so sources must be in managed storage or a CORS-enabled bucket):

```python
from wherobots_gl import Map
Map(
    layers=[
        {"type": "cog", "source": COG_URI, "name": "slope (deg)", "band": 1, "colormap": "viridis", "rescale": [0, 45], "opacity": 0.85},
        {"type": "geoparquet", "source": fields_out, "name": "mean NDVI per field", "colorByColumn": "mean_ndvi",
         "colorByColormap": {"type": "preset", "preset": "RdYlGn"}, "colorByDomain": [0.0, 0.9], "opacity": 0.85},
        {"type": "geoparquet", "source": pts_out, "name": "sample points"},
    ],
    view={"lat": 36.62, "lng": -120.27, "zoom": 11},
    basemap="dark",
)
```

- Layer `type`: `cog`, `geoparquet`, `zarr` (RasterFlow mosaics and multiscale stores),
  `pmtiles` (vector tiles). The layer dict keys are forwarded to the front end; `source`,
  `name`, `opacity` and the geoparquet keys `colorByColumn`, `colorByColormap`
  (`{"type": "preset", "preset": ..., "reversed": bool}`), `colorByDomain` are from the
  Wherobots docs (Getis-Ord and NOAA examples). The `cog` keys `band`, `colormap`, `rescale`
  ran only inside job runs, where the map cell is skipped, so they are **untested in a live
  notebook**; check the `wherobots_gl` docstring on the runtime
  before relying on them.
- Large COGs: the browser fetches overviews from the COG, so export with overviews
  (`RS_AsCOG` or the rasterio COG driver, never plain `RS_AsGeoTiff`) or panning is slow.
- Colormap and `rescale` are per layer, so a merged COG renders with one stretch across the
  whole area. Per-tile files loaded as separate layers each get their own stretch.

## QGIS caveat

When per-tile GeoTIFFs are dragged into QGIS as separate layers, each layer gets its own
contrast stretch (min/max from that tile's statistics), so neighbouring tiles render with
different grey ramps and the edges look like seams even when the values are continuous
across them. Check the values with the identify tool, or load the tiles through a single
VRT / the merged COG, or set the same min/max on every layer, before concluding the halo
failed. The real seam test is numeric (`edge_effects.md`, "How to verify"), not visual.

## Decision point: how should raster results leave the job?

Ask the analyst before writing anything: **who or what reads this next, and how many times?**
The answer picks the format. Do not default silently.

| Option | Write with | Who can read it | Size and cost | Choose when |
|--------|-----------|-----------------|---------------|-------------|
| Cloud Optimized GeoTIFF, one per tile | `RS_AsCOG(rast)` + `write.format("raster")` with `pathField`; plus a GeoParquet tile index (`RS_Envelope`) | Any GIS, GDAL, rasterio, QGIS (VRT over the index), `wherobots_gl.Map` by URL, RS_FromPath as out-db later | deflate + overviews; a 512 px 3-band float32 tile is about 1 MB; distributed write, no driver memory | The result is a product someone will open, map, or reuse as an input. Default for terrain, index rasters, masks, LISA |
| One merged COG | collect in-db tiles, paste on the driver, rasterio `COG` driver, ship bytes through the raster writer | Same as above, and simpler to open (one file) | Driver memory = whole mosaic (2 GB for a Sentinel-2 band as float32); driver-side only | Small study areas (a few hundred MB), demos, a single file for a report |
| Plain GeoTIFF | `RS_AsGeoTiff(rast)` + raster writer | Any GIS or tool | Uncompressed, tiled 256, no overviews: 3x the bytes of a COG and slow to pan | A downstream tool needs uncompressed GeoTIFF, or a tool cannot read COG overviews correctly |
| Havasu (Iceberg) table with out-db rasters | `df.writeTo("catalog.db.table").create()` on a DataFrame whose raster column is out-db | Any Sedona/Wherobots session or job through the catalog; `RS_*` SQL over tiles; spatial filter push-down on tile footprints; time travel | Only references are stored (path + window), pixels stay in the source COGs; cheap and fast to write and query | Building a queryable library of scenes or tiles that many jobs will filter and read (a raster catalogue, a time series) |
| Havasu (Iceberg) table with in-db rasters | same, after `RS_AsInDB` or from a raster-returning UDF | Sedona/Wherobots only | Pixels in parquet data files; large in-db rasters make every read a full load and risk executor OOM | Small chips (ML samples, clipped windows, 64 to 512 px) that downstream Sedona jobs read many times |
| Standalone parquet with the Sedona raster type | `df.write.parquet(path)` with a `RasterUDT` column | Sedona only (`sedona.read.parquet`); Spark type `RasterUDT`, physical binary, 1 to 2 MB per 512 px tile of Sedona's own serialization | Fast to write, no catalog; unreadable by GDAL/QGIS/pandas | Intermediate stage between two Sedona jobs in the same pipeline, never a deliverable |
| GeoParquet vectors | `write.format("geoparquet")` | Any GIS, GeoPandas, DuckDB, `wherobots_gl.Map` | Small | Zonal statistics, point samples, tile indexes, vectorized results |
| Zarr | RasterFlow outputs | RasterFlow, xarray, `wherobots_gl.Map` | Chunked, multiscale | Mosaics and model outputs inside the RasterFlow workflow |
| PNG / image | `RS_AsPNG`, `RS_AsImage` | Browsers, notebooks | Lossy for floats (integers only) | Previews and thumbnails, never analysis |

Recommendations to give with the question:

- **"I want to look at it or use it in QGIS/ArcGIS"**: COG tiles plus the index. Merge only if the
  study area is small.
- **"Other jobs will query this by area or date"**: Havasu table with out-db references. Keep
  the pixels in the source COGs; write derived rasters as COG tiles first, then register them.
- **"This is step 3 of a 5-step pipeline"**: standalone parquet with the raster type, or a
  Havasu table if the stages are separate job runs. Say explicitly that it is Sedona-only.
- **"I need the numbers per field/point"**: GeoParquet, not a raster.
- **"It feeds a model"**: in-db chips in a Havasu table at the model's chip size.

Costs to state: COG tiles are the only option that is both distributed and GIS-readable. A
merged file is driver-bound. Parquet raster columns are opaque outside Sedona (the analyst who
downloads one gets bytes). In-db tables grow with pixel count and bite on memory; out-db tables
do not, but the source files must stay where they are.

Footguns: `RS_Clip` output is single-band; nodata must be set before `RS_AsCOG` for float
rasters (`RS_SetBandNoDataValue(r, band, CAST('NaN' AS DOUBLE))`); identical file names in
sibling folders (halo vs no-halo) confuse viewers; a `part-*` folder per file is how the
distributed writer lays files out (see the follow-up below).

## Follow-up after the write: keep the writer layout or flatten into one folder? (ask)

The distributed raster writer puts every file under a `part-*` folder
(`tiles/part-00000-<uuid>-c000/n38w122_x00_y00.tif`). Spark tools do not care; people do:
browsing, `gdalbuildvrt tiles/*.tif`, QGIS drag-and-drop and plain S3 listings all want one
folder. After the analysis, offer it as a recommended extra step:

> "The tiles are written in the writer's `part-*` layout. Want them moved into one flat folder
> (`tiles/<name>.tif`) with the index updated? Recommended for anything a person will open."

- Keep the layout when only Spark reads the result back (raster reader with a trailing `/`,
  the GeoParquet index paths) or the folder is an intermediate.
- Flatten for deliverables: `flatten_written_tiles(sedona, tiles_dir)` then
  `rewrite_index_paths(sedona, index_dir, tiles_dir)` (`code_writer_layout.md`). It refuses to move
  anything when two files share a name (name tiles uniquely, by upper-left corner), never
  overwrites, is safe to rerun, and deletes the emptied `part-*` folders and `_SUCCESS` only after
  every file is in place. On S3 each move is a server-side copy + delete.
  *Measured* 2026-10-05: CONUS slope, 3,603 COGs (51.7 GB), tiny runtime, 48 threads: moved in
  99 s, 901 writer entries removed, index rewritten in 18 s, all 3,603 read back from the flat folder.
- Do not try to make the writer itself write flat: it always nests. Writing each file from Python
  (rasterio to `/vsis3/`) inside the UDF would avoid the nesting but bypasses the writer's commit
  protocol (task retries can leave duplicates or partial files); untested here.

## Decision point: where do outputs go? (ask before the first write)

Never let a default pick the location. Ask: **who needs to find this, with which identity, and
from which tool?** Then choose:

| Location | Path pattern | Who can see it | Choose when |
|----------|--------------|----------------|-------------|
| Managed storage, shared | `s3://<managed-bucket>/<org>/data/shared/<project>/<run>/` | Every member of the org, in the Cloud file browser, notebooks and jobs | Default for team-visible results and anything a human will open |
| Managed storage, the caller's private folder | the default managed upload location of a notebook or SDK upload | Only the identity that wrote it. **A service-principal API key writes to a folder no human can browse.** | Scratch for one person running their own notebook; never for a job run with a service-principal key |
| Storage integration (customer bucket) | `s3://<integration-bucket>/<prefix>/` passed explicitly, e.g. as a job's `--output-path` argument | Whoever has the bucket; outlives the Wherobots org; visible to external tools | Deliverables the customer owns, long-lived products, anything read outside Wherobots |
| Havasu (Iceberg) catalog table | `catalog.db.table` | Every org member with catalog access, by SQL | Queryable raster or vector libraries (see the output-format decision) |
| Local or notebook filesystem | `/tmp`, `/home/...` | Nobody after the runtime stops | Never for results |

Rules that follow:

- Job scripts take `--output-path` and fail if it is missing; they do not fall back to the
  caller's private folder. Orchestrators pass an explicit shared or integration prefix.
- When the API key belongs to a service principal, its private folder is one no human can
  browse: write under shared managed storage or an integration, and say so in the run log.
- Print every output URI in the job log and in the manifest; a human should never have to
  guess a `part-*` folder.
- Keep the layout `<project>/<run-stamp>/<scenario>/` so reruns never overwrite each other.

## Naming convention for tiled raster outputs

Grid indices like `x07_y12` only mean something relative to one tile grid and one run. Name
tiles by what they are and where they are, so a file is unambiguous after it has been
downloaded, mixed with another product, or re-tiled:

```text
<product>_<variant>_<crs>_<cell>_<ulx>_<uly>.tif
slope_halo_e26910_10m_x0730680_y4069320.tif          projected CRS: UL corner in CRS units
slope_halo_e4269_1as3_w122p0400_n37p9500.tif         geographic CRS: UL corner, p = decimal point
ndvi_s2b10sgf20250619_e32610_10m_x0699960_y4100040.tif
```

Rules:

- `product`: what the pixels are (`slope`, `aspect`, `ndvi`, `lisa`), lowercase, no spaces.
- `variant`: the method or input that distinguishes otherwise identical products
  (`halo`, `nohalo`, `diff`, a scene id, a date). Never leave two variants with the same name
  in sibling folders; the distinction belongs in the file name, not the folder.
- `crs`: `e<EPSG>`; `cell`: the cell size with unit (`10m`, `30m`, `1as3` for 1/3 arc-second).
- `ulx`, `uly`: the tile's upper-left corner from `RS_UpperLeftX` / `RS_UpperLeftY`, zero-padded
  integers in a projected CRS, or `w|e<deg>p<4 decimals>` / `n|s<deg>p<4 decimals>` in a
  geographic CRS. Two tiles of the same product and grid can never collide, and sorting the
  names sorts the tiles spatially.
- Keep `x`, `y` grid indices in the tile index, not in the name, unless the grid is a published
  one (MGRS, H3, quadkey), in which case use that id instead of the corner.
- Folder layout: `<location>/<project>/<run-stamp>/<product>/tiles/part-*/<name>.tif` plus
  `<product>/tiles_index.parquet`. One product per folder, one run per stamp; never overwrite a
  stamp. The `part-*` nesting is how the distributed writer works and cannot be turned off, so
  the index (footprint, SRID, size, bands, file name, full S3 path) is the authoritative map.
  Build the full path in the index by listing the folder with `binaryFile` after the write.
- Put band meaning inside the file as well: `RS_AsCOG` does not write band descriptions, so
  record `bands` in the index (`slope_deg;aspect_deg;hillshade`) and in a sidecar `manifest.json`
  with CRS, cell size, nodata, kernel, halo width, source, and run stamp.
- One extension, `.tif`, for GeoTIFF and COG alike; the writer's `fileExtension` option sets it.

SQL expression for the name column (projected CRS; `tile_name_expr` in `code_writer_layout.md`
builds the projected and geographic forms and is what was validated):

```sql
CONCAT('slope_halo_e', RS_SRID(r), '_10m',
       '_x', LPAD(CAST(CAST(RS_UpperLeftX(r) AS BIGINT) AS STRING), 7, '0'),
       '_y', LPAD(CAST(CAST(RS_UpperLeftY(r) AS BIGINT) AS STRING), 7, '0')) AS path
```

Limits of the projected form: `LPAD(..., 7)` fits UTM eastings and northern-hemisphere
northings; Spark's `LPAD` *truncates* longer values, so southern UTM northings (8 digits),
EPSG:3857 and negative coordinates (Albers west of the central meridian) need a wider field and
a sign token. *Untested*.

Validated on the cluster (2026-10-05, `tiny` runtime): `tile_name_expr` gives `slope_halo_e32610_30m_x0772290_y4057620`
for a UTM tile, `slope_halo_e4269_30m_w121p9800_n37p9600` and `slope_halo_e4326_30m_e018p0000_s33p9000`
for geographic corners in all four hemisphere combinations, and
`slope_halo_e4326_30m_w120p0001_n36p6446` for a real Copernicus GLO-30 tile from
`wherobots_open_data.copernicus_dem.glo_30m` (UL corner -120.000139, 36.644583).
