# Reference code: Sedona raster UDFs, halo reader, neighbour files

The `@sedona_vectorized_udf` layer. Paste it **after** the functions from `code_terrain_focal.md`
and `code_indexes.md` (it calls them). Defined only when the Sedona runtime imports, so the same
cell also runs locally.

Call pattern: `tiles.selectExpr(empty_template_sql("tile"), "tile")` then `udf(F.col("t"),
F.col("tile"))`. The template supplies georeferencing for `with_bands()`; the out-db tile supplies
the halo read. The `*_udf` terrain wrappers were validated on the cluster with `RS_AsInDB(tile)` as
the template; the empty template gave bit-identical single-band output (2026-10-05) and is
*untested* for the multi-band `terrain_udf`/`terrain_nb_udf`. `ndvi_raster_udf` and
`ndmi_from_stack_udf` take an in-db raster because they read its pixels.

*Measured* 2026-10-05, Copernicus GLO-30 4-file corner, 3600 x 3600 cells: `slope_nb_udf` with
`with_neighbour_files` matches a single pass over the merged mosaic to 0.0000 deg on file seams.

Changes after the cluster validation (2026-10-07, checked locally: a stubbed-runtime read across a
two-file seam equals the merged array, and a ring with no neighbour file is NaN, never 0):
`open_source` is a context manager that exits its `rasterio.Env` with the file; it warns when the
Sedona AWS session is unavailable instead of silently using the default AWS chain (an Access
Denied in the UDF while SQL reads the same file points there); `read_with_halo` uses masked reads,
so the file's nodata and the ring outside the file become NaN for any input dtype.

Trade-off to know:

- The single-file terrain UDFs use the tile-centre latitude for geographic cell sizes; the `*_nb_udf`
  variants use per-row sizes (`cell_sizes_rows_of`) and match a single pass exactly. Prefer the
  `*_nb_udf` variants on EPSG:4326/4269 DEMs; the centre-latitude error grows with tile height.

```python
import contextlib
import math
import warnings

import numpy as np


def empty_template_sql(rast_col="rast", alias="t", band_type="F"):
    """SQL for a georeferencing template that reads NO pixels: an empty in-db raster with the tile's
    size, geotransform and SRID. Use it instead of RS_AsInDB(tile) as the `with_bands()` template.
    Measured 2026-10-05 (one cluster, 32 cores, paired runs, results bit-identical): about 40 % less wall time;
    RS_AsInDB makes the JVM read the same window from S3 that the UDF reads again."""
    r = rast_col
    return (f"RS_MakeEmptyRaster(1, '{band_type}', RS_Width({r}), RS_Height({r}), RS_UpperLeftX({r}), RS_UpperLeftY({r}), "
            f"RS_ScaleX({r}), RS_ScaleY({r}), RS_SkewX({r}), RS_SkewY({r}), RS_SRID({r})) AS {alias}")


# ============================================================================= Sedona / Wherobots layer
try:
    from pyspark.sql.types import ArrayType, DoubleType
    from sedona.spark.raster import SedonaRaster
    from sedona.spark.sql.functions import sedona_vectorized_udf
    from sedona.spark.sql.types import RasterType
    from shapely.geometry.base import BaseGeometry

    HAVE_SEDONA = True
except ImportError:  # local use: pure functions only
    HAVE_SEDONA = False

if HAVE_SEDONA:

    def cell_size_m_of(raster: "SedonaRaster"):
        """Metric cell sizes for a SedonaRaster: reads the geotransform and decides projected vs geographic
        from the CRS WKT (PROJCS/PROJCRS present -> metres)."""
        at = raster.affine_trans
        wkt = raster.crs_wkt or ""
        projected = "PROJCS" in wkt or "PROJCRS" in wkt
        lat = at.ip_y + at.scale_y * raster.height / 2.0
        return cell_size_m(at.scale_x, at.scale_y, lat, projected)

    def affine6_of(raster: "SedonaRaster"):
        at = raster.affine_trans
        return (at.scale_x, at.skew_x, at.ip_x, at.skew_y, at.scale_y, at.ip_y)

    def gdal_path(path: str) -> str:
        if path.startswith("s3a://"):
            path = "s3://" + path[len("s3a://"):]
        if path.startswith("file:"):
            path = path[5:]
        if path.startswith("s3://"):
            return "/vsis3/" + path[len("s3://"):]
        if path.startswith(("http://", "https://")):
            return "/vsicurl/" + path
        return path

    @contextlib.contextmanager
    def open_source(path: str, requester_pays: bool = False):
        """Context manager: open the out-db source file with rasterio using the session's S3 credentials
        (the same helper SedonaRaster uses). requester_pays=True for buckets like s3://naip-analytic.
        The rasterio.Env is exited with the file, so a long-lived Python worker does not pile up Envs."""
        import rasterio

        try:
            from sedona.spark.raster.gdal_conf import get_rasterio_aws_session

            session = get_rasterio_aws_session(path)
        except Exception as e:  # noqa: BLE001
            warnings.warn(f"open_source: no Sedona AWS session for {path} ({e!r}); using the default AWS chain")
            session = None
        opts = {"AWS_REQUEST_PAYER": "requester"} if requester_pays else {}
        if session is not None:
            env = rasterio.Env(session=session, AWS_NO_SIGN_REQUEST="YES" if getattr(session, "unsigned", False) else "NO", **opts)
        else:
            env = rasterio.Env(**opts)
        with env, rasterio.open(gdal_path(path), mode="r") as src:
            yield src

    def _halo_window(src, outdb, pad):
        t, at = src.transform, outdb.affine_trans
        if abs(t.a - at.scale_x) > 1e-12 or abs(t.e - at.scale_y) > 1e-12:
            raise ValueError(f"grid mismatch: {src.name} {t.a},{t.e} vs tile {at.scale_x},{at.scale_y}")
        col0 = int(round((at.ip_x - t.c) / t.a)) - pad
        row0 = int(round((at.ip_y - t.f) / t.e)) - pad
        return col0, row0

    def read_with_halo(outdb: "SedonaRaster", pad: int, band: int = 0, requester_pays: bool = False,
                       neighbours: str = None) -> np.ndarray:
        """Read the out-db tile window plus `pad` cells on every side. Returns float32
        (H + 2 pad, W + 2 pad), file nodata -> NaN, and NaN wherever no file has data.

        The ring outside the tile's own file is filled with NaN, never with the file's nodata value:
        Copernicus GLO-30 has no nodata tag, so `fill_value=src.nodata` filled with 0 m and put a fake
        cliff on every 1-degree line (measured 2026-10-05: max 82.6 deg, mean 11.2 deg on file seams).

        `neighbours`: ';'-joined paths of the other files the padded window overlaps (from
        `with_neighbour_files`). Only the overlapping strip of each is read, so a tile in a file's
        interior costs nothing extra. Neighbours must share the tile's grid (same pixel size/alignment)."""
        from rasterio.windows import Window

        h, w = outdb.height + 2 * pad, outdb.width + 2 * pad
        src_band = outdb.outdb_meta.band_indices[band] + 1
        with open_source(outdb.path, requester_pays) as src:
            col0, row0 = _halo_window(src, outdb, pad)
            # masked read: the file's nodata AND the ring outside the file come back masked, for any dtype
            arr = src.read(src_band, window=Window(col0, row0, w, h), boundless=True, masked=True,
                           out_dtype="float32").filled(np.nan)
        for p in [q for q in (neighbours or "").split(";") if q]:
            if not np.isnan(arr).any():
                break
            with open_source(p, requester_pays) as nsrc:
                c0, r0 = _halo_window(nsrc, outdb, pad)
                cw = clip_window(c0, r0, w, h, nsrc.width, nsrc.height)
                if cw is None:
                    continue
                sc, sr, ww, hh, dc, dr = cw
                part = nsrc.read(src_band, window=Window(sc, sr, ww, hh), masked=True, out_dtype="float32").filled(np.nan)
            dst = arr[dr:dr + hh, dc:dc + ww]
            hole = np.isnan(dst)
            dst[hole] = part[hole]
        return arr

    def _trim(a, pad):
        return a[pad:-pad, pad:-pad] if pad > 0 else a

    # ---- terrain UDFs. Pattern: df.selectExpr("RS_AsInDB(tile) AS t", "tile") then udf(col("t"), col("tile")).
    # `template` (in-db) supplies the output georeferencing for with_bands(); `outdb` supplies the halo read.

    @sedona_vectorized_udf(return_type=RasterType())
    def terrain_udf(template: SedonaRaster, outdb: SedonaRaster) -> SedonaRaster:
        """3 bands float32: slope_deg, aspect_deg (compass, -1 flat), hillshade (az 315, alt 45). Halo 1."""
        pad = 1
        z = read_with_halo(outdb, pad)
        dx, dy = cell_size_m_of(template)
        s = _trim(slope(z, dx, dy), pad)
        a = _trim(aspect(z, dx, dy), pad)
        h = _trim(hillshade(z, dx, dy), pad)
        return template.with_bands(np.stack([s, a, h]))

    @sedona_vectorized_udf(return_type=RasterType())
    def slope_percent_udf(template: SedonaRaster, outdb: SedonaRaster) -> SedonaRaster:
        pad = 1
        z = read_with_halo(outdb, pad)
        dx, dy = cell_size_m_of(template)
        return template.with_bands(_trim(slope(z, dx, dy, "percent"), pad)[np.newaxis])

    @sedona_vectorized_udf(return_type=RasterType())
    def hillshade_multi_udf(template: SedonaRaster, outdb: SedonaRaster) -> SedonaRaster:
        pad = 1
        z = read_with_halo(outdb, pad)
        dx, dy = cell_size_m_of(template)
        return template.with_bands(_trim(hillshade_multidirectional(z, dx, dy), pad)[np.newaxis])

    @sedona_vectorized_udf(return_type=RasterType())
    def tri_roughness_udf(template: SedonaRaster, outdb: SedonaRaster) -> SedonaRaster:
        """2 bands: TRI (Riley), roughness (3x3 range). Halo 1. Units: metres of elevation."""
        pad = 1
        z = read_with_halo(outdb, pad)
        return template.with_bands(np.stack([_trim(tri(z), pad), _trim(roughness(z), pad)]))

    @sedona_vectorized_udf(return_type=RasterType())
    def tpi_udf(template: SedonaRaster, outdb: SedonaRaster, radius: int) -> SedonaRaster:
        """1 band: TPI at `radius` cells (pass F.lit(r)). Halo = radius."""
        pad = int(radius)
        z = read_with_halo(outdb, pad)
        return template.with_bands(_trim(tpi(z, pad), pad)[np.newaxis])

    @sedona_vectorized_udf(return_type=RasterType())
    def curvature_udf(template: SedonaRaster, outdb: SedonaRaster) -> SedonaRaster:
        """2 bands: profile, plan curvature (1/m, Zevenbergen-Thorne). Halo 1."""
        pad = 1
        z = read_with_halo(outdb, pad)
        dx, dy = cell_size_m_of(template)
        prof, plan = curvature(z, dx, dy)
        return template.with_bands(np.stack([_trim(prof, pad), _trim(plan, pad)]))

    @sedona_vectorized_udf(return_type=RasterType())
    def focal_mean_udf(template: SedonaRaster, outdb: SedonaRaster, size: int) -> SedonaRaster:
        """Generic focal template: NaN-aware mean over size x size (pass F.lit(size), odd). Halo = size // 2."""
        pad = int(size) // 2
        z = read_with_halo(outdb, pad)
        return template.with_bands(_trim(focal_stat(z, int(size), "mean"), pad)[np.newaxis])

    @sedona_vectorized_udf(return_type=ArrayType(DoubleType()))
    def tpi_sum_sumsq_count_udf(outdb: SedonaRaster, radius: int) -> list:
        """Pass 1 for landform classification: [sum, sum of squares, count] of TPI over the tile's own
        cells, so scene-wide mean and sd can be aggregated in Spark before standardising."""
        pad = int(radius)
        v = _trim(tpi(read_with_halo(outdb, pad), pad), pad)
        ok = np.isfinite(v)
        return [float(v[ok].sum()), float((v[ok] ** 2).sum()), float(ok.sum())]

    # ---- spectral index UDFs. Out-db arrives as raw DN -> convert with s2_reflectance (defaults: sentinel-cogs).
    S2_SCALE, S2_OFFSET = 10000.0, 0.0

    @sedona_vectorized_udf(return_type=RasterType())
    def ndvi_raster_udf(red_indb: SedonaRaster, nir_outdb: SedonaRaster) -> SedonaRaster:
        """Raster out: RS_AsInDB(red) template + out-db nir. Set nodata afterwards with RS_SetBandNoDataValue."""
        r = s2_reflectance(red_indb.as_numpy()[0], S2_SCALE, S2_OFFSET)
        n = s2_reflectance(nir_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET)
        return red_indb.with_bands(ndvi(n, r)[np.newaxis])

    @sedona_vectorized_udf(return_type=RasterType())
    def ndmi_from_stack_udf(stack_indb: SedonaRaster) -> SedonaRaster:
        """RS_StackTileExplode(ARRAY(nir, swir16), ref, w, h) tile -> NDMI. Band order = array order."""
        a = stack_indb.as_numpy()
        n, s = s2_reflectance(a[0], S2_SCALE, S2_OFFSET), s2_reflectance(a[1], S2_SCALE, S2_OFFSET)
        return stack_indb.with_bands(ndmi(n, s)[np.newaxis])

    @sedona_vectorized_udf(return_type=ArrayType(DoubleType()))
    def ndvi_sum_count_udf(red_outdb: SedonaRaster, nir_outdb: SedonaRaster) -> list:
        """Scalar out, both out-db (zero JVM pixel copies): [sum, count] of valid NDVI. Aggregate in Spark."""
        v = ndvi(s2_reflectance(nir_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET),
                 s2_reflectance(red_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET))
        ok = np.isfinite(v)
        return [float(v[ok].sum()), float(ok.sum())]

    def geometry_mask(raster: "SedonaRaster", geom, all_touched: bool = False) -> np.ndarray:
        """Boolean (H, W) mask, True inside `geom`; geom must already be in the raster CRS."""
        from rasterio import features
        from rasterio.transform import Affine

        return features.geometry_mask([geom], out_shape=(raster.height, raster.width),
                                      transform=Affine(*affine6_of(raster)), invert=True, all_touched=all_touched)

    @sedona_vectorized_udf(return_type=ArrayType(DoubleType()))
    def ndvi_sum_count_in_polygon_udf(red: SedonaRaster, nir: SedonaRaster, geom: BaseGeometry) -> list:
        """[sum, count] of NDVI inside the polygon on a bbox window (RS_Clip by ST_Envelope first).
        Aggregate sum/count across (field, tile) pairs in Spark for exact per-field means."""
        v = ndvi(s2_reflectance(nir.as_numpy()[0], S2_SCALE, S2_OFFSET),
                 s2_reflectance(red.as_numpy()[0], S2_SCALE, S2_OFFSET))
        ok = geometry_mask(red, geom) & np.isfinite(v)
        return [float(v[ok].sum()), float(ok.sum())]

    # ---- cross-file halo (multi-file DEMs such as Copernicus GLO-30 1-degree COGs, 3DEP 1x1 tiles).
    # Pattern (measured 2026-10-05, 4-file corner, 3600 x 3600 cells: 0.0000 deg vs single pass):
    #   tiles = sedona.read.format("raster").option("tileWidth", "1024").option("tileHeight", "1024").load(glob)
    #   tiles = with_neighbour_files(tiles, pad_deg=2 * pixel_size)       # adds `neighbours` string column
    #   tiles.selectExpr("RS_AsInDB(rast) AS t", "rast", "neighbours").select(slope_nb_udf(...))

    def with_neighbour_files(tiles, pad_deg: float, rast_col: str = "rast"):
        """Add `path` and `neighbours` (';'-joined paths of OTHER files within pad_deg of each tile's
        envelope). Uses the reader's own tiles as the file catalogue: a spatial self-join on envelopes,
        no VRT. Use pad_deg >= halo cells x pixel size (2x for safety)."""
        from pyspark.sql import functions as F

        t = (tiles.withColumn("path", F.expr(f"RS_BandPath({rast_col})"))
             .withColumn("_env", F.expr(f"RS_Envelope({rast_col})"))
             # deterministic tile key: file + upper-left corner (x/y are per file and may be absent)
             .withColumn("_tid", F.concat_ws("|", "path", F.expr(f"CAST(RS_UpperLeftX({rast_col}) AS STRING)"),
                                             F.expr(f"CAST(RS_UpperLeftY({rast_col}) AS STRING)"))))
        cat = t.select(F.col("path").alias("_npath"), F.col("_env").alias("_nenv")).distinct()
        nb = (t.select("_tid", "path", "_env").join(cat, F.expr(f"ST_Intersects(ST_Buffer(_env, {pad_deg}), _nenv) AND path <> _npath"), "left")
              .groupBy("_tid").agg(F.concat_ws(";", F.collect_set("_npath")).alias("neighbours")))
        return t.drop("_env").join(nb, "_tid").drop("_tid")

    def cell_sizes_rows_of(raster: "SedonaRaster", n_rows: int, pad: int):
        """(dx, dy) for a padded array: per-row (n_rows, 1) arrays for geographic CRSs, scalars if projected."""
        at = raster.affine_trans
        wkt = raster.crs_wkt or ""
        if "PROJCS" in wkt or "PROJCRS" in wkt:
            return abs(at.scale_x), abs(at.scale_y)
        return row_cell_sizes(at.ip_y, at.scale_x, at.scale_y, n_rows, pad)

    @sedona_vectorized_udf(return_type=RasterType())
    def slope_nb_udf(template: SedonaRaster, outdb: SedonaRaster, neighbours: str) -> SedonaRaster:
        """1 band float32 slope (degrees), cross-file halo, per-row cell sizes. Halo 1."""
        pad = 1
        z = read_with_halo(outdb, pad, neighbours=neighbours)
        dx, dy = cell_sizes_rows_of(template, z.shape[0], pad)
        return template.with_bands(_trim(slope(z, dx, dy), pad)[np.newaxis].astype(np.float32))

    @sedona_vectorized_udf(return_type=RasterType())
    def terrain_nb_udf(template: SedonaRaster, outdb: SedonaRaster, neighbours: str) -> SedonaRaster:
        """3 bands float32: slope_deg, aspect_deg (compass, -1 flat), hillshade 315/45. Cross-file halo 1."""
        pad = 1
        z = read_with_halo(outdb, pad, neighbours=neighbours)
        dx, dy = cell_sizes_rows_of(template, z.shape[0], pad)
        return template.with_bands(np.stack([_trim(slope(z, dx, dy), pad), _trim(aspect(z, dx, dy), pad),
                                             _trim(hillshade(z, dx, dy), pad)]).astype(np.float32))

    @sedona_vectorized_udf(return_type=ArrayType(DoubleType()))
    def tpi_moments_nb_udf(outdb: SedonaRaster, neighbours: str, r_small: int, r_large: int) -> list:
        """Landform pass 1: [sum, sumsq, count] of TPI at r_small then at r_large over the tile's own cells."""
        pad = int(max(r_small, r_large))
        z = read_with_halo(outdb, pad, neighbours=neighbours)
        out = []
        for r in (int(r_small), int(r_large)):
            v = _trim(tpi(z, r), pad)
            ok = np.isfinite(v)
            out += [float(v[ok].sum()), float((v[ok].astype(np.float64) ** 2).sum()), float(ok.sum())]
        return out

    @sedona_vectorized_udf(return_type=RasterType())
    def landform_nb_udf(template: SedonaRaster, outdb: SedonaRaster, neighbours: str, r_small: int, r_large: int,
                        mean_small: float, sd_small: float, mean_large: float, sd_large: float) -> SedonaRaster:
        """Landform pass 2: Weiss 10 classes (uint8, 0 nodata) with scene-wide TPI moments from pass 1."""
        pad = int(max(r_small, r_large))
        z = read_with_halo(outdb, pad, neighbours=neighbours)
        dx, dy = cell_sizes_rows_of(template, z.shape[0], pad)
        ts, tl = _trim(tpi(z, int(r_small)), pad), _trim(tpi(z, int(r_large)), pad)
        sl = _trim(slope(z, dx, dy), pad)
        cls = landform_class(ts, tl, sl, sd_small, sd_large, mean_small, mean_large)
        return template.with_bands(cls[np.newaxis].astype(np.uint8))

def mosaic(df, col):
    """Paste in-db tiles (columns x, y, <col>) into one CHW float32 array on the DRIVER by tile offsets.
    Exact when tiles were computed with a halo. Returns (array, top-left affine_trans, crs_wkt).
    Small areas only: the whole mosaic lives in driver memory."""
    rows = df.select("x", "y", col).collect()
    rs = [(r["x"], r["y"], r[col]) for r in rows]
    xs, ys = sorted({x for x, _, _ in rs}), sorted({y for _, y, _ in rs})
    wid = {x: next(r.width for xx, _, r in rs if xx == x) for x in xs}
    hei = {y: next(r.height for _, yy, r in rs if yy == y) for y in ys}
    xoff = {x: sum(wid[i] for i in xs if i < x) for x in xs}
    yoff = {y: sum(hei[i] for i in ys if i < y) for y in ys}
    first = next(r for x, y, r in rs if x == xs[0] and y == ys[0])
    out = np.full((len(first.bands_meta), sum(hei.values()), sum(wid.values())), np.nan, dtype=np.float32)
    for x, y, r in rs:
        out[:, yoff[y]:yoff[y] + r.height, xoff[x]:xoff[x] + r.width] = r.as_numpy()
    return out, first.affine_trans, first.crs_wkt
```
