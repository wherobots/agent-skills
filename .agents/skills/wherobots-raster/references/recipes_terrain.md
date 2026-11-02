# Terrain and focal recipes (Python raster UDF with a shared halo reader)

All code is in `code_terrain_focal.md` (pure numpy/scipy functions, unit-tested locally on
synthetic surfaces, 14 checks) and `code_sedona_udfs.md` (`@sedona_vectorized_udf` wrappers that
exist only when the Sedona runtime imports). Job runs upload one script: paste the `code_*` blocks in the order SKILL.md gives, then
your driver.

Conventions shared by every recipe:

- Input `z`: 2-D float32 elevation in **metres**, nodata already NaN (`read_with_halo` does both).
- `dx, dy`: cell sizes in **metres**. Geographic DEMs (USGS 3DEP EPSG:4269, Copernicus
  EPSG:4326) must go through `cell_sizes_rows_of` (per row, exact; the `*_nb_udf` UDFs) or
  `cell_size_m_of(template)` (tile-centre latitude, small error that grows with tile height; 8.1 x
  10.3 m for 1/3 arc-second at 37.9 N) or be reprojected first (`RS_Resample` /
  `RS_ReprojectMatch`, whole-coverage read).
- Row 0 is north. Gradients follow the ESRI/GDAL Horn convention (`dzdx` = east - west,
  `dzdy` = south - north, each over 8 x cell size). Aspect is the compass bearing of the
  downslope direction. Tested: a plane rising east reports aspect 270, rising north 180.
  Use `ndimage.correlate`, not `convolve`: `convolve` flips the kernel and mirrors aspect
  east-west while slope stays correct, so the bug survives a slope-only check.
- Nodata: a 3x3 kernel over NaN yields a NaN ring one cell wide around each nodata cell (the
  GDAL default). `tpi`, `focal_stat`, `roughness` ignore NaN inside the window instead.
- Halo: the number of cells `read_with_halo` must pad on every side (`HALO` dict in the module).

## Generic focal pipeline (fastest measured, exact)

For any focal or terrain product from one file or many files sharing one grid. Slope is the
worked example; the code is `code_pipeline.md` (`files_for_aoi`, `focal_tiles`, `run_focal`,
`build_tile_index`, `write_manifest`, `single_pass_check`) and its "Driver skeleton" runs as
pasted. The steps:

1. **Parameters in one block**: source (`files_for_aoi` kind: catalog names, catalog footprint,
   glob, or path list), AOI, `TILE_PX`, operation, product/variant/cell for names, output path.
2. **Select files** with `files_for_aoi`. It asserts a non-empty result: a name filter that
   matches nothing returns 0 rows silently. Naming rules are source-specific
   (`copernicus_glo30_names` is the example: files named by their SW corner, names include `.tif`).
   Pad the AOI by the halo before deriving names, so an AOI edge on a file line pulls the neighbour.
3. **Tile** with `focal_tiles`: out-db tiles at `TILE_PX`, neighbour files for the halo,
   partitions from the tile count (computed, not assumed).
4. **Compute and write** with `run_focal`: empty template + the operation's UDF, nodata set,
   names from `tile_name_expr`, **persisted** before the write so the UDF runs once.
5. **Index from the written files** with `build_tile_index` (full paths, footprints, valid
   counts; reading every tile back is also the "opens" check), then `write_manifest`.
6. **Verify** with `single_pass_check` on a seam-straddling subset with relief: PASS needs the
   output to match a single pass **and** the per-file negative control to fail on the seams.

What changes per operation:

| Operation | UDF (code_sedona_udfs.md) | Halo | Output | Nodata |
|-----------|---------------------------|------|--------|--------|
| slope | `slope_nb_udf` | 1 | float32, degrees | NaN |
| slope + aspect + hillshade | `terrain_nb_udf` | 1 | 3 x float32 | NaN |
| TPI radius r | `tpi_udf(t, tile, F.lit(r))` (single file) | r | float32, metres | NaN |
| focal mean n x n | `focal_mean_udf(t, tile, F.lit(n))` (single file) | n // 2 | float32 | NaN |
| landforms (two-pass) | `tpi_moments_nb_udf` then `landform_nb_udf` | max(r1, r2) | uint8 classes | 0 |

TPI and focal mean have only single-file wrappers: for a source split over files, copy
`slope_nb_udf` and swap the function, keeping `read_with_halo(..., neighbours=...)`.

Every choice below was measured on 2026-10-05 against its alternative on the same cluster
(`small` runtime scaled to 32 cores, two repeats in ABAB order, outputs bit-identical before any
timing counted), plus California at scale (66 files, 620 M cells, seams 0.0 deg vs single pass at
3 corners). The naive-user test on 2026-10-07 reproduced the slope result on a 4-file corner:
0.000000 deg vs single pass.

| Choice | Measured alternative | Result |
|--------|----------------------|--------|
| Catalog table as file index (`name IN (...)`), re-tiled to 2048 px | catalog tiles as stored (256 px); bucket glob; catalog `RS_Intersects` filter | lookup 2.0-4.3 s vs 26-27 s glob vs 26-29 s `RS_Intersects` (scans the global table); compute 25 s vs **164 s** on the 256-px catalog tiles (6.4x) vs 23 s glob; identical output |
| Glob when there is no catalog | explicit path list | a list fails on missing (all-ocean) cells; glob skips them |
| Tiles of 2048 px | 1024 px | about 2.3x the throughput on the same files; earlier 1024 vs 256 px: 6.8 s vs 16.3 s |
| Empty template (`empty_template_sql`) | `RS_AsInDB(tile)` | about 40 % less wall time at both 1024 and 2048 px (paired runs on the same files); identical output |
| Neighbour-file halo (`*_nb_udf`) | per-file halo | per-file: 82.6 deg fake cliffs (0-fill) or a NaN cross every 1 degree; neighbour: 0.0 |
| NaN fill outside the file | file nodata | Copernicus has no nodata tag: 0 m cliffs |
| Per-row cell sizes (geographic) | per-tile centre | tiled = single pass exactly (0.0000 deg) |
| Neighbour strip read | full padded window per neighbour | 79 s vs 72 s: no speed difference; strip uses less memory |
| `RS_BandPath` for the file path | Python UDF returning `.path` | 0.5-0.8 s vs 6.2 s on 1584 tiles |
| Partitions from the tile count | from start-up cores | CA runs started on 8 to 24 cores; a cores-based count caps an autoscaled cluster |
| `RS_AsCOG` + distributed writer | | encoding costs less than one extra Python pass; not the bottleneck |

Best combination (2048 px + empty template) against the earlier default (1024 px +
`RS_AsInDB`): roughly **4x** the throughput on 32 cores (per-run cell counts differed between
comparisons; treat the ratios as indicative). Untested: tiles larger than 2048 px, the empty
template with multi-band outputs.

## The call pattern (all recipes)

```python
from pyspark.sql import functions as F
# tiles: x, y, tile (out-db, from RS_TileExplode on a rectangle-clipped out-db DEM)
base = tiles.selectExpr("x", "y", "RS_AsInDB(tile) AS t", "tile")   # template + halo source
out = base.select("x", "y", terrain_udf(F.col("t"), F.col("tile")).alias("terrain"))
out.selectExpr("x", "y", "RS_SummaryStats(terrain, 'mean', 1) AS mean_slope").show()
```

(Older single-file pattern with `RS_AsInDB(tile)` as the template: still correct, about 1.7x slower than the empty template.) `RS_AsInDB(tile)` makes
the JVM read the tile once and hand it to Python as the georeferencing template for `with_bands()`. The out-db `tile` carries path + window; the
UDF reads that window plus the halo through rasterio. Scalar parameters (radius, window
size) are passed as `F.lit(...)` columns. Column API only; the UDF cannot be called from a
SQL string. The DataFrame in `tiles` is produced by

```sql
SELECT RS_TileExplode(RS_Clip(RS_FromPath('<dem>'), 1, ST_GeomFromText('<rect>', <dem srid>)), 512, 512) AS (x, y, tile)
```

which reads nothing (rectangle in the DEM CRS keeps the clip out-db). Set nodata on any
result you keep: `RS_SetBandNoDataValue(r, 1, CAST('NaN' AS DOUBLE))`.

## Slope (Horn 1981)

- Formula: `dzdx = ((c + 2f + i) - (a + 2d + g)) / (8 dx)`, `dzdy = ((g + 2h + i) - (a + 2b + c)) / (8 dy)`
  over the 3x3 window `a b c / d e f / g h i` (north row first); slope = `atan(hypot(dzdx, dzdy))`.
- Halo 1. Units: degrees (`units="degrees"`), percent rise (`"percent"` = 100 x rise/run) or radians.
- Function: `slope(z, dx, dy, units)`. UDF: band 1 of `terrain_udf` (degrees), or `slope_percent_udf`.
- Test: tilted plane with gradient (gx, gy) gives `atan(hypot(gx, gy))` to 1e-3 deg, also with
  dx != dy. Cluster: tiled result vs single-pass identical away from the outer ring
  (`edge_effects.md`).

## Aspect (compass, with flat handling)

- Formula: `a = atan2(dzdy, -dzdx)` in degrees; compass = `90 - a` if `a <= 90`, `360 - a + 90`
  if `a > 90`, `90 - a` if `a < 0` (ESRI). Cells with `hypot(dzdx, dzdy) <= flat_tolerance`
  get `flat_value` (default -1, GDAL's default; use NaN if you will average aspects, or better,
  average `sin`/`cos` of aspect, never the degrees).
- Halo 1. Units: degrees clockwise from north, 0-360, -1 flat.
- Function: `aspect(z, dx, dy, flat_value=-1.0, flat_tolerance=0.0)`. UDF: band 2 of `terrain_udf`.
- Test: eight plane orientations give N/NE/E/... to 1e-3 deg; a flat plane gives -1 everywhere.

## Hillshade (single sun and multidirectional)

- Formula (ESRI/GDAL): `255 * (cos(zenith) cos(slope) + sin(zenith) sin(slope) cos(az_math - aspect_math))`,
  zenith = 90 - altitude, `az_math = (360 - azimuth + 90) mod 360`, clipped to 0-255.
  `z_factor` scales the gradient (use it when elevation units differ from horizontal units).
- Multidirectional (Mark 1992, as `gdaldem -multidirectional`): suns at 225, 270, 315, 360
  weighted by `sin^2(aspect - azimuth)`; flat cells get the plain mean. Untested against
  `gdaldem` output; tested only for range and finiteness.
- Halo 1 (2 if you smooth the DEM with a 3x3 first). Units: 0-255 float32; cast to uint8 for export.
- Functions: `hillshade(z, dx, dy, azimuth=315, altitude=45, z_factor=1)`,
  `hillshade_multidirectional(z, dx, dy, altitude=45)`. UDFs: band 3 of `terrain_udf`,
  `hillshade_multi_udf`.
- Test: flat plane gives `255 sin(45)` = 180.3; a 30-degree face turned to the sun gives
  `255 cos(15)` = 246.3 and is brighter than the face turned away.

## TRI (terrain ruggedness index, 3x3)

- Formula (Riley et al. 1999): `sqrt(sum over 8 neighbours of (z_n - z_c)^2)`; the GDAL
  default (Wilson) is the mean absolute difference, `method="wilson"`.
- Halo 1. Units: metres of elevation. 0 on a flat plane; on a tilted plane with per-cell
  rise (a, b) Riley TRI is `sqrt(6 (a^2 + b^2))`, so TRI is not a slope-free roughness.
- Function: `tri(z, method="riley")`. UDF: band 1 of `tri_roughness_udf`.

## TPI (topographic position index) and two-scale landform classes

- Formula (Weiss 2001): `z - mean(z over a disk of radius r cells, centre excluded)`;
  `inner_radius > 0` makes it an annulus. Positive = ridge/top, negative = valley/toe, ~0 =
  flat or constant slope. NaN inside the window is ignored (sum of valid / count of valid).
- Halo **r** (the disk radius in cells; convert from metres with the cell size).
- Units: metres. Function: `tpi(z, radius, inner_radius=0)`. UDF: `tpi_udf(t, tile, F.lit(r))`.
- Test: a Gaussian ridge gives TPI > 0 along the crest and < 0 in the flanks; a tilted plane
  gives 0 to 1e-3; a NaN cell does not poison its neighbours.
- Landform classification (Weiss, 10 classes): standardise TPI at a small and a large
  radius with **scene-wide** mean and sd, then threshold at +-1 sd and slope 5 degrees.
  Scene-wide moments need a first pass: `tpi_sum_sumsq_count_udf(tile, F.lit(r))` returns
  `[sum, sum_sq, count]` per tile; `mean = S / N`, `sd = sqrt(S2 / N - mean^2)` in Spark; then
  `landform_class(tpi_small, tpi_large, slope_deg, sd_small, sd_large, mean_small, mean_large)`
  in a second UDF with halo `max(r_small, r_large)`. Tile-local standardisation gives a
  different class map per tile (same failure as tile-local LISA). Classes: 1 canyons,
  2 midslope drainages, 3 upland drainages, 4 U-shaped valleys, 5 plains, 6 open slopes,
  7 upper slopes, 8 local ridges in valleys, 9 midslope ridges, 10 mountain tops; 0 nodata.
  Output uint8. The class function is unit-tested on synthetic inputs; the two-pass UDF
  chain is untested on the cluster.

## Roughness (range in window)

- Formula: `max - min` over an n x n window (`gdaldem roughness` = 3x3). NaN-aware.
- Halo n // 2. Units: metres. Function: `roughness(z, size=3)`. UDF: band 2 of `tri_roughness_udf`.
- Test: 0 on a flat plane; `2 (a + b)` on a tilted plane with per-cell rise (a, b).

## Curvature (profile and plan)

- Quadratic fit `z = D x^2 + E y^2 + F xy + G x + H y + I` on the 3x3 window:
  - Zevenbergen & Thorne 1987 (exact through the 4-neighbours): `D = ((Z4 + Z6)/2 - Z5) / dx^2`,
    `E = ((Z2 + Z8)/2 - Z5) / dy^2`, `F = (-Z1 + Z3 + Z7 - Z9) / (4 dx dy)`, `G = (-Z4 + Z6) / (2 dx)`,
    `H = (Z2 - Z8) / (2 dy)` with `Z1..Z9` row-major from the north-west corner.
  - Evans 1979 / Young (least squares over all 9 cells, smoother): `D = (Z1+Z3+Z4+Z6+Z7+Z9 - 2(Z2+Z5+Z8)) / (6 dx^2)`,
    `E = (Z1+Z2+Z3+Z7+Z8+Z9 - 2(Z4+Z5+Z6)) / (6 dy^2)`, `F = (Z3+Z7-Z1-Z9) / (4 dx dy)`,
    `G = (Z3+Z6+Z9-Z1-Z4-Z7) / (6 dx)`, `H = (Z1+Z2+Z3-Z7-Z8-Z9) / (6 dy)`.
  - profile = `-2 (D G^2 + E H^2 + F G H) / (G^2 + H^2)`, plan = `2 (D H^2 + E G^2 - F G H) / (G^2 + H^2)`;
    flat cells (G = H = 0) return 0.
- Sign: for `z = a x^2` profile is `-2a` and plan 0; a bowl `a (x^2 + y^2)` gives profile
  `-2a`, plan `+2a`. Negative profile = concave along the slope (valley cross-section).
  ArcGIS reports `-100 x` this; agree on the convention before comparing.
- Halo 1. Units: 1/m (multiply by 100 for per-100 m). Function:
  `curvature(z, dx, dy, method="zevenbergen"|"evans") -> (profile, plan)`. UDF: `curvature_udf`.
- Test: both methods give 0 on a plane, `-2a`/0 on a cylinder, `-2a`/`+2a` on a bowl to 1e-4.

## Generic focal statistic template

- `focal_stat(z, size=3, stat="mean"|"std"|"min"|"max"|"range"|"median"|"count", footprint=None)`,
  NaN-aware (mean/std/count use sum-of-valid over count-of-valid; min/max/range use +-inf
  fill; median uses `generic_filter` with `nanmedian`, which is slow: 1024 x 1024 at 5x5 is
  seconds, not milliseconds).
- Halo `size // 2`. UDF: `focal_mean_udf(t, tile, F.lit(size))`; copy it and swap the stat.
- Test: 3x3 mean/median of a linear ramp reproduce the ramp; range 2; std `sqrt(2/3)`;
  5x5 count 25 in the interior; a NaN cell leaves its neighbours finite.

## Validation status

Cluster run on the `small` runtime, 2026-09-30: 4 tiles of 512 px cut from the USGS 3DEP
1/3 arc-second tile `n38w122` (rectangle clip 1016 x 1017 cells, still out-db; dx 8.144 m, dy
10.277 m at 37.9 N), each halo UDF mosaicked and compared cell by cell with a single-pass
computation over the whole clip collected on the driver (outer ring excluded, ~1.03 M cells).

| Recipe | Local synthetic test | Cluster: max abs diff vs single pass (mean) | Wall, 4 tiles |
|--------|----------------------|----------------------------------------------|---------------|
| slope (deg) | yes | 0.0092 deg (0.0027); on seams 0.0090, off seams 0.0092 | 7.7 s (incl. UDF warm-up) |
| slope (percent) | yes | 0.073 % (0.0058) | 0.9 s |
| aspect (compass, flat -1) | yes | 0.0093 deg circular (0.0059) | with slope |
| hillshade 315/45 | yes | 0.039 (0.0073) on 0-255 | with slope |
| multidirectional hillshade | range only | 0.050 (0.0087); matches its own single pass, not checked against gdaldem | 1.1 s |
| TRI (Riley), roughness 3x3 | yes | 0.0 (0.0) | 0.9 s |
| TPI (r = 5) | yes | 0.0 (0.0) | 0.9 s |
| curvature (Z-T) profile / plan | yes | 1.3e-4 / 1.1e-4 1/m (3e-6) | 0.9 s |
| landform classes two-pass | class function only | untested | |
| focal_stat | yes | untested on the cluster (same reader and pattern as TPI) | |

The non-zero differences are float32 rounding of the Horn gradient (the tile path and the
driver path evaluate `arctan`/`arctan2` on slightly different float32 partial sums); the
integer-free stencils (TRI, roughness, TPI) are bit-identical. Per-tile GeoTIFF export of the
terrain tiles through the distributed writer: 4 files in 4.5 s.

## Aggregating aspect (and any angle): circular mean only

Aspect is a bearing. Zonal or tile means of aspect degrees are meaningless (the mean of 350 and
10 is 180, the opposite direction). Aggregate aspect as a vector: mean of sin and cos of the
angle, then `atan2`, and report the resultant length as the concentration (0 = no dominant
aspect). `RS_SummaryStats(rast, 'mean', 2)` on an aspect band and `RS_ZonalStats` on it are
wrong; compute the circular mean in a UDF, or aggregate the gradient components (dz/dx, dz/dy)
and derive aspect from their means. The same applies to any angle, hour-of-day or day-of-year.
Slope, hillshade, TRI, TPI and curvature are linear in the cell and may be averaged normally.
