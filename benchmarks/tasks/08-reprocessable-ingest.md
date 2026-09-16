# Task 08: Reprocessable Daily Ingest

## Objective

"Design a pipeline that ingests yesterday's fleet GPS pings from
`s3://fleet-raw/pings/<date>/*.parquet`, matches them to the nearest Overture road, and writes daily
per-vehicle trip lines as GeoParquet." Produce the layer design and the SQL/job skeleton -- do not run
it against production data.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server and `wherobots` CLI are available
- No `org_catalog` tables exist yet

## Success Criteria

- [ ] Design separates Bronze (schema-enforced ingest) from Silver (spatial matching) from Gold
      (trip lines), each as an Iceberg/Havasu table
- [ ] Bronze read declares explicit types -- no `inferSchema`
- [ ] The daily write is idempotent: `INSERT OVERWRITE` of the run's partition or a full
      `CREATE OR REPLACE`, **not** `CREATE TABLE IF NOT EXISTS` + `INSERT INTO`
- [ ] Point-to-road matching does **not** use `ST_KNN`/`ST_DWithin` alone against road centrelines
      without acknowledging centroid reduction -- it uses point-to-line distance
- [ ] Trip line construction orders points before `ST_MakeLine` (`COLLECT_LIST` is unordered) and
      filters to >= 2 points
- [ ] Output is GeoParquet plus an Iceberg table; no shapefile anywhere in the design
- [ ] Runtime is sized above the `tiny` default for the spatial join

## Allowed Tools

- MCP server tools (read-only exploration)
- `wherobots` CLI (`--dry-run` only)
- File writes in the repo

## Skill Under Test

`wherobots-pipeline-designer` (with `wherobots-develop` for submission)

## Failure Mode Being Measured

The B condition typically produces a single monolithic script with `INSERT INTO` (non-idempotent on
retry), `ST_KNN` against road lines (centroid trap), and an unordered `COLLECT_LIST` into
`ST_MakeLine` (scrambled trip geometry).
