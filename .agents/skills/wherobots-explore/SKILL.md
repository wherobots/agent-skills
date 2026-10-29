---
name: wherobots-explore
description: Use when exploring Wherobots catalogs, discovering table schemas, or generating spatial queries — covers the MCP tool workflow sequence and constraints.
---

# Wherobots Explore

## MCP Workflow Sequence

The server does not enforce an order, but this sequence avoids wasted queries:

1. **Search documentation** (`search_documentation`) -- understand available spatial functions and data formats
2. **Browse catalog** (`list_catalogs` -> `list_databases` -> `list_tables` -> `describe_table`) -- discover schemas
3. **Write the SQL** against the columns `describe_table` returned. There is no query-generation tool.
4. **Run the query** (`submit_read_query`) -- returns rows if it finishes within about 20 seconds,
   otherwise a `query_id`. Poll `get_query_results` with that ID instead of submitting again.

Skipping steps (e.g., querying before exploring the schema) will produce errors or poor results.

## Exploration Discipline

- **Explore before writing SQL.** `describe_table` the target before writing SQL against it.
  Never query assumed columns -- a query over an invented column name costs a full round trip.
- **Validate cheap before expensive.** `LIMIT` samples and `COUNT(*)` sanity checks before a
  full-table spatial join; prefilter to an AOI first. Execution bills compute even read-only.
- **Two failed attempts = stop.** Re-read the schema and reconsider the table choice rather than
  looping on a failing query. A clean query returning 0 rows is a failure to investigate, not
  an answer -- usually the wrong table, filter value, or geometry column.
- **Anything destined for production goes to a repo file**, not left in the chat transcript.

## Constraints

- **Read-only by default**: `submit_read_query` refuses writes such as CREATE TABLE, DROP, INSERT, UPDATE, and DELETE (`CREATE TEMPORARY VIEW` is allowed). `submit_write_query` runs writes but is listed only when enabled for you; if it is not listed, use the CLI or SDK.
- **Result size**: a response holds about 16 KB of rows, and a larger result comes back `truncated`, or as a short-lived download URL when it is too large to read inline. Aggregate, filter, or select fewer columns rather than paging; `get_query_results` takes `offset` and `limit` when you do need to page.
- **Scope limits**: the catalog, query, and documentation tools cannot submit job runs, manage workspaces, or modify infrastructure. Use the CLI or SDK for those tasks. Tools for Tasks, schedules, and My Files are listed only when enabled for you.
