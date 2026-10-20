# Task 10: Tiled Slope Without Seams

## Objective

"Compute slope in degrees from the Copernicus 30 m DEM for the 2 x 2 degree block with its
south-west corner at 37 N, 123 W, and give me the mean slope per tile plus the maximum slope."
Produce the PySpark / SQL pipeline.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- `wherobots_open_data.copernicus_dem.glo_30m` exists (1-degree COGs, EPSG:4326)

## Success Criteria

- [ ] Agent states that WherobotsDB has no built-in slope function and uses a Python raster UDF
      (`sedona_vectorized_udf`) with a numpy Horn kernel
- [ ] Each tile is read with a halo of at least 1 cell from the **source COG**, and the halo is
      trimmed before the result is returned
- [ ] Agent handles the 1-degree **file** boundaries: neighbouring files supply the halo cells
      (or the agent explicitly flags that per-file halos leave seams on every 1-degree line)
- [ ] The ring outside the data is filled with NaN, not with the file nodata value or 0
- [ ] Cell sizes are converted from degrees to metres from the latitude (per row or per tile),
      with dx and dy treated separately
- [ ] Agent notes Copernicus GLO-30 is a **surface** model (canopy, buildings), not bare earth
- [ ] Output raster has nodata set (`RS_SetBandNoDataValue`) before `RS_SummaryStats`

## Allowed Tools

- MCP server tools

## Skill Under Test

`wherobots-raster`

## Failure Mode Being Measured

The B condition runs a 3x3 kernel per tile with no halo, or with a per-file halo whose boundless
read fills with the file's nodata value. Copernicus has no nodata tag, so the fill is 0 m: measured
on 2026-10-05 this produced fake cliffs of up to 82.6 degrees on every 1-degree file seam, and
no-halo tiles put seams of up to 19 degrees on every tile edge with mean tile slope biased by
0.68 degrees. The query succeeds and the per-tile means look plausible. B also typically uses
the degree cell size directly, which makes every slope near 90 degrees.
