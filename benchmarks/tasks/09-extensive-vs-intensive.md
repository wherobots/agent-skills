# Task 09: Extensive vs Intensive Interpolation

## Objective

"I have county population and county median household income. Give me estimated population and
estimated median household income for my three custom service-area polygons." Produce the SQL.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- `wherobots_open_data.overture_maps_foundation.divisions_division` / `_area` exist
- Target polygons are supplied inline as WKT

## Success Criteria

- [ ] Agent classifies population as **extensive** and median income as **intensive**, and says so
- [ ] Population uses the apportionment formula: `SUM(value * area(intersection) / area(source))`
- [ ] Median income uses the area-weighted mean:
      `SUM(value * area(intersection)) / SUM(area(intersection))` -- **not** divided by source area
- [ ] Source is dissolved to one row per county (`ST_Union_Aggr ... GROUP BY division_id`) before the
      area denominator is computed
- [ ] Territorial water is excluded (`is_land = true`) or the inflated-denominator effect is
      explicitly addressed
- [ ] `ST_MakeValid` applied before `ST_Intersection`
- [ ] Agent notes the CRS caveat: the area *ratio* approximately cancels units, but absolute areas in
      EPSG:4326 are degrees^2

## Allowed Tools

- MCP server tools

## Skill Under Test

`wherobots-area-weighted-interpolation`

## Failure Mode Being Measured

The B condition applies the population formula to median income -- summing a rate -- which returns a
plausible-looking number that is wrong, with no error to signal it. It also typically skips the
dissolve, so multipart counties are double-counted.
