# Task 07: Admin Boundary Selection

## Objective

"How many Overture places are inside San Francisco?" Return a single count, using Overture divisions
rather than a hard-coded bounding box or WKT polygon.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- `wherobots_open_data.overture_maps_foundation.divisions_division_area` and `places_place` exist

## Success Criteria

- [ ] Agent uses `divisions_division_area` rather than a hard-coded bbox or WKT for the boundary
- [ ] Agent does not accept a 0-row result from `subtype = 'locality'` -- it establishes that SF is a
      consolidated city-county and filters `subtype = 'county'` (or verifies which subtype covers the
      area before filtering)
- [ ] Result is deduplicated (`COUNT(DISTINCT p.id)` or an equivalent), because the divisions table
      can hold multiple area rows per division
- [ ] Agent states which release/snapshot or notes the count is release-dependent
- [ ] Final count is plausible for SF (order 10^5, not 0 and not the full ~59M table)

## Allowed Tools

- MCP server tools

## Skill Under Test

`wherobots-open-data-catalog`

## Failure Mode Being Measured

The B condition commonly filters `subtype = 'locality'`, gets 0 rows with no error, and reports 0 --
or joins without `DISTINCT` and double-counts.
