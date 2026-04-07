# Task 01: List Catalogs

## Objective

List all available data catalogs in the Wherobots platform and display their names.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The Wherobots MCP server is available
- At least one managed catalog exists in the account

## Success Criteria

- [ ] Agent correctly identifies MCP as the appropriate tool (not CLI or SDK)
- [ ] Agent calls `list_catalogs_tool` via the MCP server
- [ ] Output includes the names of all available catalogs
- [ ] Agent does not attempt to use CLI or SDK for this discovery task

## Allowed Tools

- MCP server tools
- Bash (for environment inspection only)

## Skill Under Test

`wherobots-usage` (for interface selection) + `wherobots-explore` (for MCP workflow)
