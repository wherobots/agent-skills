# Areal (Area-Weighted) Interpolation — Wherobots Patterns

Transfer a value measured on one polygon layer (**source**) onto a different, non-coincident polygon
layer (**target**) — e.g. Census Block Group population → NYC neighborhoods, or county stats → your
service areas. Also called spatial **enrichment** or areal **apportionment**.

Adapted from a PostGIS recipe into WherobotsDB (Apache Sedona). **The SQL is not a straight port** —
PostGIS `::geography`, `<->`, `cross join lateral`, and `ogr2ogr`/GeoPackage/shapefile ingest do not
carry over. Sedona idioms and this catalog's quirks are used instead.

> Core templates validated on live compute **2026-07-14** against `wherobots_open_data` (Overture
> release `2026-06-17.0`) over a bounded SF/San Mateo window. Results quoted inline.

---

## The one decision that determines correctness: extensive vs. intensive

Before writing any SQL, **classify every variable you are interpolating.** Getting this wrong yields
plausible-but-wrong numbers, silently.

| Kind | Examples | Meaning | How to interpolate |
|------|----------|---------|--------------------|
| **Extensive** | population, household count, total income ($), building count | A **total** that scales with area; splitting the polygon splits the count | Multiply by the **source-area fraction** and **SUM**: `Σ value · area(∩)/area(source)` |
| **Intensive** | median age, median income, density, % impervious, temperature | A **rate/ratio/average** that does **not** scale with area | **Area-weighted average**, weighting by intersection area: `Σ(value · area(∩)) / Σ(area(∩))` — never divide by source area |

The source book imports population (extensive), median age, and median income (both **intensive**)
and shows only the population formula. Applying the population formula to median age/income would be
**wrong** — it would sum rates. Use the intensive formula for those.

Equations:
```
Extensive (apportion a total):
    target_value = Σ_over_source  value · ( area(intersection(source, target)) / area(source) )

Intensive (blend a rate):
    target_value = Σ( value · area(intersection) ) / Σ( area(intersection) )
```

---

## Pattern A — Extensive interpolation (VALIDATED)

Interpolate a count-like total. Below: Overture county `population` → a synthetic target polygon.

```sql
WITH target AS (                      -- your target polygons; here a synthetic AOI envelope
  SELECT 'downtown_metro' AS target_id,
         ST_PolygonFromEnvelope(-122.47, 37.68, -122.38, 37.80) AS geom
),
src AS (   -- ONE row per source entity: dissolved land geometry + total area + the value
  SELECT a.division_id,
         first(a.names.primary) AS name,
         first(d.population)    AS population,
         ST_MakeValid(ST_Union_Aggr(a.geometry)) AS geom
  FROM wherobots_open_data.overture_maps_foundation.divisions_division_area a
  JOIN wherobots_open_data.overture_maps_foundation.divisions_division d
    ON a.division_id = d.id
  WHERE a.country = 'US' AND a.region = 'US-CA' AND a.subtype = 'county'
    AND a.is_land = true                                   -- land only (see CRS/denominator gotchas)
    AND a.bbox.xmin < -122.38 AND a.bbox.xmax > -122.47    -- cheap AOI prefilter
    AND a.bbox.ymin < 37.80  AND a.bbox.ymax > 37.68
  GROUP BY a.division_id                                   -- dissolve multipart → one geom/entity
)
SELECT t.target_id,
       SUM(s.population * (ST_Area(ST_Intersection(s.geom, t.geom)) / ST_Area(s.geom))) AS pop_est
FROM target t
JOIN src s ON ST_Intersects(s.geom, t.geom)                -- only overlapping pairs
GROUP BY t.target_id;
```
**Validated 2026-07-14:** land-only per-source contributions SF 548,502 (fraction 0.6276 × 873,965) +
San Mateo 14,582 (0.0191 × 765,135) = **`pop_est ≈ 563,084`**. (The same aggregation without the
`is_land` filter returns only 127,318 — the territorial-water denominator gotcha below, quantified.)

---

## Pattern B — Intensive interpolation (area-weighted mean)

For rates/averages (median income, density). Same intersection-area machinery, different formula.
```sql
SELECT t.target_id,
       SUM(s.median_income * ST_Area(ST_Intersection(s.geom, t.geom)))
         / SUM(ST_Area(ST_Intersection(s.geom, t.geom))) AS income_awm
FROM target t
JOIN src s ON ST_Intersects(s.geom, t.geom)
GROUP BY t.target_id;
```
> Uses the identical `ST_Area(ST_Intersection(...))` weighting proven in Pattern A. Validate on a
> bounded sample with your real source table before shipping. Note: area-weighting a **median** is an
> approximation (you can't exactly recombine medians); it is the standard, accepted one.

---

## Non-negotiable gotchas (each hit while validating)

| Gotcha | Why it bites | Fix |
|--------|-------------|-----|
| **Source must be 1 row per entity** | `divisions_division_area` stores **multiple polygon parts per division** (land / territorial water / islands). Naively, San Francisco appeared **twice** with fractions 0.628 and 0.134 — double-counting. | Dissolve first: `ST_Union_Aggr(geometry) GROUP BY <entity_id>`. Denominator must be the entity's **total** area, not one part. |
| **Territorial-water inflates the denominator** | Admin polygons include offshore extent (Farallones, territorial sea), so `area(source)` balloons and land fractions look too small. Without the land filter SF's fraction dropped from 0.628 → ~0.13. | Filter `is_land = true` (Overture divisions carry `is_land`/`is_territorial`) before dissolving, when interpolating land-based quantities. |
| **Invalid geometries throw / mis-area** | `ST_Intersection` on self-intersecting rings errors or returns wrong area. | Wrap source **and** target in `ST_MakeValid(...)` before intersecting. |
| **Extensive vs intensive** | Summing an intensive variable (median income) with the extensive formula is silently wrong. | Classify first (top of doc); use the matching formula. |
| **CRS of the area ratio** | On EPSG:4326 `ST_Area` returns **degrees²**. The *ratio* area(∩)/area(source) approximately cancels units, so Pattern A works un-projected — but it is only **exact under an equal-area projection**, because degrees² distortion varies with latitude across large polygons. | For accuracy (large/high-latitude polygons, or absolute areas), `ST_Transform` both sides to an equal-area CRS (e.g. `EPSG:5070` CONUS Albers, or a local UTM) before `ST_Area`. Never use the raw-degree ratio for absolute area. |
| **Cost: intersection is expensive** | `ST_Intersection` runs per candidate pair. | Keep the `ST_Intersects` join predicate + bbox/AOI prefilter so only overlapping pairs reach `ST_Intersection`. Cap runaway weights with `LEAST(1.0, frac)` if source/target share edges. |

---

## Advanced — dasymetric refinement (better than uniform)

Plain area weighting assumes population is spread **uniformly** inside each source polygon. It isn't —
people live in buildings, not parks or water. Dasymetric interpolation weights by an **ancillary**
layer. Wherobots ships the ancillary data in the open-data catalog, so this is native:

- **Overture buildings** (`overture_maps_foundation.buildings_building`) — weight by building
  footprint area (or count) inside each intersection instead of raw intersection area.
- **Overture base land use / land cover** (`base_land_use`, `base_land_cover`) — mask out
  non-residential area before weighting.

Sketch (weight extensive value by residential building footprint area, not polygon area):
```sql
-- weight_i = building_footprint_area(source_i ∩ target) / building_footprint_area(source_i)
-- target_value = Σ value_i · weight_i
```
> Not yet validated end-to-end here — build it on a bounded AOI and check that Σ weights over a full
> source polygon ≈ 1.0 before trusting it. The area-ratio components are the same validated primitives
> from Pattern A.

---

## Getting the data in (Wherobots ≠ PostGIS)

The source recipe uses `ogr2ogr` to load a GeoPackage/GDB into PostGIS and `ELT`-joins the ACS
columns in-database. On Wherobots:

- **Do not** ingest shapefiles/GeoPackage as a pipeline format. Land the source (e.g. ACS Block
  Groups) as a **Havasu/Iceberg** Bronze table (GeoParquet for file output) — see the
  `wherobots-pipeline-designer` skill's layer contracts. Enforce explicit types; **never `inferSchema`**.
- The book's "8,319-column, select-only-what-you-need" advice still applies: project just the join key
  + the few variables you interpolate at Bronze.
- Keep the geometry column named `geometry`; store in EPSG:4326.
- For named target boundaries (counties, localities, neighborhoods) prefer joining Overture
  `divisions_division_area` over importing your own — see `open-data-catalog`.

## Related
- `spatial-sql-patterns` — the underlying join/CRS/aggregation primitives (Pattern 7 = `ST_Transform`).
- `open-data-catalog` — `divisions_division`(+`_area`) schema, `is_land`, the population join key, buildings/land-use layers.
- `wherobots-pipeline-designer` — productionizing this as Bronze→Silver→Gold with reprocessable tables.
