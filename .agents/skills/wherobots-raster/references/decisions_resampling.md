# Decisions to make with the analyst before touching pixels

Every item below changes the numbers, not just the runtime. Ask, record the answer in the
pipeline (a constant with a comment), and only then write SQL. Numbers marked *measured* come
from Wherobots Cloud job runs (2026-09-26/27, `small` runtime); everything
else is stated as a default, not a measurement.

## 1. Mixed native resolutions

**Ask:** "Which bands drive the answer, and what is the native resolution of each?"
Sentinel-2: 10 m (B02 blue, B03 green, B04 red, B08 NIR), 20 m (B05-B07 red edge, B8A, B11
SWIR1, B12 SWIR2, SCL), 60 m (B01, B09). USGS 3DEP: 10 m (1/3 arc-second), 30 m (1 arc-second).
Copernicus GLO-30: 30 m. NAIP: 0.6 m (2022) or 1 m, 4 bands RGB+NIR.

**Default:** put every band on the grid of the **finest band that carries signal for the
objects you measure**, resample the others to it with nearest neighbour. For NDMI (NIR 10 m,
SWIR1 20 m) over fields: 10 m grid, SWIR1 duplicated 2x2.

**Override:** go to the coarse grid when the coarse band carries the signal *and* the objects
are large relative to it (SWIR-driven moisture over large zones, county-scale summaries), or
when the fine band is only there for a mask.

**Cost:** each halving of cell size multiplies pixels by 4 and memory per in-db tile by 4.
*Measured:* 3-band Sentinel-2 stack, 121 tiles: 41 s at the 10 m reference (362 M px in)
vs 32 s at the 20 m reference (90 M px in). Nearest-neighbour upsampling invents no values,
so the extra cost is the only downside.

## 2. Size of the objects measured

**Ask:** "What is the smallest polygon (or the point spacing) you need a trustworthy number
for?" Fields under 5 acres, building footprints, tree crowns, sample plots are *small*.
Watersheds, counties, H3 cells at resolution 7 and coarser are *large*.

**Default:** a polygon needs on the order of tens of cells for a stable mean; below about
10 cells the boundary rule (centroid-in vs all-touched) and one nodata pixel dominate.

*Measured* (Sentinel-2 NDMI, 16 fields under 5 acres, Fresno County): median 93 cells per
field at 10 m vs 22 at 20 m; 4 of 16 fields had fewer than 10 cells at 20 m; per-field NDMI
differed between the two grids by 0.0099 median and **0.10 maximum**. The 20 m answer for
those four fields is a handful of cells, not a statistic.

**Override:** large zones can use the coarse grid; the aggregated mean converges either way.

**Points:** resolution is a sampling question. `RS_Value` returns the cell under the point; on
a 20 m band a point 5 m from a field edge may read the neighbour's field. Buffer the point and
use `RS_ZonalStats` when the point location is uncertain relative to the cell size.

## 3. Signal-bearing band

**Ask:** "Which band actually carries the physical signal for this index?" NDMI and NBR are
SWIR-driven (20 m on Sentinel-2); NDVI/GNDVI/EVI are 10 m; red-edge chlorophyll (CIre) is
20 m on Sentinel-2 (B05); on NAIP everything is the same resolution.

**Default:** the signal band decides how much *detail* is real; the object size (item 2)
decides which *grid* you compute on. Both can be true: compute NDMI on the 10 m grid for
small fields while telling the analyst the moisture detail is still 20 m.

## 4. Nearest vs bilinear vs average

**Ask:** "Are the values categorical, physical, or an index?"

| Data | Up (finer) | Down (coarser) |
|------|-----------|----------------|
| Categorical (SCL, NLCD, CDL) | nearest | nearest, or mode if you can compute it |
| Physical continuous (reflectance, elevation) | nearest keeps values honest; bilinear only for display | average (area-weighted mean) |
| Derived index (NDVI) | compute the index *after* resampling the inputs, not before | average of the index is not the index of the averages: decide which you report |

**Default:** nearest for analysis, average for downsampling continuous data, bilinear only for
rendering. `RS_StackTileExplode` resamples to the reference band with **nearest** (that is what
the engine does; there is no option). `RS_Resample(raster, ..., algorithm)` offers the GeoTools
algorithms for an explicit choice, but it reads the whole coverage: tile first.

*Measured:* NAIP per-field NDVI, NDVI-of-means (`RS_ZonalStats` on red and NIR windows
separately) vs mean-of-NDVI (masked UDF) differed by 0.031 mean absolute across 200 fields;
on Sentinel-2 windows the two agreed to 1e-4. Pin one definition before comparing methods.

## 5. CRS choice

**Ask:** "What CRS is the raster in, and what CRS are the polygons in?" Then check both with
`RS_SRID(rast)` and `ST_SRID(geometry)`. Never assume.

- **Geographic DEMs** (USGS 3DEP EPSG:4269, Copernicus EPSG:4326): the cell is in degrees.
  USGS 1/3 arc-second = 0.0000926 deg = about 8.1 m east-west and 10.3 m north-south at 37.9 N
  (*measured*); Copernicus 0.000277778 deg = about 24.8 m by 30.9 m at
  36.6 N. Slope from degree cell sizes is wrong by a factor of ~1e5. Use per-row cell sizes
  (`row_cell_sizes`; `cell_size_m()` at the tile centre is close but not exact) or reproject first (`RS_Resample`/`RS_ReprojectMatch`: whole-coverage read).
  Anisotropy matters: 8.1 vs 10.3 m is a 27 % difference between dx and dy at this latitude.
- **UTM zones split across a study area**: NAIP quads over the Fresno AOI came in EPSG:26910
  and 26911 (*measured*, 198 quads). Transform each polygon into the CRS of the tile it is
  joined to (`ST_Transform(geom, 'EPSG:4326', CONCAT('EPSG:', RS_SRID(tile)))`), do the
  zonal work per tile, then aggregate. Do not reproject the rasters to a single zone unless the
  output must be one mosaic.
- **NAD83 vs WGS84 tags**: the California field-boundary polygons used in these runs were tagged
  EPSG:4269. A predicate against an EPSG:4326 geometry returns **false with no error**
  (*measured*). `ST_SetSRID(geometry, 4326)` first, then `ST_Transform` into the raster CRS.
  The NAD83/WGS84 datum difference is metre-level in the conterminous US, below the 10 m
  cells used here (not measured; check when cells are sub-metre, e.g. NAIP).
- **`srid 0`**: NLCD (Albers, custom WKT) resolved to SRID 0 in `RS_MetaData` (*measured*).
  Polygon transforms will silently assume WGS84. Set the SRID explicitly (`RS_SetSRID`) after
  confirming the projection from the file's WKT.
- Output CRS: keep the raster CRS for raster outputs; write vector results in EPSG:4326.

## 6. Nodata and scale/offset units

**Ask:** "What is nodata in this file, and are the values physical units or digital numbers?"
Then **verify on a known target** before trusting any tag, STAC field or collection rule.

- `wherobots_open_data.sentinel2.l2a_source_items` links to `sentinel-cogs` files with **no
  scale/offset tags**: SQL functions and `as_numpy()` both return DN; the DN is reflectance x
  10000 with **no** BOA offset (a lush field read red DN ~550, NIR ~5800; *measured*). Use
  `DN / 10000`, DN 0 = nodata. Applying the documented -1000 offset pushed red negative and
  every field NDVI to 1.0 with no error raised (*measured*, corrected 2026-09-27).
- Earth Search Collection-1 files carry scale 0.0001 / offset -0.1: SQL functions return
  reflectance as doubles (4x the memory), a UDF on the out-db tile still sees DN.
- Sanity ranges for a vegetated pixel: red 0.03-0.08, NIR 0.3-0.5, NDVI 0.6-0.9. Water NDVI
  below 0. If the numbers are off by 1000x or every index saturates, the units are wrong.
- NAIP quads have a zero-valued collar; treat red + NIR = 0 as nodata or the collar drags means
  down (*measured* as part of the 0.031 disagreement above).
- Any raster you write from a UDF: `RS_SetBandNoDataValue(r, CAST('NaN' AS DOUBLE))` (or the
  sentinel you used), otherwise `RS_SummaryStats` means include NaN and return NaN.
- Nodata inside a focal window: a 3x3 kernel with NaN propagation yields a NaN ring one cell
  wide around every nodata cell (GDAL's default). The `tpi`/`focal_stat` functions in
  `code_terrain_focal.md` ignore NaN instead; state which behaviour you want.

## 7. Tile size vs COG block size

**Ask:** "What is the internal block size?" `RS_MetaData(rast)` gives `tileWidth`/`tileHeight`.
Sentinel-2 COGs on `sentinel-cogs`: 1024. NAIP, USGS 3DEP 1/3 arc-second: 512. NLCD, CDL:
512. Copernicus DEM table: 256-px tiles.

**Default:** `RS_TileExplode(rast, block, block)`. One tile = one HTTP range request per band.
Exception, measured 2026-10-05: halo terrain UDFs over 1-degree DEM COGs ran 2.3x faster on
2048-px tiles than on 1024-px tiles (fewer rows, less per-row Python overhead; `recipes_terrain.md`).
Tiles that are multiples of the block size cost no extra I/O.

**Cost:** tiles smaller than the block still fetch the whole block per read (*measured*: 256-px
tiles over 1024-px blocks produce 1849 tiles instead of 121 and every read pulls the 1024
block). Tiles larger than the block are fine for I/O but raise the per-row memory: a
1024 x 1024 UInt16 tile is 2 MB on disk and 8 MB as doubles, 16 MB for a two-band stack.
Striped files (block 10812 x 1, 156335 x 1) work but tile explode is 14x slower (2.61 s vs
0.19 s for 484 tiles, *measured*) and every window read pulls whole strips: re-tile with
`rio cogeo create --blocksize 512` before heavy use.

For Python per-row overhead (COG open, VRT, Arrow) prefer fewer, larger tiles; for esda-style
statistics with a legacy `W` object keep tiles at 512 or under (memory). 512 is the sweet spot
for stencil-style focal work (*measured* locally: 2 MB per band, 0.1 s of maths).

## 8. Zonal boundary rule: centroid-in or all-touched

**Ask:** "Should a cell count for a polygon when its centre is inside, or whenever the polygon
touches it?" This is the analyst's call: it changes what the number means, not whether it is
correct. `RS_ZonalStats*` default is centroid-in (`allTouched = false`); the argument after
`statType` (or after `band` in `RS_ZonalStatsAll`) is `allTouched`.

| | Centroid-in (default) | All-touched |
|---|---|---|
| Cells counted | those whose centre lies in the polygon | every cell the boundary or interior touches |
| Small or thin polygons | can get 0 cells: no value | always at least 1 cell |
| Edge cells | included only when mostly inside | all included: the value leans toward the surroundings (field edges, roads, water) |
| Adjacent polygons | each cell belongs to one polygon | a shared edge cell counts for both: sums and counts double-count across polygons |
| Area totals (sum of a density, counts) | close to the polygon area | inflated by the edge ring |

**Default to recommend:** centroid-in when polygons span tens of cells or more, or when values
will be summed across adjacent polygons (populations, totals, areas). All-touched when every
polygon must get a value and the polygons are small relative to the cell (fields under a few
cells, building footprints on 30 m data, narrow riparian strips); report the cell count next
to the value either way, so the reader can see when a number rests on a handful of cells.
Mixing the two rules in one table is the thing to avoid.

*Measured* (2026-10-05, Copernicus 30 m slope, 250,000 California field polygons,
centroid-in): 247,613 of 250,000 fields received at least one cell, mean 114 cells per
field. The 2,387 without a value were not broken down (tiny polygons, or outside the DEM
tiles); all-touched on the same set is untested.

## Cheat sheet to paste into a pipeline header

```python
GRID_M = 10          # target grid, decided by object size (item 2)
RESAMPLE = "nearest" # analysis; 'average' only when downsampling continuous data
RASTER_EPSG = 32610  # from RS_SRID, not assumed
VECTOR_EPSG = 4269   # from ST_SRID; retag with ST_SetSRID before ST_Transform
SCALE, OFFSET, NODATA = 10000.0, 0.0, 0   # verified on a known target on <date>
TILE = 1024          # = COG block size from RS_MetaData
ZONAL_ALL_TOUCHED = False  # boundary rule, decided with the analyst (item 8)
```

## How the engine resamples (verified in source, 2026-09-30)

- `RS_StackTileExplode` maps every non-reference band onto the reference grid by **nearest
  neighbour**. Downsampling 10 m to 20 m with it keeps 1 of every 4 pixels; it does not average.
- `RS_Resample` offers NearestNeighbor, Bilinear and Bicubic. None of them is a block average.
- For a true area-weighted downsample (the right choice when the coarse grid is the target and
  the fine band carries noise), do it in the UDF: reshape to (H/k, k, W/k, k) and take the mean,
  or `scipy.ndimage.zoom` / rasterio `Resampling.average` on the window.
- Upsampling 20 m to 10 m by nearest neighbour duplicates values; bilinear smooths them. Neither
  adds information; both preserve polygon geometry on the fine grid.
State which one you used in every pipeline and benchmark.
