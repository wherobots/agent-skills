# Wherobots Open Data Catalog — Table Selection Map

**Catalog:** `wherobots_open_data`
**Mapped:** 2026-07-09 (via Wherobots MCP against live compute)
**Overture release then current (`main`):** `2026-06-17.0`

> **What this file is — and is not.** It records what you cannot get from a tool call: which table to
> pick for a question, the join keys between them, and the behaviors that produce silently wrong
> answers. It deliberately does **not** transcribe full column lists — run `describe_table` for those.
> Column *sets* change with every Overture release; the join keys and quirks below have been stable
> across releases, which is why they are worth writing down.
>
> **Refresh cadence:** re-check after an Overture release bump (monthly), and any time a query fails
> on a column this file names. Update the "Mapped" date above when you do.

---

## 1. Database overview

`wherobots_open_data` holds **7 databases / 30 tables**:

| Database | Tables | What it is |
|---|---|---|
| `overture_maps_foundation` | 17 | Overture Maps: places, buildings, transportation, divisions, addresses, base layers, geocoder |
| `foursquare` | 3 | Foursquare Open Places (POIs), category taxonomy, and incremental deltas |
| `copernicus_dem` | 1 | Copernicus GLO-30 (30m) global DEM as **raster** tiles |
| `noaa` | 1 | NOAA storm warning polygons (VTEC) |
| `partner_samples` | 1 | Regrid US parcel sample (rich attribute set) |
| `rasterflow_output_samples` | 3 | Example vectorized outputs from RasterFlow ML models |
| `spatial_knowledge_graph` | 4 | Precomputed node/edge graph (SF + Manhattan) over Overture GERS entities |

All vector tables are Iceberg/Havasu with native `geometry` columns. Assume **EPSG:4326 (degrees)**
unless a column proves otherwise. Copernicus is a `raster` column, not vector.

---

## 2. Which Overture table answers which question

Every Overture table carries `id` (GERS string ID), `geometry`, `bbox`, `sources`, `version`, and
most carry `names` (`struct<primary, common:map, rules:list>`). **Filter on the scalar
`bbox.xmin/xmax/ymin/ymax` doubles to window a region before any spatial predicate** — the cheapest
prefilter available, and used in every template in `wherobots-spatial-sql-patterns`.

| Question | Table | Geometry | Scale | What to know before querying |
|---|---|---|---|---|
| Where are the POIs? | `places_place` | Point | ~59M | **Two category systems coexist and disagree** (§4.1). `confidence` is 0–1; a meaningful fraction of rows have no category at all. |
| Where are the buildings? | `buildings_building` | Polygon | ~785M | `height`/`min_height`/`roof_height` in **meters**; `num_floors` int. `has_parts = true` → join `buildings_building_part.building_id = building.id` for 3D detail. |
| Roads, rail, waterways? | `transportation_segment` | LineString | ~294M | The most complex schema here. Attributes are **linearly referenced**, not scalars (§4.4). `subtype` = `road`/`rail`/`water`; `class` = `motorway`/`primary`/`residential`/`footway`/… |
| Road network topology? | `transportation_connector` | Point | ~330M | Nodes only. Join `segment.connectors[].connector_id → connector.id`; `connectors[].at` is position 0–1 along the segment. |
| Named admin boundaries? | `divisions_division_area` | Polygon | — | **The one you `ST_Intersects` against.** See §3 — has the most consequential quirks in the catalog. |
| Street addresses? | `addresses_address` | Point | — | `street`/`number`/`unit`/`postcode`/`postal_city`/`country`. No `names` struct. |
| Land, water, land use, land cover? | `base_land`, `base_water`, `base_land_use`, `base_land_cover`, `base_infrastructure`, `base_bathymetry` | mostly Polygon | — | All keyed on `subtype`/`class`. `base_land_cover` is minimal (`subtype` + cartography) — no `names`/`class`. `base_water.is_salt`/`is_intermittent` are booleans. Useful as dasymetric ancillary layers. |
| Text → location? | `geocodes`, `geocodes_index` | Point | — | `geocodes.id` is **`long`**, not a GERS string (§4.6). `geocodes_index` is an inverted index: `token_phrase` → `address_ids` (list<long>) → `geocodes.id`. |

For anything not listed, `list_tables` + `describe_table` beats guessing — the point of this table is
to stop you querying `places_place` when you wanted `divisions_division_area`.

---

## 3. Divisions — the join keys that matter

Use divisions for **all geographic filtering**; never hard-code WKT/bbox for an admin area.

Three tables, one entity:

| Table | Geometry | Role | Key |
|---|---|---|---|
| `divisions_division` | Point | The division **record** and its attributes — including `population` (int), `admin_level`, `wikidata`, `hierarchies`, `parent_division_id` | `id` |
| `divisions_division_area` | Polygon | The division's **area** — what you intersect against | `division_id` → `divisions_division.id` |
| `divisions_division_boundary` | LineString | Shared borders, incl. `is_disputed` | `division_ids` (list) |

Both area and boundary carry `country` (ISO2), `region` (e.g. `US-CA`), `subtype`
(`country`/`region`/`county`/`locality`), `admin_level`, `class`, and the booleans
**`is_land`** / **`is_territorial`**.

**Canonical boundary join:**

```sql
JOIN wherobots_open_data.overture_maps_foundation.divisions_division_area a
  ON ST_Intersects(a.geometry, b.geometry)
WHERE a.country = 'US' AND a.region = 'US-CA' AND a.subtype = 'region'
```

**Attributes live on the other table.** `population` is on `divisions_division`, geometry is on
`divisions_division_area` — join `a.division_id = d.id` to get both.

Two behaviors that produce wrong answers rather than errors — both hit while validating:

- **Multiple polygon parts per division** (land / territorial water / islands), so a naive join
  double-counts and an area denominator is inflated. Filter `is_land = true` and/or dissolve with
  `ST_Union_Aggr(geometry) GROUP BY division_id` before any area math. Quantified in
  `wherobots-area-weighted-interpolation`: SF's land fraction of a test AOI moved from 0.13 → 0.63
  once territorial water was excluded.
- **The `subtype` you expect may not exist.** San Francisco has no `locality` polygon — it is a
  consolidated city-county, so it appears as `subtype = 'county'`. A `subtype` filter that returns 0
  rows is a logic failure, not an answer; verify which subtype actually covers your AOI first.

---

## 4. Non-Overture databases

| Table | Type | What to know |
|---|---|---|
| `foursquare.places` | Point, ~105M | Flat schema — no nested structs. **Geometry column is `geom`, not `geometry`.** Categories are parallel arrays `fsq_category_ids` / `fsq_category_labels`; `latitude`/`longitude` columns exist alongside `geom`. Choose this over `places_place` when you want flat columns; choose Overture when you want the hierarchy. |
| `foursquare.categories` | 1,245 rows | Flattened 6-level taxonomy: `category_id`, `category_level`, plus `level1_..level6_category_id`/`_name`. Join `places.fsq_category_ids[] → categories.category_id`. |
| `foursquare.deltas` | ~7.4M | `fsq_place_id`, `action`, `redirect` — incremental changes applied on top of a `places` snapshot. |
| `copernicus_dem.glo_30m` | **raster** | Raster column is `rast`; `footprint` is its geometry, `x`/`y` are tile indices. Use `RS_*` functions on `rast`, never vector `ST_*`. |
| `noaa.storm_warnings` | Polygon | VTEC warnings. **All column names are UPPERCASE** (`PHENOM`, `ISSUED`, `EXPIRED`, `WINDTAG`, …) — match case exactly. Timestamps are timestamptz. |
| `partner_samples.regrid_parcels` | Polygon | 139-column US parcel schema. The clusters worth knowing: identity (`geoid`, `parcelnumb`), use/zoning (`usecode`/`usedesc`, `zoning`, `lbcs_*`), valuation (`landval`/`improvval`/`parval`, `saledate`, `saleprice`), and hazard/census joins (`census_*`, `fema_flood_zone*`). `describe_table` for the rest. |
| `rasterflow_output_samples.*` | Polygon/Line | Vectorized ML outputs: `fields_of_the_world_vector_global` (ag field boundaries), `chesapeakersc_vector` and `tile2net_vector` (both carry `score_mean`/`score_std`). Samples, not full coverage — check extent before designing against them. |

### Spatial Knowledge Graph (`spatial_knowledge_graph`)

Precomputed graph over Overture GERS entities — `nodes_sf`/`edges_sf` and
`nodes_manhattan`/`edges_manhattan`, identical schemas per region. **SF and Manhattan only**; there
is no global equivalent, so do not design a pipeline around it for other geographies.

- `nodes_*`: GERS `id`, `type` (`connector`/`place`/`building`/`address`/`division`/`base`/`snap_point`),
  `geometry`, plus precomputed graph metrics — `pagerank`, `component_id`, `community_id`, `degree`.
- `edges_*`: `src`/`dst` (node GERS IDs), `type` (`road`/`access`/`contains`/`adjacent`/`located_in`/
  `has_address`/`same_as`), `weight` (**meters** for road/access, `0` for containment edges),
  `bidirectional`.

Reach for this instead of walking `transportation_segment.connectors` when you need routing or
centrality in those two cities — the topology is already built.

---

## 5. The two Overture place taxonomies

`places_place` carries both a legacy flat category and a newer hierarchical taxonomy, and **they
return different values for the same POI**:

| `categories.primary` (legacy flat) | `taxonomy.primary` (hierarchical) |
|---|---|
| `dentist` | `dental_clinic` |
| `professional_services` | `professional_service` |
| `community_services_non_profits` | `social_or_community_service` |
| `landmark_and_historical_building` | `historic_site` |
| `corporate_office` | `corporate_or_business_office` |

`taxonomy` is `struct<primary:string, hierarchy:list<string>, alternates:list<string>>`; `hierarchy`
runs root→leaf and its last element equals `taxonomy.primary`:

```text
software_development  → services_and_business → technical_service → software_development
jewelry_store         → shopping → fashion_and_apparel_store → jewelry_store
restaurant            → food_and_drink → restaurant
```

**Consequences:**

- Pick one system per query and say which. Prefer `taxonomy` for new work; `categories` remains for
  back-compat.
- Filter a whole branch with `array_contains(taxonomy.hierarchy, 'food_and_drink')` rather than
  enumerating leaf values — leaf names are exactly what changes between releases.
- Both can be NULL on a real POI. Handle it; do not assume a category exists.

---

## 6. Snapshots & time travel

Havasu keeps each Overture release as an Iceberg **TAG**, so every table is queryable at any past
release. This is the mechanism to know; the tag list itself is live data — read it, don't trust a
snapshot of it in this file.

```sql
-- what releases exist right now
SELECT name, type, snapshot_id
FROM wherobots_open_data.overture_maps_foundation.places_place.refs
WHERE type = 'TAG' ORDER BY name DESC;

-- query one of them (default is latest / main)
SELECT * FROM wherobots_open_data.overture_maps_foundation.places_place
VERSION AS OF '2026-05-20.0' LIMIT 10;
```

- Overture tags are `YYYY-MM-DD.N`, released monthly. Occasionally a same-day `.1` respin exists
  alongside `.0` — order by name descending rather than assuming `.0`.
- Foursquare uses its own `dt=YYYY-MM-DD` form (`VERSION AS OF 'dt=2025-02-06'`); check
  `foursquare.places.refs`.
- **Pin the tag for anything reproducible.** A pipeline reading `main` silently changes inputs when
  the monthly release lands, which shows up as an unexplained metric shift, not an error.

---

## 7. Quirks & gotchas

1. **Two Overture place taxonomies, and they disagree** — see §5. Pick one per query; don't mix.
2. **Foursquare geometry column is `geom`, not `geometry`.** Overture uses `geometry`. Easy to typo
   across a join between the two.
3. **A meaningful fraction of places have a `NULL` category** in both systems. Always handle NULL.
4. **Transportation attributes are linearly referenced**, not per-row scalars. `speed_limits`,
   `road_flags`, `access_restrictions` etc. are lists of structs with a `between:[start,end]`
   fraction along the segment. `EXPLODE()` them — and `EXPLODE` cannot be nested inside another
   expression (Spark).
5. **Routing topology lives in `connectors`**: `segment.connectors[].connector_id` →
   `transportation_connector.id`, with `at` giving position along the segment. For SF/Manhattan, the
   prebuilt `spatial_knowledge_graph` saves the work.
6. **`geocodes.id` is `long`; GERS IDs elsewhere are `string`.** Don't cross those key spaces.
7. **CRS is EPSG:4326 (degrees).** `ST_DWithin(a, b, 0.001)` ≈ 100m at the equator and shrinks with
   latitude — transform or use spheroidal variants for true metric distance.
8. **Copernicus DEM is a `raster` column** (`rast`) — `RS_*` functions, not vector `ST_*`.
9. **NOAA columns are UPPERCASE** (`ISSUED`, `PHENOM`, …); match case exactly.
10. **`divisions_division_area` has multiple parts per division** and includes territorial water —
    filter `is_land` and/or dissolve before area math (§3). The single most common source of a
    plausible-but-wrong number in this catalog.
11. **Filter cheaply on the `bbox` struct doubles** (`bbox.xmin` etc.) before any spatial predicate.
    Note the two forms: `xmin > west AND xmax < east` keeps features *inside* the window, while
    `xmin < east AND xmax > west` keeps features that merely *overlap* it (what you want when the
    feature is larger than the window, e.g. a county).
