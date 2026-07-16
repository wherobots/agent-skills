---
name: open-data-catalog
description: Use when working with the Wherobots Open Data Catalog — which datasets exist and their schemas, join keys, CRS, and quirks. Covers wherobots_open_data (Overture, Foursquare, rasters), release snapshots, and category taxonomies so you pick the right table and columns without guessing.
---

# Wherobots Open Data Catalog

Meta-knowledge for `wherobots_open_data` (Overture Maps, Foursquare, Copernicus DEM, NOAA, and more).
The catalog is **read-only**; write your outputs to `org_catalog`.

**Read [`references/catalog-map.md`](references/catalog-map.md)** for the full walk: every database and
table, Overture layer schemas, the current release snapshot, and a category-taxonomy sample. Verify
against the live catalog with the MCP tools when precision matters — schemas change per Overture release.

## Pick the right table

- **Named boundaries** (country/region/county/locality) → `overture_maps_foundation.divisions_division_area`
  (polygons), joined to `divisions_division` (attributes like `population`) on `division_id = id`.
- **POIs** → Overture `places_place` (rich, nested) or Foursquare `places` (flat). Different schemas.
- **Roads / topology** → `transportation_segment` (LineString) + `transportation_connector` (Point).
- **Buildings** → `buildings_building` (Polygon). **Elevation raster** → `copernicus_dem.glo_30m` (`raster`).

## Quirks that waste turns

- **Geometry column name differs by source:** Overture uses `geometry`; Foursquare `places` uses `geom`.
- **CRS is EPSG:4326 (degrees)** for all vector data. Copernicus is a `raster` column (use `RS_*`).
- **`divisions_division_area` has multiple polygon parts per entity** (land/territorial water/islands).
  Filter `is_land = true` and/or dissolve (`ST_Union_Aggr`) before area math, or you double-count.
- **Two Overture place taxonomies coexist and disagree:** legacy flat `categories.primary` vs new
  hierarchical `taxonomy.primary` + `taxonomy.hierarchy`. Pick one per query; prefer `taxonomy`.
- **Releases are Iceberg snapshots:** query `<table>.refs` for tags; `VERSION AS OF '<tag>'` for time travel.

## Related skills

- `spatial-sql-patterns` — validated query templates against these tables.
- `wherobots-explore` — the MCP tool sequence for discovering schemas live.
