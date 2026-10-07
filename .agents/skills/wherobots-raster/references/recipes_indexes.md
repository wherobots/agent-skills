# Spectral index recipes (Sentinel-2 and NAIP)

Functions in `code_indexes.md` take **reflectance** arrays (floats 0-1, nodata NaN)
and return float32 with NaN where undefined; normalized differences are clipped to [-1, 1].
Convert digital numbers first with `s2_reflectance(dn, scale, offset, nodata)` after the
units check below. Local tests cover every function on scalar inputs; the cluster runs
covered NDVI (scene, per field, points) and NDMI (10 m vs 20 m grids), and the validation job
(2026-09-30) ran `ndvi_raster_udf`, `ndvi_sum_count_udf`
and `ndmi_from_stack_udf` from `code_sedona_udfs.md` on 2 tiles: raster-path and
scalar-path NDVI means agree to 9.5e-9 (0.303117 over 2,097,149 valid px), NDMI from the
stack in range with 1,048,574 valid px per tile (33.7 s including the stack read). Everything
else is **untested on the cluster** and is arithmetic on the same call pattern.

## Band naming

| Role | Sentinel-2 L2A asset (`wherobots_open_data.sentinel2.l2a_source_items` `assets.<name>.href`) | Native | NAIP (4-band quad, band index) | NAIP native |
|------|--------------------------------------------------------------------------------------------|--------|-------------------------------|-------------|
| blue | `blue` (B02) | 10 m | 3 | 0.6 m (2022) / 1 m |
| green | `green` (B03) | 10 m | 2 | |
| red | `red` (B04) | 10 m | 1 | |
| red edge | `rededge1` (B05), `rededge2` (B06), `rededge3` (B07) | 20 m | - | |
| NIR | `nir` (B08) | 10 m | 4 | |
| NIR narrow | `nir08` (B8A) | 20 m | - | |
| SWIR1 | `swir16` (B11) | 20 m | - | |
| SWIR2 | `swir22` (B12) | 20 m | - | |
| scene classification | `scl` | 20 m | - | |

`RS_Clip(tile, band, geom)` returns **one band** (the one you pass); on NAIP clip band 1 and
band 4 separately for NDVI (*measured*).

## Index table

| Index | Formula (reflectance) | Function | Sentinel-2 bands | Grid | Resample needed on S2? |
|-------|-----------------------|----------|------------------|------|------------------------|
| NDVI | (NIR - red) / (NIR + red) | `ndvi(nir, red)` | B08, B04 | 10 m | no |
| GNDVI | (NIR - green) / (NIR + green) | `gndvi(nir, green)` | B08, B03 | 10 m | no |
| EVI | G (NIR - red) / (NIR + C1 red - C2 blue + L), G=2.5, C1=6, C2=7.5, L=1 | `evi(nir, red, blue)` | B08, B04, B02 | 10 m | no; reflectance only (the L term is meaningless on DN) |
| SAVI | (1 + L)(NIR - red) / (NIR + red + L), L=0.5 | `savi(nir, red, L=0.5)` | B08, B04 | 10 m | no; reflectance only |
| MSAVI2 | (2 NIR + 1 - sqrt((2 NIR + 1)^2 - 8 (NIR - red))) / 2 | `msavi(nir, red)` | B08, B04 | 10 m | no; reflectance only |
| NDWI (McFeeters) | (green - NIR) / (green + NIR) | `ndwi(green, nir)` | B03, B08 | 10 m | no |
| MNDWI (Xu) | (green - SWIR1) / (green + SWIR1) | `mndwi(green, swir1)` | B03, B11 | 10 or 20 m | **yes** |
| NDMI (Gao) | (NIR - SWIR1) / (NIR + SWIR1) | `ndmi(nir, swir1)` | B08, B11 | 10 or 20 m | **yes** |
| NBR | (NIR - SWIR2) / (NIR + SWIR2) | `nbr(nir, swir2)` | B08, B12 | 10 or 20 m | **yes** |
| NBR2 | (SWIR1 - SWIR2) / (SWIR1 + SWIR2) | `nbr2(swir1, swir2)` | B11, B12 | 20 m | no (both 20 m) |
| NDBI (Zha) | (SWIR1 - NIR) / (SWIR1 + NIR) | `ndbi(swir1, nir)` | B11, B08 | 10 or 20 m | **yes** |
| BSI | ((SWIR1 + red) - (NIR + blue)) / ((SWIR1 + red) + (NIR + blue)) | `bsi(blue, red, nir, swir1)` | B02, B04, B08, B11 | 10 or 20 m | **yes** |
| CIre / RECI | NIR / red_edge - 1 | `reci(nir, red_edge)` | B08 (or B8A), B05 | 10 or 20 m | **yes** (B8A + B05 is 20 m only) |

NAIP: NDVI, GNDVI, SAVI/MSAVI, NDWI, EVI are all possible from the four bands at one
resolution; no SWIR, so no NDMI/NBR/NDBI/BSI. NAIP values are uint8 DN with no reflectance
calibration: the ratio indexes are fine, the L-term indexes (EVI, SAVI, MSAVI) are not.

**Resampling** ("yes" above): choose the grid by the size of the objects, not by cost
(`decisions_resampling.md`). *Measured*: 16 fields under 5 acres, NDMI at 10 m vs 20 m: median
93 vs 22 cells per field, 4 fields under 10 cells at 20 m, NDMI differences up to 0.10.
`RS_StackTileExplode(ARRAY(nir, swir16), refIdx, w, h)` resamples the other band to the
reference band's grid with nearest neighbour; refIdx 0 = 10 m grid (tile 1024), refIdx 1 =
20 m grid (tile 512 covers the same ground). The stack path is the only way to get two
different-resolution bands into one UDF row with matching shapes without writing your own
resampler; it costs one JVM copy per tile (32 to 41 s vs 25 s no-stack for a 3-band scene).

## Units check (do this once per collection, on a known target)

| Source | Tags in file | SQL functions return | `as_numpy()` on out-db returns | Use |
|--------|--------------|----------------------|--------------------------------|-----|
| `sentinel-cogs/sentinel-s2-l2a-cogs` (what `wherobots_open_data.sentinel2` links to) | none | DN | DN | `DN / 10000`, no offset, DN 0 = nodata (*measured*: lush field red DN ~550, NIR ~5800) |
| Earth Search `sentinel-2-c1-l2a` | scale 0.0001, offset -0.1 | reflectance as doubles (4x memory) | DN | in SQL never re-apply; in a UDF apply scale/offset yourself or pass `RS_AsInDB(tile)` so the UDF receives rescaled values |
| NAIP quads | none | uint8 DN | uint8 DN | ratios only; `red + nir = 0` is the collar (nodata) |

Sanity: vegetation red 0.03-0.08, NIR 0.3-0.5, NDVI 0.6-0.9; water NDVI < 0. If every field
comes out at NDVI 1.0, an offset was applied that the file does not need (*measured*
failure, corrected 2026-09-27). `s2_reflectance(dn, scale=10000.0, offset=0.0, nodata=0)`
is the module default; the UDFs use `S2_SCALE`, `S2_OFFSET` module constants (edit them, or
the inlined copy, per collection).

## Cloud masking with SCL

SCL classes (ESA): 0 nodata, 1 saturated/defective, 2 dark, 3 cloud shadow, 4 vegetation,
5 not vegetated, 6 water, 7 unclassified, 8 cloud medium, 9 cloud high, 10 thin cirrus,
11 snow/ice. `scl_mask(scl, keep=SCL_CLEAR)` keeps 4, 5, 6 (`SCL_CLEAR_WITH_SNOW` adds 11);
`apply_mask(index, mask)` sets the rest to NaN. SCL is 20 m: bring it onto the index grid with
`RS_StackTileExplode(ARRAY(red, nir, scl), 0, 1024, 1024)` (nearest) or compute on the 20 m
grid with `refIdx` pointing at `scl`. Untested on the cluster; the class table is ESA's and
the mask function is unit-tested. Filter scenes first by `eo:cloud_cover` in the items table
so most tiles need no mask at all.

## Call pattern 1: raster out (in-db template + out-db bands)

```python
from pyspark.sql import functions as F
scene = sedona.sql(f"""
  SELECT RS_FromPath(assets.red.href) AS red, RS_FromPath(assets.nir.href) AS nir
  FROM wherobots_open_data.sentinel2.l2a_source_items WHERE id = '{SCENE_ID}'""")
red_t = scene.selectExpr("RS_TileExplode(red, 1024, 1024) AS (x, y, red)")
nir_t = scene.selectExpr("RS_TileExplode(nir, 1024, 1024) AS (x, y, nir)")
tiles = red_t.join(nir_t, ["x", "y"]).where(f"RS_Intersects(red, ST_GeomFromText('{AOI}', 4326))")

ndvi_tiles = (tiles.selectExpr("x", "y", "RS_AsInDB(red) AS red_indb", "nir")
              .select("x", "y", ndvi_raster_udf(F.col("red_indb"), F.col("nir")).alias("ndvi"))
              .selectExpr("x", "y", "RS_SetBandNoDataValue(ndvi, CAST('NaN' AS DOUBLE)) AS ndvi"))
```

One JVM read (the template), one rasterio window read (NIR), one output band. *Measured*
24 s for a 121-tile scene, 13 to 27 s for 6 tiles including warm-up. For a
three-band index (EVI, BSI) add more out-db columns; for a mixed-resolution index use
`RS_StackTileExplode` and a stack UDF such as `ndmi_from_stack_udf` (band order = array order).

## Call pattern 2: scalar aggregate (two out-db columns, no raster)

```python
sc = (tiles.select(ndvi_sum_count_udf(F.col("red"), F.col("nir")).alias("sc"))
      .agg(F.sum(F.col("sc")[0]).alias("s"), F.sum(F.col("sc")[1]).alias("n")).collect()[0])
mean_ndvi = sc["s"] / sc["n"]
```

Zero JVM pixel copies; *measured* 18 s for the whole scene (121 tiles), 4.8 s for 6 tiles.
Per polygon: `ndvi_sum_count_in_polygon_udf(red_win, nir_win, geom_r)` on bbox windows
(`RS_Clip(red, 1, ST_Envelope(geom_r))`, out-db) and `sum(s) / sum(n)` grouped by polygon
(*measured* 17.6 s on 1 M px for 211 field-tile pairs).

## Writing your own index UDF

```python
@sedona_vectorized_udf(return_type=RasterType())
def evi_raster_udf(red_indb: SedonaRaster, nir: SedonaRaster, blue: SedonaRaster) -> SedonaRaster:
    r = s2_reflectance(red_indb.as_numpy()[0], S2_SCALE, S2_OFFSET)
    n = s2_reflectance(nir.as_numpy()[0], S2_SCALE, S2_OFFSET)
    b = s2_reflectance(blue.as_numpy()[0], S2_SCALE, S2_OFFSET)
    return red_indb.with_bands(evi(n, r, b)[np.newaxis])
```

Annotate every raster argument with `SedonaRaster` and geometry arguments with
`BaseGeometry`; the first argument must be the in-db template when a raster comes out;
`with_bands` takes a CHW array of any band count and dtype but the same H x W.

## Ratio indexes and zonal statistics: index first, then aggregate

Do not compute NDVI (or any ratio index) from the zonal means of the bands. NDVI of the band
means is a brightness-weighted mean of per-pixel NDVI (weights = NIR + red per pixel), equal to
the plain mean only when the polygon is homogeneous. Measured on Sentinel-2 at 10 m: median gap
0.0005 to 0.008 NDVI, up to 0.12, biased low, growing with polygon size and heterogeneity
(up to 25% of 40-px polygons differ by more than 0.02). Correct SQL-only path per polygon:
`RS_ZonalStats(RS_MapAlgebra(RS_Union(RS_Clip(red, 1, bbox), RS_Clip(nir, 1, bbox)), 'D', '<ndvi>'),
geom, 1, 'mean')`, or an NDVI tile first and then zonal stats, or the masked UDF. Sums and
counts (area, pixel counts) may be aggregated from bands; ratios may not.
