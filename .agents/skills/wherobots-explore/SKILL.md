---
name: wherobots-explore
description: MCP workflow patterns for Wherobots data discovery, schema exploration, and spatial query generation.
---

# Wherobots Explore

Use this skill when exploring Wherobots catalogs, discovering table schemas, or generating spatial queries through the MCP server.

## MCP Workflow Sequence

The MCP server enforces a specific ordering. Follow this sequence:

1. **Search documentation** (`search_documentation_tool`) -- understand available spatial functions and data formats
2. **Browse catalog** (`list_catalogs_tool` -> `list_databases_tool` -> `list_tables_tool` -> `describe_table_tool`) -- discover schemas
3. **Generate query** (`generate_spatial_query_tool`) -- produces a validated spatial SQL query from natural language
4. **Execute query** (`execute_query_tool`) -- runs the query and returns results

Skipping steps (e.g., executing before exploring the schema) will produce errors or poor results.

## Constraints

- **Read-only**: MCP enforces SELECT-only queries. Do not attempt DDL (CREATE/DROP) or DML (INSERT/UPDATE/DELETE).
- **Pagination**: `execute_query_tool` supports `limit` and `offset` parameters. Use them for large result sets to avoid timeouts.
- **Scope limits**: MCP cannot submit jobs, manage workspaces, or modify infrastructure. Use the CLI or SDK for those tasks.

## Hierarchy Shortcut

Use `list_hierarchy_tool` to get the full catalog -> database -> table tree in one call instead of making multiple sequential calls. Useful when you need an overview before drilling into a specific table.
