# Window and edge effects in tiled raster analysis

WherobotsDB has no `RS_Slope`, `RS_Aspect`, `RS_Hillshade` or any focal function. Focal
work runs in a Python raster UDF one tile at a time, and a tile is a hard edge the kernel
cannot see across. This file is about making per-tile results identical to a single-pass
computation over the whole scene. Numbers marked *measured* come from
Wherobots Cloud job runs (tiled terrain export 2026-09-30, tiled terrain and LISA runs
2026-09-26, cross-file halo comparison 2026-10-05) and a local single-pass seam re-check (2026-09-30).

## Why tiling breaks focal operations

A 3x3 kernel at a tile's outer ring needs one row or column that lives in the neighbouring
tile. Whatever the kernel does there instead (edge replication, zero fill, NaN) is wrong.
*Measured* on USGS 1/3 arc-second DEM, Mount Diablo, 512-px tiles, Horn slope:

| Check | No halo | 1-cell halo |
|-------|---------|-------------|
| Max slope difference vs the halo result, on tile seams (2 rows/cols either side) | 19.15 deg (25 tiles); 18.48 deg (9 tiles) | 0 |
| Max difference away from seams | 0.0 deg | 0 |
| Mean difference on seam cells | 3.33 deg | 0 |
| Max hillshade difference on seams (0-255) | 69.2 | 0 |
| Cells with any difference | 40,995 of 4,682,887 (0.9 %) | 0 |
| Mean tile slope bias, Copernicus 30 m, 256-px tiles (15 tiles) | 0.68 deg per tile | 0 |
| Per-row max vs single-pass ground truth, rows around one seam (local re-check) | 6.96 to 17.87 deg | 0.006 to 0.011 deg (float32 rounding) |
| Merged COG vs single-pass ground truth, whole area | max 19.15, mean 0.031 deg | max 0.036, mean 0.0044 deg |
| Cluster validation job, 4 tiles of 512 px, tiled halo UDF vs single pass on the driver (1.03 M cells) | - | slope max 0.009 deg, on seams 0.009 and off seams 0.009 (float32 rounding); TRI/roughness/TPI exactly 0 |

0.9 % of cells is small, but every one of them sits on a visible seam, and per-tile means
(the thing a dashboard shows) are biased by 0.68 degrees on 256-px tiles; the bias grows as
tiles shrink because the edge ring is a larger share of the tile.

## The halo pattern

Read the tile window **plus a ring of `pad` cells** straight from the source COG, compute on
the padded array, trim the ring, return the tile's own cells.

```python
@sedona_vectorized_udf(return_type=RasterType())
def slope_halo_sketch_udf(template: SedonaRaster, outdb: SedonaRaster) -> SedonaRaster:   # sketch; real UDFs in code_sedona_udfs.md
    pad = 1
    z = read_with_halo(outdb, pad)            # (H + 2, W + 2) float32, nodata -> NaN
    dx, dy = cell_size_m_of(template)
    s = slope(z, dx, dy)[pad:-pad, pad:-pad]  # trim
    return template.with_bands(s[np.newaxis])

tiles.selectExpr("x", "y", "RS_AsInDB(tile) AS t", "tile") \
     .select("x", "y", slope_halo_sketch_udf(F.col("t"), F.col("tile")).alias("terrain"))
```

Pass the tile **twice**: `RS_AsInDB(tile)` is the georeferencing template for `with_bands()`
(there is no `from_numpy`), and the out-db `tile` carries the path, band index and pixel
window that `read_with_halo` turns into a rasterio `Window(col0 - pad, row0 - pad,
W + 2 pad, H + 2 pad)` read with `boundless=True`. The extra ring is a few hundred cells: the
halo variant ran in 8.95 s vs 17.15 s without it in the 2026-09-26 terrain run (the no-halo run
paid the first-UDF-call warm-up), so the halo read is not the cost. `read_with_halo` and
the UDFs are in `code_sedona_udfs.md`.

Only the **out-db** argument can supply a halo. If the JVM already materialised the tile
(`RS_AsInDB`, `RS_StackTileExplode`, polygon `RS_Clip`) the neighbours are gone; keep the
out-db column alongside.

## Halo width per operation

| Operation | Kernel | Halo cells (`pad`) |
|-----------|--------|--------------------|
| Slope, aspect, hillshade (Horn), curvature (Zevenbergen-Thorne, Evans), TRI, roughness 3x3 | 3x3 | 1 |
| Any 5x5 kernel (Gaussian smoothing, 5x5 focal mean) | 5x5 | 2 |
| n x n focal statistic | n x n | n // 2 |
| TPI at radius r (disk), annulus TPI with outer radius r | (2r+1)^2 | r |
| Two-scale landform classification (TPI at r1 and r2) | | max(r1, r2) |
| Hillshade on a smoothed DEM (smooth k x k, then 3x3) | chained | k // 2 + 1 |
| Chained kernels in general | | sum of the radii |
| Queen-contiguity lag (LISA, Gi*, Geary, join counts) | 3x3 | 1 |
| Distance-band weights of d cells | | d |

Rule: halo = the radius of the largest neighbourhood any output cell depends on, summed over
chained steps. Over-padding costs only I/O; under-padding costs correctness silently.

## Scene-wide statistics need two passes

Anything that centres on a mean or divides by a scene-wide sum (Moran's I, LISA, Geary's C,
Gi*, standardised TPI for landform classes, z-scores) cannot be computed per tile and
averaged. Two failure modes (*measured* locally, NDVI 1024 x 1024 cut into 16 tiles):

| Mistake | Effect |
|---------|--------|
| No halo | edge cells see 3 or 5 neighbours instead of 8: lag biased low on every seam |
| Tile-local mean | a uniformly green tile shows no clusters, a half-green tile shows a fake HH/LL split; max abs error in local I of 1.96e4 (values range to about 250) |

The decomposition:

1. **Pass 1**: scalar UDF returns `[sum, count]` per tile (no halo needed); Spark sums them
   into the scene mean `m` and count `N`.
2. **Pass 2**: with the halo, `z = value - m` on the padded array, the stencil lag, and per
   tile the partial sums for the tile's own cells: `num = sum(z_i * lag_i)`, `den = sum(z_i^2)`,
   `s0 = valid neighbour pairs`. Global Moran's I = `(N / S0) * num / den`. Exact.
3. **Pass 3** (raster out): local `I_i = (N - 1) z_i lag_i / den` and the quadrant from the
   signs of `z_i` and `lag_i`, with `RS_AsInDB(tile)` as the template.

*Measured*: recombined 16 halo tiles match whole-array `esda.Moran` and `Moran_Local` to
5.7e-14 (float64). On the cluster, 106 tiles of 512 px: pass 1 15 s, pass 2 29 s (scene
I = 0.954), pass 3 32 s; naive per-tile esda 159 s and statistically wrong. The same
decomposition holds for Geary's C (sums of squared differences), Gi* (global sums and sums
of squares, local lag) and row-standardised weights (carry the neighbour count out of the
halo). Permutation p-values are the one thing that does not decompose; run them on a
shortlist of tiles. Code for the landform case: `tpi_sum_sumsq_count_udf` and `tpi_moments_nb_udf` in
`code_sedona_udfs.md`; the Moran/LISA passes follow the same shape (pass 1 is
`ndvi_sum_count_udf`).

## Merging tiles back

With the halo, every tile already holds the exact value up to its own edge, so the merge is a
paste by pixel offset: cumulative tile widths give the column offset, cumulative heights the
row offset, the top-left tile's geotransform becomes the mosaic's. No feathering, no overlap
averaging, no seam. *Measured*: the merged halo COG differs from a single-pass computation
over the whole clip by at most 0.036 deg (mean 0.0044 deg), which is float32 rounding of the
Horn gradient. Doing the paste on the driver works for small areas (1297 x 1513 and 2167 x 2161 cells); at scene scale
keep the tiles and write a tile index instead (see `export_and_render.md`).

Watch the last row/column of tiles: `RS_TileExplode` produces narrower tiles there unless
`padWithNoData` is set (740 px wide on a 10980-px Sentinel-2 band). Offsets must come from
the actual tile widths, not from `x * TILE`.

## The study-area boundary

`read_with_halo(...)` fills the ring outside the *file* with NaN (never with the file's nodata
value; see the cross-file section for why). Three cases:

- Tile at the file edge: the ring is nodata, the outer row/column of the result is NaN (or
  edge-replicated if you fill with the edge value). That is the correct answer: there is no
  terrain beyond the file.
- Tile at the edge of a **rectangle clip** inside a larger file (the usual case: an AOI cut
  from the USGS 3DEP 1/3 arc-second tile `n38w122`): the ring is read from the file beyond the clip, so the AOI edge
  is computed exactly. The out-db tile keeps the source path, so this happens automatically.
- Tile at the edge of a **file** in a multi-file DEM or mosaic (Copernicus GLO-30 1-degree COGs,
  3DEP 1x1 tiles, NAIP quads, Sentinel-2 granules on one grid): the neighbouring cells live in a
  different file. Nothing in WherobotsDB crosses that boundary: each out-db tile references one
  file, `RS_TileExplode`/the raster reader have no overlap option, a tile window can only run past
  the right/bottom edge (nodata-padded on the JVM), and there is no mosaic function (`RS_Union*`
  stack bands). Use the **neighbour-file halo** (section below).

## Cross-file halo: neighbour files from the reader's own catalogue

Requirement: the neighbour files must share pixel size **and** alignment (same grid).
`_halo_window` raises on a pixel-size mismatch but rounds a sub-pixel origin shift silently, so
check alignment yourself (the probe's `ul_frac_px` must agree across files). Files on different grids (mixed resolutions, a
different UTM zone, shifted origins): resample them onto one grid or build a mosaic first, then
halo (*untested*). Pixel registration varies by source (Copernicus GLO-30 origins sit half a
pixel off the degree lines: UL -122.000139, not -122); everything here works from each file's
geotransform, so tile names and seam masks follow the real corner.

The measured example below is a Copernicus 4-file corner; the method is generic.

Load the whole prefix with the raster reader (glob; missing all-ocean cells are skipped), then a
spatial self-join of tile envelopes gives each tile the other files its padded window touches.
The UDF reads its own file with NaN fill, then only the overlapping strip of each neighbour.
No VRT, no second catalogue; tiles in a file's interior read exactly as before.

```python
tiles = sedona.read.format("raster").option("tileWidth", "1024").option("tileHeight", "1024") \
    .load("s3a://copernicus-dem-30m/Copernicus_DSM_COG_10_N3[78]_00_W12[23]_00_DEM/*.tif")
tiles = with_neighbour_files(tiles, pad_deg=2 / 3600)          # adds path, neighbours (';'-joined)
out = tiles.selectExpr("RS_AsInDB(rast) AS t", "rast", "neighbours") \
           .select(slope_nb_udf(F.col("t"), F.col("rast"), F.col("neighbours")))
```

*Measured* 2026-10-05, Copernicus GLO-30, 4-file corner at (-122, 38), 3600 x 3600 cells vs a
single pass over a `rasterio.merge` mosaic:

| Variant | File seams (36k cells) | Elsewhere | 16 tiles |
|---------|------------------------|-----------|----------|
| per-file halo, `fill_value=src.nodata` (Copernicus has **no nodata tag** -> 0 m) | max **82.6 deg**, mean 11.2 deg | 0 | 28.9 s |
| per-file halo, NaN fill | exact where valid, **14,388 cells NaN** | 0 | 27.5 s |
| neighbour-file halo | **0.0000** | 0 | 39.2 s |

Same result on written COG tiles (4 files, 60 tiles; seam check on
the written files: max 0.0). Gotchas found on the way:

- **Files without a nodata tag**: a boundless read with `fill_value=src.nodata` fills with 0, a
  cliff to sea level on every file edge. Always fill with NaN.
- Raster-reader `x`/`y` are **per file** (0..3 for 3600-px files at 1024 px; last tile 528 px).
  Name output tiles by their upper-left corner (`tile_name_expr`, `export_and_render.md`) and place
  them by geotransform, never `x * TILE`.
- All-ocean 1-degree cells have no file: an explicit path list fails with PATH_NOT_FOUND; a glob
  skips them. The coast then has a correct NaN ring (no terrain beyond the data).
- `wherobots_open_data.copernicus_dem.glo_30m` points at the same files at 256 px plus 16-px
  slivers (3600 = 14 x 256 + 16). Use it as the **file index**, not as the tiles: filter by `name`,
  take `RS_BandPath`, `RS_TileExplode(RS_FromPath(path), 2048, 2048)`. *Measured* 2026-10-05, 23
  files, 298 M cells, identical output: halo slope on the stored 256-px tiles 164 s, on the
  re-tiled 2048-px tiles 25 s; file lookup by name 2-4 s vs 26-27 s for a bucket glob.
- Geographic DEMs: use per-row cell sizes (`row_cell_sizes`); per-tile-centre dx makes tiled
  output differ from a single pass by the dx drift across the tile.

Every merged raster therefore has a valid outer ring only where the source
file continued beyond the AOI; the seam checks exclude the outer ring for that reason.

## How to verify (always with a negative control)

A check that cannot fail proves nothing: on flat terrain, or a subset that misses the seams, a
broken pipeline also scores 0. Every verification therefore runs two comparisons on the **same**
subset and passes only if both hold:

- the pipeline output matches a single pass over the merged source (within float32 rounding:
  about 1e-2 deg for slope; stencils without trigonometry match exactly);
- a known-bad variant (no halo, or per-file halo) shows a **non-zero** error on the seams.

`single_pass_check` in `code_pipeline.md` does both. It stitches the source files by their own
geotransforms (so half-pixel origins need no special handling), computes the single pass on the
driver, reads the written tiles back, builds the seam mask from the file edges, and computes the
per-file control. Pick a subset that straddles the seams **and has relief**; the function reports
`check_discriminates = False` when the control cannot fail there.

*Measured example:* per-file halo with 0-fill on a source without a nodata tag gave 82.6 deg fake
cliffs on the file seams; the neighbour halo gave 0.0 (2026-10-05). Unit test on synthetic
2 x 2 files (2026-10-08): correct output 0.0 vs single pass, per-file control 12.5 deg on seams,
per-file output FAILS, flat surface reports `check_discriminates = False`.

Further checks:

1. **Per-row profile.** Print the max difference for the eight rows around a seam: a halo bug
   shows as a spike on exactly the seam rows.
2. **Diff raster.** Write `|halo - nohalo|` as its own tile set; anything non-zero away from
   seams is a bug in the halo read (wrong `col0/row0` rounding, wrong band index).
3. **Statistics**: compare a two-pass global statistic against `esda` on the collected subset;
   expect agreement to 1e-10 or better in float64.
