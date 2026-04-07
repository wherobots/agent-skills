# Task 02: Describe Table

## Objective

Find and display the full schema (column names, types, and any spatial column details) for the table `wherobots_open_data.overture.building`.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- The `wherobots_open_data` catalog exists with the `overture` database and `building` table

## Success Criteria

- [ ] Agent navigates the catalog hierarchy correctly (catalog -> database -> table)
- [ ] Agent calls `describe_table_tool` with the correct fully-qualified table name
- [ ] Output includes all column names and their data types
- [ ] Spatial/geometry columns are identified in the output

## Allowed Tools

- MCP server tools
- Bash (for environment inspection only)

## Skill Under Test

`wherobots-explore`
