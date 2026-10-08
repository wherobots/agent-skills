---
name: wherobots-raster
description: Use for raster analysis on WherobotsDB - tiling, out-db vs in-db reads, Python raster UDFs (sedona_vectorized_udf), NDVI and other indexes, zonal stats, point sampling, slope/hillshade/TPI with tile halos, resampling and CRS choices, COG export. Sedona RS_ functions, not GDAL.
---

# Raster analysis on WherobotsDB

Best practice for raster work on Wherobots, for any source the user brings: inspect the source,
pick the pattern by what the question needs, read only the pixels you need, keep tiled focal
operations seam-free, and verify the result with a check that can fail. Named datasets appear
only as worked examples.

> Validated on Wherobots Cloud (Spark 4.1.3, `small`/`tiny` runtimes) job runs **2026-09-26 to
> 2026-10-07**, plus local unit tests of the reference code (2026-10-08). Examples were measured
> on Sentinel-2 L2A, Copernicus GLO-30, USGS 3DEP 1/3 arc-second and NAIP; they illustrate the
> rules and are not the scope. Anything not measured is marked *untested*. Re-check on runtime
> upgrades.

**Read the reference file the task needs, in full:**

| File | When |
|------|------|
| [`references/decisions_resampling.md`](references/decisions_resampling.md) | mixed resolutions, small polygons, geographic CRS, unknown units: what to ask and the default to use |
| [`references/loading_units_patterns.md`](references/loading_units_patterns.md) | entry points and formats, rescale defaults, the units check, core tile/zonal/export patterns, out-db cast check, `RS_MapAlgebra` traps |
| [`references/tile_vs_zonal.md`](references/tile_vs_zonal.md) | full-tile vs per-polygon vs per-point work; measured costs; zonal reliability flags |
| [`references/edge_effects.md`](references/edge_effects.md) | any focal/neighbourhood operation or scene-wide statistic on tiles: halo widths, cross-file halos, two-pass, verification with a negative control |
| [`references/recipes_terrain.md`](references/recipes_terrain.md) | the generic focal pipeline template; slope, aspect, hillshade, TRI, TPI, landforms, roughness, curvature |
| [`references/recipes_indexes.md`](references/recipes_indexes.md) | spectral indexes (NDVI ... CIre), cloud masks, the units check per target type |
| [`references/export_and_render.md`](references/export_and_render.md) | output format and location decisions, COG export, tile naming, index and manifest, reading back, rendering |
| [`references/code_terrain_focal.md`](references/code_terrain_focal.md) | numpy/scipy terrain and focal functions |
| [`references/code_indexes.md`](references/code_indexes.md) | numpy spectral index, mask and in-memory COG functions |
| [`references/code_sedona_udfs.md`](references/code_sedona_udfs.md) | `@sedona_vectorized_udf` terrain wrappers, halo reader, neighbour-file halo, empty template |
| [`references/code_index_udfs.md`](references/code_index_udfs.md) | spectral index UDFs (raster out, scalar out, per polygon) |
| [`references/code_writer_layout.md`](references/code_writer_layout.md) | tile naming expression, flattening the writer's `part-*` folders, index path rewrite |
| [`references/code_pipeline.md`](references/code_pipeline.md) | file selection for an AOI, focal pipeline, tile index, manifest, single-pass check, job driver skeleton |

The `code_*` files are one Python module split by topic. Concatenate their `python` blocks in the
order of the table above (terrain_focal, indexes, sedona_udfs, index_udfs, writer_layout,
pipeline) into one notebook cell or above the driver of a job run.

## 1. Mental model: a raster is a reference until something asks for pixels

| Kind | The row holds | Produced by |
|------|---------------|-------------|
| **out-db** | path + pixel window + band list + georeferencing | `RS_FromPath`, raster reader, STAC reader, `RS_TileExplode`, `RS_Clip` with a rectangle in the raster CRS |
| **in-db** | the pixel array | `RS_AsInDB`, `RS_StackTileExplode`, polygon `RS_Clip`, `RS_Band`, `RS_Resample`, `RS_MapAlgebra`, `RS_FromGeoTiff`, any raster-returning UDF |

Out-db reads fetch only the COG blocks under the requested window. **Anything that produces an
in-db raster reads its whole input extent.** Tile first, then transform.

| Reads nothing | Header only | A window | Whole coverage |
|---------------|-------------|----------|----------------|
| `RS_FromPath`, `RS_TileExplode`, `RS_Envelope`, `RS_Intersects`, `RS_Width`, `RS_NumBands`, `RS_SRID`, rectangle `RS_Clip` (same SRID, default crop, no nodata arg) | `RS_MetaData` | `RS_ZonalStats*` on a small zone, `RS_Value(s)` (blocks under the points), a UDF on an **out-db** tile (rasterio window read) | polygon `RS_Clip` or any CRS mismatch, `RS_SummaryStats*`, `RS_Band`, `RS_Resample`, `RS_AsInDB`, `RS_StackTileExplode` (per output tile), `RS_Union_Aggr`, `RS_AsGeoTiff`/`RS_AsCOG`, `RS_MapAlgebra` |

Measured on one Sentinel-2 scene: polygon clip in EPSG:4326 against the UTM scene 14.7 s and
120.6 M pixels; rectangle clip in the scene CRS then polygon clip 0.12 s and 10.8 k pixels. Same
statistics.

## 2. The Python raster UDF

```python
@sedona_vectorized_udf(return_type=RasterType())
def ndvi_outdb_udf(template: SedonaRaster, red_outdb: SedonaRaster, nir_outdb: SedonaRaster) -> SedonaRaster:
    out = ...                                        # numpy from red_outdb.as_numpy(), nir_outdb.as_numpy()
    return template.with_bands(out[np.newaxis])      # CHW, any band count/dtype, same H x W

tiles.selectExpr(empty_template_sql("red"), "red", "nir").select(ndvi_outdb_udf(F.col("t"), F.col("red"), F.col("nir")))
```

- Any mix of raster (`SedonaRaster`), geometry (`BaseGeometry`) and scalar arguments
  (`F.lit(...)`). Annotate or they arrive as bytes. Column API only, not `spark.udf.register`.
- **Out-db argument** = cheap: the JVM ships a reference, Python reads the window through
  rasterio. Values are **raw DN** (file scale/offset not applied). **In-db argument** = the JVM
  reads, serialises to Arrow, Python decodes.
- **Raster out needs a template** for `with_bands()` (there is no `from_numpy`). Use the empty
  template (`empty_template_sql`): it reads no pixels. *Measured* 2026-10-05: about 40 % less wall
  time than `RS_AsInDB(tile)` in paired runs, identical output.
- `as_numpy()` (bands, H, W); `as_numpy_masked()` turns per-band nodata into NaN;
  `bands_meta[i].nodata`, `affine_trans`, `crs_wkt`, `width`, `height`, `path`.
- First call per cluster costs 15 to 20 s. Runtime has numpy, scipy, rasterio, scikit-learn.
- Requester-pays buckets: SQL works through a storage integration; Python needs
  `open_source(path, requester_pays=True)`.
- Use the vectorized decorator over the legacy row `udf`: on 1,849 small in-db tiles 1.4 s vs
  3.5 s, identical results (measured 2026-09-30).

## 3. Decision trees

Branches are by **property of the question and the source**; numbers are measured examples.

### Which pattern?

```text
What do you need?
├─ a number per POLYGON ................. see "Zonal decision" below
├─ a value per POINT .................... RS_Value / RS_Values on the out-db raster, points in the raster CRS
├─ a NUMBER for a whole area ............ scalar UDF on out-db band tiles -> [sum, count] -> aggregate
├─ a new RASTER per pixel (index, mask) . empty template + bands out-db -> raster UDF
├─ a new RASTER from a NEIGHBOURHOOD .... same + halo read; generic focal pipeline (recipes_terrain.md)
├─ a scene-wide STATISTIC (Moran, Gi*) .. two passes: [sum, count] then halo partial sums (edge_effects.md)
└─ a persisted multi-band STACK ......... RS_StackTileExplode(ARRAY(...), refIdx, w, h); otherwise never stack
```

### Zonal decision

```text
What is the per-polygon statistic?
├─ single band, built-in stat (mean, sum, count, min, max)
│     RS_ZonalStatsAll(RS_Clip(tile, band, ST_Envelope(geom_r)), geom_r, band) per (polygon, tile),
│     then sum(sum) / sum(count) per polygon
├─ ratio or index (NDVI, NDMI, any (a-b)/(a+b))
│     index FIRST, per pixel, on the bbox windows of each band (ndvi_sum_count_in_polygon_udf),
│     then sum(sum) / sum(count). Never the index of the band means.
└─ who reads the windows?  rows x window size decides, not the dataset:
      few hundred pairs, windows ~100+ px ... Python reads out-db windows (211 pairs: 17.6 s)
      thousands of pairs, small windows ...... JVM reads: RS_AsInDB(window) into the UDF
                                               (1,398 pairs: 6.5 s vs 233 s Python per-row opens)
Always: carry n_px and reliable = n_px >= MIN_PX (default 10), and record the boundary rule.
```

### Which grid? (details in decisions_resampling.md)

```text
Bands at different resolutions?
├─ objects small relative to the coarse cell ... finest grid, nearest-neighbour up
│     (16 fields < 5 acres: 93 px at 10 m vs 22 at 20 m; NDMI differed by up to 0.10)
├─ objects large and the coarse band carries the signal ... coarse grid (4x fewer pixels)
└─ unsure ...................................... ask; unattended: finest grid, state it in the header
Geographic CRS (degrees)?
└─ metric cell sizes per row from the latitude, dx and dy separately, or reproject first
```

### Focal operation on tiles?

```text
Kernel radius r (3x3 -> 1, 5x5 -> 2, TPI radius r -> r, chained kernels -> sum of radii)
└─ read window + r ring from the SOURCE file through the out-db tile; compute; trim
   (measured, no halo: seams up to 19 deg of slope; mean tile slope off by 0.68 deg on 256-px tiles)
Source split over many files that share one grid?
└─ neighbour-file halo: with_neighbour_files + *_nb_udf; fill the ring with NaN, never the file nodata
   (measured: per-file halo on a source with no nodata tag -> 82.6 deg fake cliffs; neighbour halo -> 0)
Files on different grids? -> resample to one grid or build a mosaic first (untested), then the above
```

## 4. Know your source (inspect before choosing a pipeline)

Run the probe in [`references/loading_units_patterns.md`](references/loading_units_patterns.md)
("Probe a source") on one file of any table, bucket or path, then answer:

| Check | Why it matters | Example |
|-------|----------------|---------|
| One file or many; the naming/grid rule | many files on one grid need neighbour-file halos; the rule drives `files_for_aoi` | Copernicus GLO-30 names each 1-degree file by its SW corner |
| Internal block size (`RS_MetaData` tile width/height) | tile at it, or a multiple of it | Sentinel-2 COGs 1024 px; striped files (10812 x 1) work but tile explode is 14x slower |
| Nodata tag present? | if absent, choose nodata explicitly; never fill with an absent tag | Copernicus has none: a 0-fill makes sea-level cliffs |
| Scale/offset tags; what SQL vs UDF reads return | `RS_FromPath` applies tags, out-db UDF reads do not | `sentinel-cogs` files: no tags, DN / 10000 with no offset |
| Pixel registration (UL corner vs grid lines) | tile names and seam masks follow the real corner | GLO-30 UL at -122.000139, not -122 |
| CRS / SRID (0 = unrecognised) | transform vectors into it; SRID 0 silently assumes WGS84 | NLCD Albers resolved to SRID 0 |
| What the values represent | surface vs bare earth, DN vs reflectance, class codes (nearest-only) | GLO-30 and ASTER are surface models; bare earth: USGS 3DEP, GEDTM30, FABDEM |
| An out-db catalog table exists? | use it as tiles if its tiling suits the job, else as a **file index** (filter, `RS_BandPath`, re-tile) | `copernicus_dem.glo_30m` is 256-px tiles with 16-px slivers: as a file index, 2048-px re-tile ran 6.4x faster |

The format the reader cannot open (JPEG2000, HDF, Zarr, GeoPackage rasters): convert to COG first.

## 5. Top rules

1. Inspect the source (section 4), tile at the block size, footprint-filter with
   `RS_Intersects`, only then touch pixels.
2. Never pass an untiled scene to a UDF, `RS_AsInDB`, a stack or MapAlgebra.
3. Window before polygon: rectangle `RS_Clip` in the raster CRS, or let `RS_ZonalStats` do it
   (4 to 100x faster measured; 2.9 GB executor peak instead of 8.9 to 15.7 GB).
4. Raster out: empty template; read pixels out-db in the UDF. Metadata on the JVM
   (`RS_BandPath` 0.5 s vs a Python UDF 6.2 s on 1584 rows).
5. Focal kernels need a halo of the kernel radius from the source (and from neighbour files when
   the source is split); scene-wide statistics need two passes.
6. **Every verification has a negative control**: show the check fails on a known-bad variant
   (no halo, per-file halo, wrong offset) before trusting that it passes.
7. Choose the grid by the size of the objects measured, not by cost; state it in the header.
8. Verify units on known targets chosen by what is on the ground (`recipes_indexes.md`); set
   nodata on every raster you write: `RS_SetBandNoDataValue(r, 1, CAST('NaN' AS DOUBLE))` (the
   2-argument form sets band 1 too; verified 2026-10-08).
9. Check SRIDs on both sides (`RS_SRID`, `ST_SRID`); `ST_SetSRID` then `ST_Transform` into the
   raster CRS; a mismatch returns false silently.
10. `RS_Clip` output has one band; clip bands separately.
11. Ratio indexes per polygon: index first, then aggregate (the zonal decision above).
12. Aspect is an angle: aggregate with a circular mean (sin/cos).
13. Force work with `count(col)` or `collect()`; `df.count()` prunes UDFs. Persist a computed
    raster before writing it and indexing it, or the UDF runs twice.
14. Sort by source path; `spark.wherobots.raster.outdb.readahead=4m` for whole-tile reads, 64 KB
    default for point sampling.
15. Name tiles from their upper-left corner (`tile_name_expr`), one product per folder, one run
    per stamp, and write a tile index and manifest beside them.

## 6. Running this as a job

Skeleton in `code_pipeline.md` ("Driver skeleton"): `SedonaContext`, argparse with a **required**
`--output-path`, every output URI printed, parameters in one block. Build one script: the
`code_*` blocks in table order, then the driver. Upload it to a **shared** location (shared
managed storage or a storage integration) and submit it with the CLI or SDK, covered by the
`wherobots-develop` skill in this repo (`npx skills add wherobots/agent-skills@wherobots-develop`).
The SDK's managed-upload default is the caller's private folder: with a service-principal key
nobody can browse it.

## Decisions (ask interactively; unattended jobs use the default and record it)

Each changes the numbers or who can use them. Record the choice in the pipeline header **and** the
output manifest (`write_manifest`).

| Decision | Ask | Unattended default |
|----------|-----|--------------------|
| Zonal boundary rule (`decisions_resampling.md` item 8) | centroid-in or all-touched? | centroid-in; output carries `n_px`, `reliable`, `boundary_rule` |
| Grid for mixed resolutions | which band drives the answer; smallest object? | finest signal-bearing grid, nearest neighbour |
| Output format (`export_and_render.md`) | who reads it next, how many times? | COG tiles + GeoParquet index + manifest; GeoParquet for per-polygon numbers |
| Output location | who must find it, with which identity? | the job's required `--output-path` (shared or integration); fail if missing |
| Writer layout after a tiled write | Spark-only consumers, or people? | keep `part-*` for intermediates; flatten deliverables (`flatten_written_tiles`) |

## Sibling skills

- `wherobots-develop` — submitting and monitoring the job runs these patterns run in.
- `wherobots-open-data-catalog` — what raster tables `wherobots_open_data` holds (examples here).
- `wherobots-pipeline-designer` — where raster stages sit in a Bronze/Silver/Gold pipeline.
