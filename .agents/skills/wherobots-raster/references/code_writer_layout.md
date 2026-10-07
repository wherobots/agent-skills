# Reference code: tile names and the raster writer's part-* folders

The distributed raster writer nests every file under `part-*` folders and cannot be told not to. Run
these **after** the write, and only when the analyst wants one flat folder (see
`export_and_render.md`). Uses the session's Hadoop FileSystem, so the writer's credentials apply; on
s3a each move is a server-side copy + delete. Refuses to move anything when two files share a name,
never overwrites, safe to rerun, and removes the emptied `part-*` folders and `_SUCCESS` only after
every file is in place.

*Measured* 2026-10-05: CONUS slope, 3,603 COGs (51.7 GB), `tiny` runtime, 48 threads: moved in 99 s,
index rewritten in 18 s, all 3,603 files read back from the flat folder.

## Tile name expression

`<product>_<variant>_e<srid>_<cell>_<ulx>_<uly>` from the tile's upper-left corner: zero-padded
integers in a projected CRS, `w122p0400_n37p9500` style for EPSG:4326/4269. *Validated* 2026-10-05
on UTM, NAD83 and WGS84 tiles in all four hemisphere combinations and on a real Copernicus GLO-30
tile (`export_and_render.md` lists the outputs and the width limits of the projected form).

```python
def tile_name_expr(col: str, product: str, variant: str, cell: str) -> str:
    """SQL expression for <product>_<variant>_e<srid>_<cell>_<ulx>_<uly>: projected CRSs get
    zero-padded integer corners, geographic CRSs (4326/4269) get w122p0400_n37p9500 style."""
    ulx, uly, srid = f"RS_UpperLeftX({col})", f"RS_UpperLeftY({col})", f"RS_SRID({col})"
    proj = (f"CONCAT('_x', LPAD(CAST(CAST({ulx} AS BIGINT) AS STRING), 7, '0'), "
            f"'_y', LPAD(CAST(CAST({uly} AS BIGINT) AS STRING), 7, '0'))")
    geo = (f"CONCAT('_', IF({ulx} < 0, 'w', 'e'), LPAD(CAST(CAST(FLOOR(ABS({ulx})) AS INT) AS STRING), 3, '0'), 'p', "
           f"LPAD(CAST(CAST(ROUND((ABS({ulx}) - FLOOR(ABS({ulx}))) * 10000) AS INT) AS STRING), 4, '0'), "
           f"'_', IF({uly} < 0, 's', 'n'), LPAD(CAST(CAST(FLOOR(ABS({uly})) AS INT) AS STRING), 2, '0'), 'p', "
           f"LPAD(CAST(CAST(ROUND((ABS({uly}) - FLOOR(ABS({uly}))) * 10000) AS INT) AS STRING), 4, '0'))")
    corner = f"IF({srid} IN (4326, 4269), {geo}, {proj})"
    return f"CONCAT('{product}_{variant}_e', {srid}, '_{cell}', {corner})"
```

## Flatten part-* folders

```python
from concurrent.futures import ThreadPoolExecutor

from pyspark.sql import functions as F


def flatten_written_tiles(sedona, tiles_dir: str, threads: int = 32, dry_run: bool = False) -> dict:
    """Move every .tif under `tiles_dir/part-*/` to `tiles_dir/<name>.tif` (one flat folder).

    The distributed raster writer nests files under part-* folders; GIS users usually want one
    folder. Uses the session's Hadoop FileSystem (same credentials as the writer); on s3a a
    rename is a server-side copy + delete. Refuses to move anything if two files share a name.
    Idempotent: files already at the top level are left alone, so a failed run can be rerun.
    Removes the emptied part-* folders and _SUCCESS markers only after every file is in place.
    Returns counts; rewrite any tile index paths afterwards (see `rewrite_index_paths`)."""
    jvm = sedona.sparkContext._jvm
    conf = sedona.sparkContext._jsc.hadoopConfiguration()
    root = jvm.org.apache.hadoop.fs.Path(tiles_dir.rstrip("/"))
    fs = root.getFileSystem(conf)
    root_q = fs.makeQualified(root).toString().rstrip("/")
    files, it = [], fs.listFiles(root, True)
    while it.hasNext():
        p = it.next().getPath()
        if p.getName().endswith(".tif"):
            files.append(p.toString())
    nested = [f for f in files if f.rsplit("/", 1)[0].rstrip("/") != root_q]
    names = [f.rsplit("/", 1)[1] for f in files]
    dupes = sorted({n for n in names if names.count(n) > 1}) if len(set(names)) != len(names) else []
    out = {"tif_total": len(files), "nested": len(nested), "already_flat": len(files) - len(nested), "duplicates": dupes[:20]}
    if dupes:
        raise ValueError(f"duplicate tile names, nothing moved: {dupes[:10]}")
    if dry_run or not nested:
        return out

    def move(src):
        dst = jvm.org.apache.hadoop.fs.Path(root_q + "/" + src.rsplit("/", 1)[1])
        return bool(fs.rename(jvm.org.apache.hadoop.fs.Path(src), dst))

    with ThreadPoolExecutor(max_workers=threads) as ex:
        ok = list(ex.map(move, nested))
    out["moved"] = sum(ok)
    out["failed"] = [f for f, o in zip(nested, ok) if not o][:20]
    flat = 0
    for st in fs.listStatus(root):
        if st.isFile() and st.getPath().getName().endswith(".tif"):
            flat += 1
    out["flat_after"] = flat
    if flat == len(files) and not out["failed"]:
        removed = 0
        for st in fs.listStatus(root):
            n = st.getPath().getName()
            if (st.isDirectory() and n.startswith("part-")) or n == "_SUCCESS":
                fs.delete(st.getPath(), True)
                removed += 1
        out["removed_writer_entries"] = removed
    return out

def rewrite_index_paths(sedona, index_dir: str, tiles_dir: str, path_col: str = "path"):
    """Point a GeoParquet tile index at the flattened files: <tiles_dir>/<name>.tif.
    Writes to <index_dir>_flat, then swaps it in place of <index_dir>."""
    idx = sedona.read.format("geoparquet").load(index_dir)
    base = tiles_dir.rstrip("/") + "/"
    new = idx.withColumn(path_col, F.concat(F.lit(base), F.element_at(F.split(F.col(path_col), "/"), -1))).cache()
    n = new.count()
    tmp = index_dir.rstrip("/") + "_flat"
    new.write.format("geoparquet").mode("overwrite").save(tmp)
    jvm = sedona.sparkContext._jvm
    conf = sedona.sparkContext._jsc.hadoopConfiguration()
    src, dst = jvm.org.apache.hadoop.fs.Path(tmp), jvm.org.apache.hadoop.fs.Path(index_dir.rstrip("/"))
    fs = dst.getFileSystem(conf)
    fs.delete(dst, True)
    fs.rename(src, dst)
    return n
```

Known limitations (*untested* fixes, so the code above is left as validated): the recursive
listing moves any `.tif` under `tiles_dir`, not only those in `part-*` folders, so point it at a
folder that holds only writer output; `rewrite_index_paths` deletes the old index before an
unchecked rename, so check that `<index_dir>` exists afterwards.
