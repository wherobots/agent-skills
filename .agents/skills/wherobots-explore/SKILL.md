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
4. **Execute query** (`submit_query_tool` -> `get_query_status_tool` -> `get_query_results_tool`) -- runs the query as a background job and returns results

Skipping steps (e.g., executing before exploring the schema) will produce errors or poor results.

## Running Queries: Submit -> Poll -> Fetch

Queries run as **background jobs** so that no tool call outlives the client's tool-call timeout --
a cold SQL session alone can take several minutes to provision.

1. `submit_query_tool(query, limit=...)` starts the job and waits about 20 seconds. Short queries
   return their results directly in this call.
2. If the response status is `starting_session` or `running`, the query is still going. Poll
   `get_query_status_tool(query_id)` until it succeeds. **Never resubmit a query that is already
   running** -- resubmitting starts a second job and doubles the compute.
3. Fetch the rows with `get_query_results_tool(query_id)` once the status is succeeded.
4. `cancel_query_tool(query_id)` cancels a job you no longer need.

Expect a cold start to take minutes, not seconds. Treat a `running` status as normal progress
rather than a failure to retry.

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
- **Pagination**: `submit_query_tool` accepts a `limit`. For large result sets, page with `LIMIT` / `OFFSET` in the SQL itself and fetch each page with `get_query_results_tool`.
- **Scope limits**: MCP cannot submit jobs, manage workspaces, or modify infrastructure. Use the CLI or SDK for those tasks.

## Catalog Traversal

There is no single-call hierarchy tool. Walk the tree with `list_catalogs_tool` ->
`list_databases_tool` -> `list_tables_tool`, then `describe_table_tool` on the tables you intend to
query. On a large organization these listings can return thousands of entries; when that is likely,
call them from a subagent so the full list stays out of the main context window.
