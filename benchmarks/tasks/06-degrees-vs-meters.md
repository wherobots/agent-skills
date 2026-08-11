# Task 06: Degrees vs Meters

## Objective

"Find every Overture place within 500 metres of the point (-122.408, 37.784) and report its distance
in metres." Produce and run the query.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- `wherobots_open_data.overture_maps_foundation.places_place` exists

## Success Criteria

- [ ] Agent recognises the catalog is EPSG:4326 and that a bare `ST_DWithin(a, b, 500)` would mean
      500 **degrees**, not metres
- [ ] Distance filter is expressed in metres correctly -- `ST_DWithin(..., 500, true)` (spheroid), or
      an equivalent `ST_DistanceSphere` / `ST_Transform` formulation
- [ ] Reported distances are metres, not degrees (values in the 0-500 range, not ~0.005)
- [ ] Query includes a `bbox.*` scalar prefilter before the spatial predicate
- [ ] Agent does not silently accept a 0-row or whole-globe result

## Allowed Tools

- MCP server tools

## Skill Under Test

`wherobots-spatial-sql-patterns`

## Failure Mode Being Measured

The B condition typically writes PostGIS-style `ST_DWithin(geom, pt, 500)` and returns either the
whole dataset or an implausible result, without noticing the unit error.
