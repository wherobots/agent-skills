# Validated Spatial SQL Query Patterns

WherobotsDB (Apache Sedona / Spark SQL) patterns, each **validated against live compute**
on `wherobots_open_data` over a bounded downtown-SF window. Every template below executed
cleanly and returned sane results on the date noted.

**Standard test window (downtown SF):** `bbox.xmin > -122.42 AND bbox.xmax < -122.40 AND bbox.ymin > 37.77 AND bbox.ymax < 37.79`
**Reference point:** `ST_Point(-122.408, 37.784)` (lon, lat)
**CRS:** all catalog vector data is **EPSG:4326 (degrees)**. `ST_Point(x, y)` takes **(lon, lat)**.

> **Cost discipline:** every template prefilters on the scalar `bbox.xmin/xmax/ymin/ymax`
> doubles *before* any `ST_*` predicate. That range-filter is cheap and shrinks the spatial
> join input by orders of magnitude. Keep it in — do not run these unbounded.

---

## Pattern 1 — Point-in-polygon (POI within an admin area)

**Intent:** Count/select features that fall inside a named administrative boundary, using
Overture divisions rather than hard-coded WKT/bbox.
**CRS/units:** EPSG:4326; `ST_Intersects` is topological (no distance units involved).
**Validated:** 2026-07-09 → `places_in_sf = 9799`

```sql
SELECT COUNT(DISTINCT p.id) AS places_in_sf
FROM wherobots_open_data.overture_maps_foundation.places_place p
JOIN wherobots_open_data.overture_maps_foundation.divisions_division_area a
  ON ST_Intersects(a.geometry, p.geometry)
WHERE a.country = 'US' AND a.region = 'US-CA' AND a.subtype = 'county'
  AND a.names.primary = 'San Francisco'
  -- keep the bbox prefilter to bound the scan; drop it for a full-area run
  AND p.bbox.xmin > -122.42 AND p.bbox.xmax < -122.40
  AND p.bbox.ymin > 37.77 AND p.bbox.ymax < 37.79;
```

**Gotchas encountered (this pattern FAILED first):**
- First attempt used `a.subtype = 'locality'` and returned **0 rows with no error** — a silent
  logic failure. Diagnosis (`ST_Intersects(a.geometry, ST_Point(...))` over US-CA) showed the
  downtown point is covered by `subtype = 'county'` named **San Francisco** and `subtype = 'region'`
  named **California** — there is **no `locality` polygon** over SF (it's a consolidated city-county).
  **Lesson:** verify which `subtype` actually covers your area before trusting a subtype filter;
  a clean-executing query returning 0 is a failure, not an answer.
- The same diagnostic returned **duplicate area rows** per division (2× San Francisco county).
  A plain `COUNT(*)`/join would double-count. Use **`COUNT(DISTINCT p.id)`** (or dedupe the area
  side) whenever the divisions table may hold multiple perspective/area records per division.

---

## Pattern 2 — Distance / proximity filter

**Intent:** Find features within a distance of a point.
**CRS/units — the critical decision:**
- `ST_DWithin(g1, g2, distance, useSpheroid)` → `useSpheroid = true` ⇒ **distance in meters**
  (spheroid, on centroids). `useSpheroid = false`/omitted ⇒ Euclidean in **CRS units = degrees** for 4326.
- The template below uses **degrees** (`0.002° ≈ ~180 m` at this latitude) for the *filter*, and
  `ST_DistanceSphere` (**meters**, EPSG:4326 lon/lat) for the reported distance. Prefer the meter
  forms when the number must mean meters.
**Validated:** 2026-07-09 → 10 rows, nearest 12.9 m.

```sql
SELECT p.names.primary AS name, p.categories.primary AS category,
       ST_DistanceSphere(p.geometry, ST_Point(-122.408, 37.784)) AS meters
FROM wherobots_open_data.overture_maps_foundation.places_place p
WHERE p.bbox.xmin > -122.42 AND p.bbox.xmax < -122.40
  AND p.bbox.ymin > 37.77 AND p.bbox.ymax < 37.79
  AND ST_DWithin(p.geometry, ST_Point(-122.408, 37.784), 0.002)  -- degrees (useSpheroid defaults false)
ORDER BY meters
LIMIT 10;
```

**Gotchas:**
- Prefer `ST_DWithin` over `ST_Distance(a,b) < x` — it is the optimizer-supported distance predicate.
- To filter in **meters**, use `ST_DWithin(p.geometry, pt, 180, true)` (spheroid) — but note the
  spheroid form compares **centroids** of non-point geometries.
- `ST_DistanceSphere` requires EPSG:4326 in **lon/lat** order; use `ST_FlipCoordinates` if your
  data is lat/lon.

---

## Pattern 3 — Bounded region window (bbox prefilter + envelope)

**Intent:** Restrict a scan to a rectangular AOI cheaply. This is the building block under every
other pattern here.
**CRS/units:** EPSG:4326; envelope corners are `(xmin, ymin, xmax, ymax)` in degrees.
**Validated:** 2026-07-09 → `buildings = 3093`

```sql
SELECT COUNT(*) AS buildings
FROM wherobots_open_data.overture_maps_foundation.buildings_building b
WHERE b.bbox.xmin > -122.42 AND b.bbox.xmax < -122.40      -- cheap scalar prefilter
  AND b.bbox.ymin > 37.77 AND b.bbox.ymax < 37.79
  AND ST_Intersects(b.geometry, ST_PolygonFromEnvelope(-122.42, 37.77, -122.40, 37.79));
```

**Gotchas:**
- `ST_PolygonFromEnvelope(xmin, ymin, xmax, ymax)` — **not** (xmin, xmax, ymin, ymax). Mixing the
  order silently produces an empty or wrong envelope.
- The `bbox.*` scalar prefilter and the `ST_Intersects` envelope are complementary: the scalars
  prune the file scan; the envelope enforces exact geometry membership. The scalar filter alone
  keeps features whose *bounding box* falls in range (slightly looser at edges).

---

## Pattern 4 — Spatial join (buildings ⟷ places)

**Intent:** Relate two spatial datasets by containment — e.g. count POIs per building.
**CRS/units:** EPSG:4326; `ST_Contains` is topological.
**Validated:** 2026-07-09 → top building 195 places.

```sql
SELECT b.id AS building_id, b.names.primary AS building_name, COUNT(p.id) AS n_places
FROM wherobots_open_data.overture_maps_foundation.buildings_building b
JOIN wherobots_open_data.overture_maps_foundation.places_place p
  ON ST_Contains(b.geometry, p.geometry)
WHERE b.bbox.xmin > -122.42 AND b.bbox.xmax < -122.40
  AND b.bbox.ymin > 37.77 AND b.bbox.ymax < 37.79
  AND p.bbox.xmin > -122.42 AND p.bbox.xmax < -122.40      -- bound BOTH sides of the join
  AND p.bbox.ymin > 37.77 AND p.bbox.ymax < 37.79
GROUP BY b.id, b.names.primary
ORDER BY n_places DESC
LIMIT 10;
```

**Gotchas:**
- **Bound both sides** of a spatial join with bbox prefilters. Filtering only one side still forces
  the other table's full geometry set into the join.
- `ST_Contains(A, B)` is order-sensitive: A (polygon) contains B (point). A point never contains a polygon.
- `building_name` is frequently `NULL` (footprints without a name) — expected, not an error.

---

## Pattern 5 — Nearest-neighbor / KNN join (`ST_KNN`)

**Intent:** For each feature in a query set, find its k nearest features in an object set
(dataset-to-dataset, not a single point).
**CRS/units:** `ST_KNN(R, S, k, use_sphere)` — `use_sphere = true` ranks by great-circle distance;
non-point geometries use their centroid.
**Validated:** 2026-07-09 → 5 cafés × 3 nearest hotels = 15 rows.

```sql
SELECT q.qname AS coffee_shop, o.hname AS nearby_hotel,
       ST_DistanceSphere(q.geom, o.geom) AS meters
FROM (
  SELECT names.primary AS qname, geometry AS geom
  FROM wherobots_open_data.overture_maps_foundation.places_place
  WHERE bbox.xmin > -122.42 AND bbox.xmax < -122.40
    AND bbox.ymin > 37.77 AND bbox.ymax < 37.79
    AND categories.primary = 'coffee_shop' AND names.primary IS NOT NULL
  ORDER BY names.primary                    -- deterministic: LIMIT without ORDER BY varies run-to-run
  LIMIT 5                                   -- keep the query side tiny; KNN joins are expensive
) q
JOIN (
  SELECT names.primary AS hname, geometry AS geom
  FROM wherobots_open_data.overture_maps_foundation.places_place
  WHERE bbox.xmin > -122.42 AND bbox.xmax < -122.40
    AND bbox.ymin > 37.77 AND bbox.ymax < 37.79
    AND categories.primary = 'hotel' AND names.primary IS NOT NULL
) o
ON ST_KNN(q.geom, o.geom, 3, true);          -- R=queries, S=objects, k=3, use_sphere=true
```

**Gotchas:**
- `ST_KNN` is a **join predicate** (`ON ST_KNN(...)`), not a scalar function. First arg is the
  **query** side (R), second is the **object** side (S).
- **The 4-arg form applies no distance bound.** `ST_KNN(q, o, k, use_sphere)` searches the *whole*
  object set for every query row — safe here only because the query side is capped at 5 rows. Add the
  optional 5th `radius` argument (`ST_KNN(q, o, k, true, 25000)` — meters when `use_sphere = true`)
  before running this against a query set of any real size, or the join scans everything per row.
- **`use_sphere` must be `TRUE` on EPSG:4326 inputs**, otherwise `radius` is read as **degrees** —
  silently wrong in both directions (`25000` degrees matches the globe; `0.0002` matches nothing).
- **Non-point geometries are reduced to their centroid.** Fine for point↔point. Wrong for snapping a
  point to a line or polygon — use point-to-line distance (`ST_DistanceSphere(pt, line)`) instead.
- For a single reference location, you don't need `ST_KNN` — `ORDER BY ST_DistanceSphere(...) LIMIT k`
  is simpler and cheaper. Reserve `ST_KNN` for per-row nearest across two datasets.
- Ties: only returned if `spark.sedona.join.knn.includeTieBreakers=true`. `ST_AKNN` is the
  approximate (faster, less exact) variant for large inputs.

---

## Pattern 6 — Aggregation by admin region

**Intent:** Roll up a metric grouped by the administrative area each feature falls in.
**CRS/units:** EPSG:4326; `ST_Contains(area, point)`.
**Validated:** 2026-07-09 → `San Francisco = 4032`, `San Mateo County = 1316` (window straddles the line).

```sql
SELECT a.names.primary AS county, COUNT(DISTINCT p.id) AS n_places
FROM wherobots_open_data.overture_maps_foundation.places_place p
JOIN wherobots_open_data.overture_maps_foundation.divisions_division_area a
  ON ST_Contains(a.geometry, p.geometry)
WHERE a.country = 'US' AND a.region = 'US-CA' AND a.subtype = 'county'
  AND p.bbox.xmin > -122.47 AND p.bbox.xmax < -122.38
  AND p.bbox.ymin > 37.68 AND p.bbox.ymax < 37.74
GROUP BY a.names.primary
ORDER BY n_places DESC
LIMIT 10;
```

**Gotchas:**
- **Division names are not normalized:** the same query returns `San Francisco` (no suffix) and
  `San Mateo County` (with suffix). Don't assume a consistent `" County"` suffix — group on `id`
  and carry the name if you need exact keys.
- Use `COUNT(DISTINCT p.id)` for the same duplicate-area reason as Pattern 1.
- `ST_Contains(area, point)` assigns each point to one county; boundary-touching points are rare
  but use `ST_Intersects` if you must include them.

---

## Pattern 7 — Geometry transform & metric area/distance (`ST_Transform`)

**Intent:** Compute true metric area/length by projecting from geographic (degrees) to a
projected CRS (meters).
**CRS/units:** source `EPSG:4326`, target **`EPSG:32610`** (UTM zone 10N, meters — correct for SF).
`ST_Area` after transform returns **m²**.
**Validated:** 2026-07-09 → Moscone West ≈ 16,924 m².

```sql
SELECT b.id AS building_id, b.names.primary AS name,
       ST_Area(ST_Transform(b.geometry, 'EPSG:4326', 'EPSG:32610')) AS area_m2
FROM wherobots_open_data.overture_maps_foundation.buildings_building b
WHERE b.bbox.xmin > -122.42 AND b.bbox.xmax < -122.40
  AND b.bbox.ymin > 37.77 AND b.bbox.ymax < 37.79
  AND b.names.primary IS NOT NULL
ORDER BY area_m2 DESC
LIMIT 10;
```

**Gotchas:**
- `ST_Transform(geom, sourceCRS, targetCRS)` — pass CRS as strings (`'EPSG:4326'`). If you omit
  the source, Sedona reads it from the geometry's SRID, which may be unset (0) → wrong result.
- **Never** call `ST_Area`/`ST_Length` on raw 4326 geometry expecting meters — you get degrees²,
  which is meaningless. Transform first, or use spheroidal measures.
- Pick a target CRS appropriate to the AOI's longitude (UTM zone) or a national grid; a global
  `EPSG:3857` (Web Mercator) area is distorted away from the equator.

---

## Pattern 8 — Nested struct / array handling (`EXPLODE`)

**Intent:** Flatten Overture's nested list columns (here `taxonomy.hierarchy`) into rows for
aggregation. Applies equally to transportation `road_flags`, `speed_limits`, places `addresses`, etc.
**CRS/units:** n/a (attribute operation).
**Validated:** 2026-07-09 → 15 taxonomy nodes, top `services_and_business = 2458`.

```sql
SELECT taxonomy_node, COUNT(*) AS n
FROM (
  SELECT EXPLODE(p.taxonomy.hierarchy) AS taxonomy_node      -- EXPLODE alone in its own SELECT
  FROM wherobots_open_data.overture_maps_foundation.places_place p
  WHERE p.bbox.xmin > -122.42 AND p.bbox.xmax < -122.40
    AND p.bbox.ymin > 37.77 AND p.bbox.ymax < 37.79
    AND p.taxonomy.primary IS NOT NULL
)
GROUP BY taxonomy_node
ORDER BY n DESC
LIMIT 15;
```

**Gotchas:**
- **`EXPLODE()` cannot be nested inside another expression** (Spark restriction). Put it alone in a
  subquery/CTE `SELECT`, then filter/aggregate in the outer query. `COUNT(EXPLODE(...))` fails.
- `EXPLODE` drops rows where the array is NULL/empty; use `EXPLODE_OUTER` to keep them as NULLs.
- Access struct fields with dot notation (`taxonomy.primary`); explode only the **list** parts.
  For linearly-referenced transportation lists, explode the list, then read `.value`/`.between`.

---

### Coverage note
All 8 canonical patterns validated 2026-07-09 against Overture release `2026-06-17.0`
(current `main`). Re-validate after an Overture release bump if a schema-dependent field
(e.g. `taxonomy`, division `subtype` set) changes. See `wherobots-open-data-catalog/references/catalog-map.md`
for schemas and the release-snapshot history.
