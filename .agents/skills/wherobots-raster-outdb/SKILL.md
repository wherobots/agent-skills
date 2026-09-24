---
name: wherobots-raster-outdb
description: Use when processing rasters in WherobotsDB for zonal statistics, map algebra, tiling, NDVI or band math over COGs. Covers which functions preserve out-db (lazy) references and which silently materialize pixels, how to check, and the many-zones pattern.
---

# Out-DB Rasters: when pixels actually get read

WherobotsDB rasters are **out-db by default**: the row holds a *reference* to remote pixel data,
and pixels are fetched on demand. This is what makes terabyte imagery tractable.

**The failure mode this skill exists for: some functions silently materialize.** They return
correct results with the right shape, raise no warning, and cost orders of magnitude more. You
cannot see it in the output, and the function reference does not say which functions do it.

## Check it in one line

Cast the raster to a string. The class name tells you everything:

```sql
SELECT CAST(rast AS STRING) FROM my_rasters LIMIT 1
```

| what you see | meaning |
|---|---|
| `LazyLoadOutDbGridCoverage2D[not loaded]` | out-db, nothing read yet |
| `OutDbGridCoverage2D["outDbCoverage", ...]` | out-db, still a reference |
| `GridCoverage2D["outDbCoverage", ...]` | in-db class, out-db provenance (e.g. `RS_Union`) |
| `GridCoverage2D["genericCoverage", ...]` | **materialized** — pixels computed |

**Read the CLASS, not the coverage name, and mind the case.** `OutDbGridCoverage2D` is the
class; `"outDbCoverage"` is only a name and appears on in-db results too. A substring test for
`OutDb` misreads `"outDbCoverage"` because of the lowercase first letter.

**This check is reliable for a STORED column, not for an inline expression.** Casting an
expression to a string forces it to evaluate, so the class you see may be an artifact of the
probe. Use it on a view or table you built; use timing to judge an expression mid-pipeline.

## Verified behaviour

Measured on WherobotsDB runtime 2.34.19, Sentinel-2 L2A COGs (2026-09-23). Re-check on
upgrades; treat anything not listed as unknown until you cast-and-look.

| function | out-db preserved? |
|---|---|
| `RS_FromPath(path)` | **yes** — `LazyLoadOutDbGridCoverage2D[not loaded]` |
| `RS_TileExplode(rast, w, h)` | **yes** — tiles stay references into the same remote file |
| `RS_StackTileExplode(ARRAY(r1,r2,r3), ref, w, h)` | **NO — materializes every tile** |
| `RS_ZonalStats(outdb_rast, geom, ...)` | **yes** — reads only the pixels in the zone |
| `RS_MapAlgebra(ARRAY(outdb, outdb, outdb), ...)` | not supported — `DATATYPE_MISMATCH` |

## The pattern that matters: many small zones, large rasters

If you are sampling vector geometries against imagery — buildings, parcels, buffers, plots —
**go straight from the out-db raster to `RS_ZonalStats`.** The engine materializes only the
pixels inside each zone.

```sql
-- GOOD: pixels read ~= zone area
SELECT z.id, RS_ZonalStats(RS_FromPath(i.cog_path), z.geom, 1, 'mean', TRUE) AS mean_val
FROM zones z, items i
WHERE ST_Intersects(i.footprint, z.geom)
```

```sql
-- BAD: materializes whole tiles to read a sliver of each
--   RS_StackTileExplode -> RS_MapAlgebra -> RS_ZonalStats
-- Correct output, no error, orders of magnitude more pixel reads.
```

**Measured difference, same workload, 1,087 zones against one Sentinel-2 scene:**

| approach | time |
|---|---|
| zonal-reduce the raw bands out-db, then compute the index | **11.6 s** |
| tile, map-algebra per pixel, then zonal-reduce | **56.6 s** |

**About 5x, not the orders of magnitude a pixel-count argument suggests.** Zone area over
raster area says these buffers touch under 1% of the scene, which looks like a ~100x argument;
the measured gap is 4.9x because per-zone overhead dominates once the read is windowed.
**Measure your own ratio rather than deriving it** — an estimate from pixel counts was wrong
here by more than two orders of magnitude.

5x is a scheduling difference, not an architectural one. It is rarely a good reason on its own
to change what a number means (see below).

## When you genuinely need per-pixel band math

`RS_MapAlgebra` takes **one raster** and indexes its **bands** (`rast[0]`, `rast[1]`). It does
not take an array of rasters: `RS_MapAlgebra(ARRAY(r1, r2), ...)` fails with
`DATATYPE_MISMATCH` regardless of whether the inputs are out-db or the same shape. Combine
bands first with `RS_Union`, which produces a real multi-band raster.

Cross-band arithmetic then needs those co-registered bands, and computing new pixel values
materializes by definition. There is no free lunch. Decide deliberately:

- **Reduce over the raw bands, then compute the index** from the reduced values: fully out-db
  and roughly 5x faster. But `mean(NDVI) != NDVI(mean)` — a ratio of means is not a mean of
  ratios. Measured on one real scene and 1,087 zones, the two routes returned **0.4377** and
  **0.4647**. **This changes what your number means.** Do not adopt it silently as an
  optimization; it is a specification decision, and at 5x the speed is seldom the deciding
  argument.
- **Per-pixel index, then reduce**: materializes, and costs accordingly. Budget for it, restrict
  the item set spatially first, and run it as a batch job rather than interactively.

Whichever you choose, write down which one it is. Downstream consumers cannot tell from the
column name.

## Other traps on this path

- **`RS_FromPath` applies the GeoTIFF's own scale/offset by default.** Pass
  `'raster.reader.auto-rescale=false'` for raw DNs. If the scale and offset live in STAC rather
  than in the TIFF tags (Sentinel-2 does this), the reader has nothing to apply and you MUST
  apply them yourself — and then the flag matters, because if the tags ever appear your offset
  double-applies silently.
- **`RS_MapAlgebra` runs Jiffle, not C.** No type declarations: `double x = rast[0];` fails to
  parse with `extraneous input 'double'`. Write `x = rast[0];`. Mask with
  `con(cond, NULL, value)` — `NULL` is the no-data marker that `excludeNoData` honors, and an
  unguarded expression happily turns a nodata pixel into a plausible-looking value.
- **`COUNT(*)` does not force map algebra to evaluate.** A query that counts rows will succeed
  over a script that cannot even parse. Force evaluation with `RS_Value`, `RS_NumBands` or a
  zonal stat when you are testing whether a script actually works.

See `references/outdb-checks.md` for the diagnostic queries.
