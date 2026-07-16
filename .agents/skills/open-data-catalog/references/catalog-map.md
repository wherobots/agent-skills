# Wherobots Open Data Catalog — Live Map

**Catalog:** `wherobots_open_data`
**Mapped:** 2026-07-09 (via Wherobots MCP against live compute)
**Overture release (current default / `main`):** **`2026-06-17.0`**
**Foursquare release:** versioned by `dt=YYYY-MM-DD` snapshot tags (see [Snapshots](#snapshots--time-travel))

> How this was built: walked every database and table with `list_databases` / `list_tables`,
> pulled schemas with `describe_table`, and ran bounded, `LIMIT`ed `GROUP BY` samples
> against a ~2km² downtown-SF window. Schemas evolve with each Overture release —
> re-verify against the live catalog when precision matters, and diff this file when you do.

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

All vector tables are Iceberg/Havasu with native `geometry` columns. Assume **EPSG:4326
(degrees)** unless a column proves otherwise — use `ST_DWithin` with degree thresholds, or
transform before metric distance work. Copernicus is a `raster` column, not vector.

---

## 2. Overture Maps (`overture_maps_foundation`)

Overture's schema is deeply nested (structs, lists, maps). Below, top-level columns are listed
with their type; important nested fields use dot notation. `bbox` (`struct<xmin,xmax,ymin,ymax:double>`),
`sources` (`list<struct<property,dataset,license,record_id,update_time,confidence,between>>`),
`version` (`int`), and `names` (`struct<primary:string, common:map<string,string>, rules:list<...>>`)
recur on almost every layer — described once here, abbreviated below as **[common]**.

**Every table has:** `id` (GERS string ID), `geometry`, `bbox`, `sources`, `version`.
Filtering on the scalar `bbox.xmin/xmax/ymin/ymax` doubles are a cheap way to window a region
without a spatial predicate (used for all sampling below).

### 2.1 `places_place` — POIs (Point), ~59M rows
The workhorse POI table. **Two category systems coexist (see [§7 quirks](#7-quirks--gotchas)).**

| Column | Type | Notes |
|---|---|---|
| `id`, `geometry`, `bbox`, `sources`, `version`, `names` | [common] | Point geometry |
| `categories` | `struct<primary:string, alternate:list<string>>` | **Legacy** flat category |
| `taxonomy` | `struct<primary:string, hierarchy:list<string>, alternates:list<string>>` | **New** hierarchical taxonomy |
| `basic_category` | `string` | Coarse bucket |
| `confidence` | `double` | 0–1 |
| `websites`,`socials`,`emails`,`phones` | `list<string>` | Contact arrays |
| `brand` | `struct<wikidata:string, names:struct<...>>` | Chain/brand |
| `addresses` | `list<struct<freeform,locality,postcode,region,country>>` | |
| `operating_status` | `string` | |

### 2.2 `buildings_building` — footprints (Polygon), ~785M rows
| Column | Type | Notes |
|---|---|---|
| `id`,`geometry`,`bbox`,`sources`,`version`,`names` | [common] | Polygon |
| `subtype`,`class` | `string` | e.g. residential/commercial |
| `height`,`min_height`,`roof_height` | `double` | meters |
| `num_floors`,`num_floors_underground`,`min_floor`,`level` | `int` | |
| `is_underground`,`has_parts` | `boolean` | `has_parts` → join to `buildings_building_part` |
| `facade_color`,`facade_material`,`roof_material`,`roof_shape`,`roof_orientation`,`roof_color` | `string` | 3D styling |
| `roof_direction` | `double` | |

### 2.3 `buildings_building_part` — (Polygon)
Same 3D/roof/facade attributes as `buildings_building`, plus **`building_id` (string)** → foreign
key back to the parent building. Join on `part.building_id = building.id`.

### 2.4 `transportation_segment` — road/rail center-lines (LineString), ~294M rows
The most complex Overture schema. Attributes are **linearly-referenced**: most are lists of
`struct<..., between:list<double>>` where `between` is a `[start,end]` fraction (0–1) along the segment.

| Column | Type | Notes |
|---|---|---|
| `id`,`geometry`,`bbox`,`sources`,`version`,`names` | [common] | LineString |
| `subtype` | `string` | `road`, `rail`, `water` |
| `class` | `string` | `motorway`,`primary`,`residential`,`footway`,… |
| `subclass`, `subclass_rules` | `string` / list | |
| `connectors` | `list<struct<connector_id:string, at:double>>` | **Topology**: `at` = position (0–1); join to `transportation_connector` |
| `road_surface`,`road_flags`,`rail_flags`,`width_rules`,`level_rules` | list<struct<…,between>> | Linearly-referenced |
| `speed_limits` | `list<struct<min_speed,max_speed:struct<value:int,unit:string>, when, between>>` | |
| `access_restrictions`,`prohibited_transitions` | list<struct<…>> | Turn/access rules for routing |
| `routes`,`destinations` | list<struct<…>> | Signed routes / destination signage |

### 2.5 `transportation_connector` — nodes (Point), ~330M rows
Minimal: `id`, `geometry` (Point), `bbox`, `sources`, `version`. Referenced by
`segment.connectors[].connector_id`. This is the graph-topology join key for routing.

### 2.6 Divisions (administrative boundaries)
Use these for **all geographic filtering** — never hard-code WKT/bbox for admin areas.

**`divisions_division`** — division records (Point, localities/countries/regions), ~admin metadata:
`country` (ISO2), `region` (ISO e.g. `US-CA`), `subtype` (`country`/`region`/`county`/`locality`),
`admin_level` (int), `class`, `names` [common], `wikidata`, `population` (int), `hierarchies`
(nested list of parent divisions), `parent_division_id`, `norms.driving_side`, `capital_division_ids`,
`local_type` (map), `perspectives`, `cartography`.

**`divisions_division_area`** — **polygon** areas (this is the one you `ST_Intersects` against):
`geometry` (Polygon), `country`, `region`, `subtype`, `admin_level`, `class`, `names`,
`is_land`,`is_territorial` (boolean), `division_id` (→ `divisions_division.id`).

**`divisions_division_boundary`** — shared borders (LineString): `division_ids` (list<string>),
`subtype`, `admin_level`, `class`, `is_disputed`,`is_land`,`is_territorial` (boolean), `country`,`region`.

> **Canonical boundary join** (from MCP server guidance):
> ```sql
> JOIN wherobots_open_data.overture_maps_foundation.divisions_division_area a
>   ON ST_Intersects(a.geometry, b.geometry)
> WHERE a.country = 'US' AND a.region = 'US-CA' AND a.subtype = 'region'
> ```

### 2.7 `addresses_address` — (Point)
`street`,`number`,`unit`,`postcode`,`postal_city`,`country` (string), `address_levels`
(list<struct<value>>), plus [common] minus `names`.

### 2.8 Base layers
Overture's physical/natural features. All Polygon-ish with [common] + `subtype`/`class`.

| Table | Geometry | Distinctive columns |
|---|---|---|
| `base_land` | Polygon | `subtype`,`class`,`names`,`surface`,`elevation` (int), `level`,`wikidata`,`source_tags` (map) |
| `base_land_use` | Polygon | `subtype`,`class`,`names`,`surface`,`elevation`,`level`,`wikidata`,`source_tags` |
| `base_land_cover` | Polygon | `subtype`, `cartography` (struct: prominence/min_zoom/max_zoom/sort_key) — minimal attrs |
| `base_water` | Polygon | `subtype`,`class`,`names`,`is_salt`,`is_intermittent` (boolean),`level`,`source_tags` |
| `base_infrastructure` | mixed | `subtype`,`class`,`names`,`height` (double),`surface`,`level`,`wikidata`,`source_tags` |
| `base_bathymetry` | Polygon | `depth` (int), `cartography` — depth contours |

### 2.9 Geocoder tables
- **`geocodes`** — `id` (**long**), `location` (string), `layer` (string), `geometry`. Note `id` is `long` here, unlike GERS strings.
- **`geocodes_index`** — inverted index: `token_phrase` (string), `address_ids` (list<long>), `frequency` (long). For text→address lookup; join `address_ids` back to `geocodes.id`.

---

## 3. Foursquare (`foursquare`)

### 3.1 `places` — POIs (Point), ~105M rows
Flat schema (no nested Overture structs), lat/lon columns present alongside `geom`:
`fsq_place_id`, `name`, `latitude`,`longitude` (double), `geom` (**geometry — note: `geom`, not `geometry`**),
`address`,`locality`,`region`,`postcode`,`admin_region`,`post_town`,`po_box`,`country`,
`date_created`,`date_refreshed`,`date_closed` (string),
`tel`,`website`,`email`,`instagram`,`twitter`,`facebook_id` (long),
`fsq_category_ids` (list<string>), `fsq_category_labels` (list<string>),
`placemaker_url`, `unresolved_flags` (list<string>), `bbox`.

### 3.2 `categories` — taxonomy dictionary, 1,245 rows
Flattened 6-level hierarchy: `category_id`,`category_level` (int),`category_name`,`category_label`,
and `level1_..level6_category_id`/`_name`. Join `places.fsq_category_ids[]` → `categories.category_id`.

### 3.3 `deltas` — incremental changes (~7.4M)
`fsq_place_id`, `action` (string), `redirect` (string). Apply on top of a `places` snapshot.

---

## 4. Other databases

| Table | Geometry / key type | Columns of note |
|---|---|---|
| `copernicus_dem.glo_30m` | **`raster`** | `rast` (raster), `x`,`y` (int tile idx), `name`, `footprint` (geometry), `ingested_at` (timestamptz). Use raster functions (`RS_*`), not vector `ST_*`, on `rast`. |
| `noaa.storm_warnings` | Polygon | VTEC warnings: `PRODUCT_ID`,`WFO`,`PHENOM`,`SIG`,`ETN`,`NWS_UGC`, `ISSUED`/`EXPIRED`/`POLYBEGIN`/`POLYEND` (timestamptz), `AREA_KM2`,`WINDTAG`,`HAILTAG` (double), `TORNADOTAG`,`DAMAGETAG`,`IS_EMERGENCY`, `VTEC_YEAR`, `geometry`. All column names **UPPERCASE**. |
| `partner_samples.regrid_parcels` | Polygon | 139-column US parcel schema: `geoid`,`parcelnumb`,`usecode`/`usedesc`,`zoning`,`yearbuilt`,`landval`/`improvval`/`parval` (float), `owner`, `saledate` (date),`saleprice`, `census_*`, `fema_flood_zone*`, `lbcs_*` land-use codes, `gisacre`/`sqft`, `geometry`. |
| `rasterflow_output_samples.fields_of_the_world_vector_global` | Polygon | `geometry`,`time` (timestamptz),`layer`,`bbox`. Ag field boundaries. |
| `rasterflow_output_samples.chesapeakersc_vector` | Polygon | `geometry`,`score_mean`,`score_std` (float),`time`,`layer`,`xmin/ymin/xmax/ymax`. |
| `rasterflow_output_samples.tile2net_vector` | Polygon/Line | Same shape as `chesapeakersc_vector` (sidewalk/crosswalk detection). |

### Spatial Knowledge Graph (`spatial_knowledge_graph`)
Precomputed graph over Overture GERS entities. Tables: `nodes_sf`/`edges_sf` and
`nodes_manhattan`/`edges_manhattan` (identical schemas per region).

- **`nodes_*`**: `id` (GERS UUID), `type` (`connector`/`place`/`building`/`address`/`division`/`base`/`snap_point`),
  `geometry`, `name`, `category`, `properties` (JSON string), `lat`,`lon`, `division_ids` (list),
  `pagerank` (double), `component_id`,`community_id` (long), `degree` (int), `updated_at`.
- **`edges_*`**: `edge_id`, `src`,`dst` (node GERS IDs), `type` (`road`/`access`/`contains`/`adjacent`/`located_in`/`has_address`/`same_as`),
  `road_class`, `weight` (double — meters for road/access, 0 for containment), `geometry` (LineString for road/access),
  `properties` (JSON), `bidirectional` (boolean), `updated_at`.

---

## 5. Category taxonomy sample (Overture `places_place`)

Bounded `GROUP BY` over a ~2km² downtown-SF window
(`bbox.xmin>-122.42 AND bbox.xmax<-122.40 AND bbox.ymin>37.77 AND bbox.ymax<37.79`),
top categories by count. **This is a local sample, not global frequency** — it illustrates the
taxonomy shape and the two coexisting systems, not real-world category ranking.

### Legacy `categories.primary` (flat)
`hotel` (263), `community_services_non_profits` (247), `software_development` (190),
`jewelry_store` (170), `professional_services` (165), `landmark_and_historical_building` (152),
`dentist` (146), `clothing_store` (140), `art_gallery` (135), `corporate_office` (132),
`hair_salon` (120), `restaurant` (104), `parking`, `coffee_shop`, `bar`, `beauty_salon`,
`automotive_repair`, `cafe`, `doctor`, `american_restaurant`, `pizza_restaurant`, …
(~569 rows had `NULL` primary category in this window.)

### New `taxonomy.primary` + `taxonomy.hierarchy` (hierarchical)
| taxonomy.primary | hierarchy path |
|---|---|
| `social_or_community_service` | community_and_government → social_or_community_service |
| `hotel` | lodging → hotel |
| `software_development` | services_and_business → technical_service → software_development |
| `jewelry_store` | shopping → fashion_and_apparel_store → jewelry_store |
| `professional_service` | services_and_business → professional_service |
| `corporate_or_business_office` | services_and_business → corporate_or_business_office |
| `historic_site` | cultural_and_historic → historic_site |
| `dental_clinic` | health_care → outpatient_care_facility → dental_clinic |
| `clothing_store` | shopping → fashion_and_apparel_store → clothing_store |
| `art_gallery` | arts_and_entertainment → arts_and_crafts_space → art_gallery |
| `hair_salon` | lifestyle_services → personal_or_beauty_service → hair_salon |
| `restaurant` | food_and_drink → restaurant |

The `hierarchy` list is root→leaf; the last element equals `taxonomy.primary`. Filter a whole
branch with `array_contains(taxonomy.hierarchy, 'food_and_drink')` rather than matching leaves.

---

## 6. Snapshots & time-travel

Havasu keeps each Overture release as an Iceberg **TAG**. Query available versions:

```sql
SELECT name, type, snapshot_id
FROM wherobots_open_data.overture_maps_foundation.places_place.refs
WHERE type = 'TAG' ORDER BY name DESC;
```

Overture release tags are `YYYY-MM-DD.N` (monthly). As of 2026-07-09, `main` = **`2026-06-17.0`**.
Full tag history observed on `places_place` (newest→oldest):

```
2026-06-17.0  2026-05-20.0  2026-04-15.0  2026-03-18.0  2026-02-18.0  2026-01-21.0
2025-12-17.0  2025-11-19.0  2025-10-22.0  2025-09-24.0  2025-08-20.1  2025-08-20.0
2025-07-23.0  2025-06-25.0  2025-05-21.0  2025-04-23.0  2025-03-19.1  2025-03-19.0
2025-02-19.0  …
```

Query a specific release (default is latest / `main`):

```sql
SELECT * FROM wherobots_open_data.overture_maps_foundation.places_place
VERSION AS OF '2026-05-20.0' LIMIT 10;
```

Foursquare uses its own `dt=YYYY-MM-DD` tags (e.g. `VERSION AS OF 'dt=2025-02-06'`) — check
`foursquare.places.refs` for the current set.

---

## 7. Quirks & gotchas

1. **Two Overture place taxonomies, and they disagree.** `categories.primary` (legacy flat) and
   `taxonomy.primary` (new hierarchical) return *different* values for the same POI:
   `dentist` vs `dental_clinic`, `professional_services` vs `professional_service`,
   `community_services_non_profits` vs `social_or_community_service`,
   `landmark_and_historical_building` vs `historic_site`, `corporate_office` vs `corporate_or_business_office`.
   Pick one system per query and state it; don't mix. Prefer `taxonomy` for new work (branch-filterable
   via `hierarchy`); `categories.primary` still exists for back-compat.
2. **Foursquare geometry column is `geom`, not `geometry`.** Overture uses `geometry`. Easy to typo across a join.
3. **A meaningful fraction of places have `NULL` category** (569 rows in the sample window). Always handle NULL.
4. **Transportation attributes are linearly-referenced**, not per-row scalars. `speed_limits`, `road_flags`,
   `access_restrictions` etc. are lists of structs with a `between:[start,end]` fraction. `EXPLODE()` them,
   and remember `EXPLODE` can't be nested inside another expression (Spark).
5. **Routing topology lives in `connectors`**: `segment.connectors[].connector_id` → `transportation_connector.id`,
   with `at` giving position along the segment. Or use the prebuilt `spatial_knowledge_graph` for SF/Manhattan.
6. **`geocodes.id` is `long`; GERS IDs elsewhere are `string`.** Don't cross-join those key spaces.
7. **CRS is EPSG:4326 (degrees).** `ST_DWithin(a, b, 0.001)` ≈ ~100m at the equator but shrinks with latitude —
   transform or use spheroidal variants for true metric distance.
8. **Copernicus DEM is a `raster` column** (`rast`) — use `RS_*` functions, not vector `ST_*`.
9. **NOAA columns are UPPERCASE** (`ISSUED`,`PHENOM`,…); quote or match case exactly.
10. **Filter cheaply on the `bbox` struct doubles** (`bbox.xmin` etc.) to window a region before any
    spatial predicate — as done for every sample in this doc — to keep exploration bounded and cheap.
