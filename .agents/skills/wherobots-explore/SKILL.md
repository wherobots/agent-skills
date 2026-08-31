---
name: wherobots-explore
description: Use when exploring Wherobots catalogs, discovering table schemas, or generating spatial queries — covers the MCP tool workflow sequence, constraints, and shortcuts.
---

# Wherobots Explore

## MCP Workflow Sequence

The MCP server enforces a specific ordering. Follow this sequence:

1. **Search documentation** (`search_documentation_tool`) -- understand available spatial functions and data formats
2. **Browse catalog** (`list_catalogs_tool` -> `list_databases_tool` -> `list_tables_tool` -> `describe_table_tool`) -- discover schemas
3. **Generate query** (`generate_spatial_query_tool`) -- produces a validated spatial SQL query from natural language
4. **Execute query** (`execute_query_tool`) -- runs the query and returns results

Skipping steps (e.g., executing before exploring the schema) will produce errors or poor results.

## Exploration Discipline

- **Explore before generating.** `describe_table_tool` the target before writing SQL against it.
  Never query assumed columns -- a query over an invented column name costs a full round trip.
- **Validate cheap before expensive.** `LIMIT` samples and `COUNT(*)` sanity checks before a
  full-table spatial join; prefilter to an AOI first. Execution bills compute even read-only.
- **Two failed attempts = stop.** Re-read the schema and reconsider the table choice rather than
  looping on a failing query. A clean query returning 0 rows is a failure to investigate, not
  an answer -- usually the wrong table, filter value, or geometry column.
- **Anything destined for production goes to a repo file**, not left in the chat transcript.

## Constraints

- **Read-only**: MCP enforces SELECT-only queries. Do not attempt DDL (CREATE/DROP) or DML (INSERT/UPDATE/DELETE).
- **Pagination**: `execute_query_tool` supports `limit` and `offset` parameters. Use them for large result sets to avoid timeouts.
- **Scope limits**: MCP cannot submit jobs, manage workspaces, or modify infrastructure. Use the CLI or SDK for those tasks.

## Hierarchy Shortcut

Use `list_hierarchy_tool` to get the full catalog -> database -> table tree in one call instead of making multiple sequential calls. Useful when you need an overview before drilling into a specific table.
