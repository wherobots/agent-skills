# Reference code: file selection, focal pipeline, tile index, manifest, single-pass check

Paste **after** `code_terrain_focal.md`, `code_indexes.md`, `code_sedona_udfs.md`,
`code_index_udfs.md` and `code_writer_layout.md` (it calls them). Everything here works on any raster source; the
Copernicus GLO-30 lines are examples, marked as such.

> The Spark parts follow code that ran green on Wherobots Cloud on 2026-10-07 (naive-user test:
> 4-file cross-seam slope, 0.000000 deg vs single pass; 16-tile index and manifest). The pure
> numpy parts (`stitch`, `seam_mask`, `compare_single_pass`) are unit-tested locally on synthetic
> multi-file surfaces, including the negative control (2026-10-08). The helpers as packaged here
> are *untested on the cluster* until the next acceptance run.

```python
import json

import numpy as np
try:  # Spark-side helpers; the pure functions above still import without Spark
    from pyspark.sql import functions as F
except ImportError:
    F = None


# ----------------------------------------------------------------------------- file selection

def files_for_aoi(sedona, source: dict, aoi_wkt: str, aoi_srid: int = 4326) -> list:
    """Source files (s3a:// paths) whose footprint meets the AOI. Fails loudly on an empty result:
    a name filter or glob that matches nothing returns 0 rows with no error.

    source = {"kind": "catalog_names", "table": ..., "names": [...]}   # fastest (2-4 s measured)
             {"kind": "catalog_footprint", "table": ..., "rast_col": "rast"}  # scans the table (~26 s global)
             {"kind": "glob", "glob": "s3a://bucket/prefix/*.tif"}     # lists + reads headers (~26 s)
             {"kind": "paths", "paths": [...]}                         # explicit; missing files fail
    Name and grid rules are source-specific; see `copernicus_glo30_names` for one example."""
    aoi = f"ST_GeomFromText('{aoi_wkt}', {aoi_srid})"
    kind = source["kind"]
    if kind == "catalog_names":
        df = (sedona.table(source["table"]).where(F.col(source.get("name_col", "name")).isin(source["names"]))
              .select(F.expr(f"RS_BandPath({source.get('rast_col', 'rast')})").alias("p")))
    elif kind == "catalog_footprint":
        r = source.get("rast_col", "rast")
        df = sedona.table(source["table"]).where(F.expr(f"RS_Intersects({r}, {aoi})")).select(F.expr(f"RS_BandPath({r})").alias("p"))
    elif kind == "glob":
        df = (sedona.read.format("raster").load(source["glob"])
              .where(F.expr(f"RS_Intersects(rast, {aoi})")).select(F.expr("RS_BandPath(rast)").alias("p")))
    elif kind == "paths":
        df = (sedona.createDataFrame([(p,) for p in source["paths"]], ["p"])
              .where(F.expr(f"RS_Intersects(RS_FromPath(p), {aoi})")))
    else:
        raise ValueError(kind)
    paths = sorted({r["p"] for r in df.where("p IS NOT NULL")
                    .select(F.regexp_replace("p", "^s3://", "s3a://").alias("p")).distinct().collect()})
    assert paths, f"files_for_aoi: no files for {source} and the AOI; check the name rule, glob, or AOI CRS"
    if kind == "catalog_names" and len(paths) < len(source["names"]):
        print(f"[warn] files_for_aoi: {len(paths)} of {len(source['names'])} named files found "
              "(missing files leave holes; expected only where the source has no data, e.g. open ocean)")
    print(f"[info] files_for_aoi: {len(paths)} files")
    return paths


def copernicus_glo30_names(xmin, ymin, xmax, ymax):
    """EXAMPLE name rule (Copernicus GLO-30 1-degree COGs): each file is named by its SW corner, so
    N37_00_W122_00 covers lat 37..38, lon -122..-121. Catalog `name` values include `.tif`."""
    out = []
    for lat in range(int(np.floor(ymin)), int(np.ceil(ymax))):
        for lon in range(int(np.floor(xmin)), int(np.ceil(xmax))):
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            out.append(f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM.tif")
    return out


# ----------------------------------------------------------------------------- focal pipeline

# What changes per operation (UDFs in code_sedona_udfs.md; all output float32 with NaN nodata
# except landform classes, uint8 with 0 nodata).
FOCAL_OPS = {
    "slope":   {"udf": "slope_nb_udf",   "halo": 1, "bands": "slope_deg"},
    "terrain": {"udf": "terrain_nb_udf", "halo": 1, "bands": "slope_deg;aspect_deg;hillshade"},
}


def focal_tiles(sedona, paths, tile_px: int, halo: int):
    """Out-db tiles of `paths` at tile_px, each with the neighbour files its halo needs, partitioned by
    tile count (the runtime autoscales, so start-up cores undercount)."""
    tiles = (sedona.createDataFrame([(p,) for p in paths], ["p"])
             .selectExpr(f"RS_TileExplode(RS_FromPath(p), {tile_px}, {tile_px}) AS (x, y, rast)"))
    px = abs(tiles.selectExpr("RS_ScaleX(rast) AS s").first()["s"])
    tiles = with_neighbour_files(tiles, pad_deg=2 * (halo + 1) * px).cache()   # pad in the raster CRS units
    n_tiles = tiles.count()
    cores = int(sedona.sparkContext.defaultParallelism)
    return tiles.repartition(max(n_tiles // 8, cores * 3)), n_tiles


def run_focal(sedona, tiles, udf, product: str, variant: str, cell: str, tiles_dir: str):
    """Compute, set nodata, name, PERSIST (so the write and the index do not run the UDF twice), write COGs.
    The index is then built from the written files (build_tile_index), which also proves they open."""
    out = (tiles.selectExpr("path", "x", "y", empty_template_sql("rast"), "rast", "neighbours")
           .select("path", "x", "y", udf(F.col("t"), F.col("rast"), F.col("neighbours")).alias("r"))
           .selectExpr("path", "x", "y", "RS_SetBandNoDataValue(r, 1, CAST('NaN' AS DOUBLE)) AS r")
           .withColumn("tile_name", F.expr(tile_name_expr("r", product, variant, cell)))
           .persist())
    n = out.select(F.count("r")).collect()[0][0]      # count(col): df.count() would prune the UDF
    # caller: out.unpersist() and tiles.unpersist() once the index is built (multi-product drivers)
    (out.selectExpr("RS_AsCOG(r) AS raster_binary", "tile_name AS path")
        .write.format("raster").option("rasterField", "raster_binary").option("pathField", "path")
        .option("fileExtension", ".tif").save(tiles_dir))
    return out, n


# ----------------------------------------------------------------------------- index and manifest

def build_tile_index(sedona, tiles_dir: str, index_dir: str, bands: str = "", with_stats: bool = True):
    """GeoParquet index of the WRITTEN tiles: full path, file name, footprint (raster CRS and EPSG:4326),
    size, SRID, nodata, and band-1 valid count/min/mean/max. with_stats=True reads every pixel once,
    which is the 'opens' check (RS_Width alone reads nothing); turn it off for very large outputs."""
    files = (sedona.read.format("binaryFile").option("pathGlobFilter", "*.tif").option("recursiveFileLookup", "true")
             .load(tiles_dir).select("path"))              # content is not read; paths come back as s3a://
    idx = files.selectExpr("path", "regexp_extract(path, '([^/]+)$', 1) AS tile_file", "RS_FromPath(path) AS r")
    cols = ["path", "tile_file", "RS_Envelope(r) AS footprint",
            "ST_Transform(RS_Envelope(r), CONCAT('EPSG:', RS_SRID(r)), 'EPSG:4326') AS geometry",
            "RS_SRID(r) AS srid", "RS_Width(r) AS width", "RS_Height(r) AS height", "RS_NumBands(r) AS n_bands",
            "RS_BandNoDataValue(r, 1) AS nodata", f"'{bands}' AS bands"]
    if with_stats:
        cols.append("RS_SummaryStatsAll(r, 1, true) AS st")
    idx = idx.selectExpr(*cols)
    if with_stats:
        idx = idx.selectExpr("*", "st.count AS valid_px", "st.min AS min_1", "st.mean AS mean_1", "st.max AS max_1").drop("st")
    idx.write.format("geoparquet").save(index_dir)
    rows = sedona.read.format("geoparquet").load(index_dir).count()
    assert rows > 0, f"build_tile_index: no .tif files under {tiles_dir}"
    return rows


def write_manifest(sedona, path: str, manifest: dict, overwrite: bool = False):
    """Write a JSON sidecar with the session's Hadoop FileSystem (same credentials as the raster writer).
    Refuses to overwrite by default: one run per stamp."""
    jvm = sedona.sparkContext._jvm
    p = jvm.org.apache.hadoop.fs.Path(path)
    fs = p.getFileSystem(sedona.sparkContext._jsc.hadoopConfiguration())
    if fs.exists(p) and not overwrite:
        raise IOError(f"write_manifest: {path} exists")
    out = fs.create(p, overwrite)
    out.write(bytearray(json.dumps(manifest, indent=2, default=str).encode()))
    out.close()
    return path


# ----------------------------------------------------------------------------- single-pass check

def collect_windows(sedona, paths, bbox, srid: int, col_expr: str = "RS_FromPath(p)"):
    """RS_AsInDB of each file clipped to bbox = (xmin, ymin, xmax, ymax) in the raster CRS:
    [(array (H, W) float32 with NaN nodata, ulx, uly, scale_x, scale_y)]. Small subsets only (driver)."""
    xmin, ymin, xmax, ymax = bbox
    wkt = f"POLYGON(({xmin} {ymin},{xmax} {ymin},{xmax} {ymax},{xmin} {ymax},{xmin} {ymin}))"
    rows = (sedona.createDataFrame([(p,) for p in paths], ["p"])
            .where(F.expr(f"RS_Intersects({col_expr}, ST_GeomFromText('{wkt}', {srid}))"))
            .selectExpr(f"RS_AsInDB(RS_Clip({col_expr}, 1, ST_GeomFromText('{wkt}', {srid}))) AS r").collect())
    out = []
    for row in rows:
        r = row["r"]
        a = r.as_numpy_masked()[0].astype(np.float32)
        at = r.affine_trans
        out.append((a, at.ip_x, at.ip_y, at.scale_x, at.scale_y))
    return out


def stitch(windows, ox, oy, sx, sy, n_rows, n_cols):
    """Paste windows onto an (n_rows, n_cols) NaN grid whose upper-left corner is (ox, oy), by
    geotransform. All windows must share the grid's pixel size and alignment."""
    g = np.full((n_rows, n_cols), np.nan, dtype=np.float32)
    for a, ulx, uly, wsx, wsy in windows:
        assert abs(wsx - sx) < 1e-12 and abs(wsy - sy) < 1e-12, "stitch: windows on a different grid"
        c0, r0 = int(round((ulx - ox) / sx)), int(round((uly - oy) / sy))
        cw = clip_window(c0, r0, a.shape[1], a.shape[0], n_cols, n_rows)
        if cw is None:
            continue
        gc, gr, w, h, ac, ar = cw
        piece = a[ar:ar + h, ac:ac + w]
        dst = g[gr:gr + h, gc:gc + w]
        dst[~np.isnan(piece)] = piece[~np.isnan(piece)]
    return g


def seam_mask(file_geos, ox, oy, sx, sy, n_rows, n_cols, width: int = 2):
    """True within `width` cells of every file edge inside the grid. Edges come from each file's own
    geotransform (ulx, uly, w, h), so half-pixel registration needs no special handling."""
    m = np.zeros((n_rows, n_cols), dtype=bool)
    for ulx, uly, w, h in file_geos:
        for x in (ulx, ulx + w * sx):
            c = int(round((x - ox) / sx))
            if 0 < c < n_cols:
                m[:, max(c - width, 0):c + width] = True
        for y in (uly, uly + h * sy):
            r = int(round((y - oy) / sy))
            if 0 < r < n_rows:
                m[max(r - width, 0):r + width, :] = True
    return m


def compare_single_pass(fn, src_windows, tiled_windows, file_geos, grid, halo: int, geographic: bool, tol: float):
    """Pure-numpy core of the check. grid = (ox, oy, sx, sy, n_rows, n_cols) is the AOI; source windows
    must cover it plus `halo` cells. fn(z, dx, dy) -> 2-D array.
      truth    : fn over the stitched multi-file source, one pass, trimmed
      tiled    : the written output, stitched
      control  : fn per file with no cross-file halo (edge-replicated), stitched -> MUST differ on seams
    PASS only if tiled matches truth within tol AND the control's seam error exceeds tol."""
    ox, oy, sx, sy, nr, nc = grid
    gox, goy, gnr, gnc = ox - halo * sx, oy - halo * sy, nr + 2 * halo, nc + 2 * halo
    if geographic:
        dx, dy = row_cell_sizes(goy, sx, sy, gnr, 0)
    else:
        dx, dy = abs(sx), abs(sy)
    z = stitch(src_windows, gox, goy, sx, sy, gnr, gnc)
    trim = (slice(halo, halo + nr), slice(halo, halo + nc))
    truth = fn(z, dx, dy)[trim]
    tiled = stitch(tiled_windows, ox, oy, sx, sy, nr, nc)
    control = np.full((gnr, gnc), np.nan, dtype=np.float32)
    for w in src_windows:                     # each file alone: what a per-file / no-halo pipeline computes
        zi = stitch([w], gox, goy, sx, sy, gnr, gnc)
        have = ~np.isnan(zi)
        if have.any():
            rr, cc = np.where(have)
            r0, r1, c0, c1 = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
            sub_dx = dx[r0:r1] if np.ndim(dx) else dx
            sub_dy = dy[r0:r1] if np.ndim(dy) else dy
            control[r0:r1, c0:c1] = fn(zi[r0:r1, c0:c1], sub_dx, sub_dy)
    control = control[trim]
    seams = seam_mask(file_geos, ox, oy, sx, sy, nr, nc)
    valid = np.isfinite(truth)

    def err(a, m):
        d = np.abs(a.astype(np.float64) - truth)
        bad = m & valid
        return float(np.nanmax(np.where(bad & np.isfinite(a), d, np.nan))) if bad.any() else float("nan"), \
            int((bad & ~np.isfinite(a)).sum())

    t_seam, t_seam_nan = err(tiled, seams)
    t_off, t_off_nan = err(tiled, ~seams)
    c_seam, c_seam_nan = err(control, seams)
    res = {"tiled_max_on_seams": t_seam, "tiled_max_off_seams": t_off, "tiled_nan_where_truth_valid": t_seam_nan + t_off_nan,
           "control_max_on_seams": c_seam, "control_nan_on_seams": c_seam_nan, "seam_cells": int(seams.sum()), "tol": tol}
    res["check_discriminates"] = bool((np.isfinite(c_seam) and c_seam > tol) or c_seam_nan > 0)
    worst = np.nanmax([t_seam, t_off]) if np.isfinite([t_seam, t_off]).any() else float("nan")
    res["tiled_ok"] = bool(np.isfinite(worst) and worst <= tol and res["tiled_nan_where_truth_valid"] == 0)
    res["PASS"] = res["tiled_ok"] and res["check_discriminates"]
    return res


def single_pass_check(sedona, src_paths, index_dir, bbox, fn, halo: int, tol: float):
    """Compare written tiles with a single pass over the merged source on bbox (raster CRS), with the
    negative control. Pick a bbox that straddles file seams and has relief (flat water cannot fail)."""
    geo = (sedona.createDataFrame([(p,) for p in src_paths], ["p"])
           .selectExpr("RS_UpperLeftX(RS_FromPath(p)) AS ulx", "RS_UpperLeftY(RS_FromPath(p)) AS uly",
                       "RS_Width(RS_FromPath(p)) AS w", "RS_Height(RS_FromPath(p)) AS h",
                       "RS_ScaleX(RS_FromPath(p)) AS sx", "RS_ScaleY(RS_FromPath(p)) AS sy", "RS_SRID(RS_FromPath(p)) AS srid").collect())
    sx, sy, srid = geo[0]["sx"], geo[0]["sy"], geo[0]["srid"]
    assert all(abs(g["sx"] - sx) < 1e-12 and abs(g["sy"] - sy) < 1e-12 and g["srid"] == srid for g in geo), \
        "single_pass_check: source files are not on one grid (pixel size or CRS differ); resample first"
    g0 = geo[0]                                # snap the AOI to the source grid
    ox = g0["ulx"] + np.floor((bbox[0] - g0["ulx"]) / sx) * sx
    oy = g0["uly"] + np.floor((bbox[3] - g0["uly"]) / sy) * sy
    nc, nr = int(round((bbox[2] - ox) / sx)), int(round((bbox[1] - oy) / sy))
    pad_bbox = (ox - halo * sx, oy + (nr + halo) * sy, ox + (nc + halo) * sx, oy - halo * sy)
    src = collect_windows(sedona, src_paths, pad_bbox, srid)
    bb = (ox, oy + nr * sy, ox + nc * sx, oy)
    bwkt = f"POLYGON(({bb[0]} {bb[1]},{bb[2]} {bb[1]},{bb[2]} {bb[3]},{bb[0]} {bb[3]},{bb[0]} {bb[1]}))"
    out_paths = [r["path"] for r in sedona.read.format("geoparquet").load(index_dir)       # only tiles in the bbox
                 .where(F.expr(f"ST_Intersects(footprint, ST_GeomFromText('{bwkt}', {srid}))")).select("path").collect()]
    tiled = collect_windows(sedona, out_paths, bb, srid)
    file_geos = [(g["ulx"], g["uly"], g["w"], g["h"]) for g in geo]
    return compare_single_pass(fn, src, tiled, file_geos, (ox, oy, sx, sy, nr, nc), halo, srid in (4326, 4269), tol)
```

## Driver skeleton for a job run

```python
import argparse
import time

from sedona.spark import SedonaContext

ap = argparse.ArgumentParser()
ap.add_argument("--output-path", required=True)     # no fallback to the caller's private folder
ap.add_argument("--run-stamp", default=time.strftime("%Y%m%d_%H%M%S"))
args = ap.parse_args()
sedona = SedonaContext.create(SedonaContext.builder().getOrCreate())

OUT = f"{args.output_path.rstrip('/')}/{args.run_stamp}/slope"
TILES_DIR, INDEX_DIR, MANIFEST = f"{OUT}/tiles", f"{OUT}/tiles_index.parquet", f"{OUT}/manifest.json"
for k, v in {"tiles": TILES_DIR, "index": INDEX_DIR, "manifest": MANIFEST}.items():
    print(f"[out] {k}={v}")

# ---- parameters (unattended defaults are recorded in the manifest)
AOI = (-122.6, 37.6, -121.4, 38.4)               # EXAMPLE: xmin, ymin, xmax, ymax in EPSG:4326
AOI_WKT = "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(*AOI)
SOURCE = {"kind": "catalog_names", "table": "wherobots_open_data.copernicus_dem.glo_30m",
          "names": copernicus_glo30_names(*AOI)}  # EXAMPLE source; any kind in files_for_aoi
OP, TILE_PX, PRODUCT, VARIANT, CELL = "slope", 2048, "slope", "halo", "30m"

paths = files_for_aoi(sedona, SOURCE, AOI_WKT)
tiles, n_tiles = focal_tiles(sedona, paths, TILE_PX, FOCAL_OPS[OP]["halo"])
out, n = run_focal(sedona, tiles, globals()[FOCAL_OPS[OP]["udf"]], PRODUCT, VARIANT, CELL, TILES_DIR)
rows = build_tile_index(sedona, TILES_DIR, INDEX_DIR, bands=FOCAL_OPS[OP]["bands"])
assert rows == n, f"index has {rows} files, computed {n} tiles"
write_manifest(sedona, MANIFEST, {"product": PRODUCT, "variant": VARIANT, "op": OP, "halo_cells": FOCAL_OPS[OP]["halo"],
                                  "tile_px": TILE_PX, "nodata": "NaN", "sources": paths, "aoi": AOI_WKT,
                                  "run_stamp": args.run_stamp, "tiles_dir": TILES_DIR, "index": INDEX_DIR, "n_tiles": rows})
# a 1-degree file corner with relief, in the raster CRS (EXAMPLE)
chk = single_pass_check(sedona, paths, INDEX_DIR, (-122.3, 37.7, -121.7, 38.3), slope, halo=1, tol=0.05)
print(f"[check] {chk}")
assert chk["PASS"], chk
```
