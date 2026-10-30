# Task 02: Describe Table

## Objective

Find and display the full schema (column names, types, and any spatial column details) for the table `wherobots_open_data.overture_maps_foundation.buildings_building`.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- The `wherobots_open_data` catalog exists with the `overture_maps_foundation` database and `buildings_building` table

## Success Criteria

- [ ] Agent navigates the catalog hierarchy correctly (catalog -> database -> table)
- [ ] Agent calls `describe_table` with the correct catalog, database, and table names
- [ ] Output includes all column names and their data types
- [ ] Spatial/geometry columns are identified in the output

## Allowed Tools

- MCP server tools
- Bash (for environment inspection only)

## Skill Under Test

`wherobots-explore`
