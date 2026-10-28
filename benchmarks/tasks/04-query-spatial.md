# Task 04: Spatial Query

## Objective

Using natural language, ask "What are the 10 largest buildings by area in the overture dataset?" and return the query results.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- The `wherobots_open_data.overture_maps_foundation.buildings_building` table exists

## Success Criteria

- [ ] Agent searches documentation first to understand available spatial functions
- [ ] Agent writes the SQL after calling `describe_table` on the table
- [ ] Agent runs the query via `submit_read_query`, polling `get_query_results` if it returns a `query_id`
- [ ] Results contain 10 rows with building identifiers and area values

## Allowed Tools

- MCP server tools

## Skill Under Test

`wherobots-explore`
