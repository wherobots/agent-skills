# Task 04: Spatial Query

## Objective

Using natural language, ask "What are the 10 largest buildings by area in the overture dataset?" and return the query results.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- The `wherobots_open_data.overture.building` table exists

## Success Criteria

- [ ] Agent searches documentation first to understand available spatial functions
- [ ] Agent explores the table schema before generating the query
- [ ] Agent uses `generate_spatial_query_tool` to produce the SQL (not hand-writing it)
- [ ] Agent executes the query via `submit_query_tool`, polling `get_query_status_tool` and fetching with `get_query_results_tool` if it does not return inline
- [ ] Results contain 10 rows with building identifiers and area values

## Allowed Tools

- MCP server tools

## Skill Under Test

`wherobots-explore`
