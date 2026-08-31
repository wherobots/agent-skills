---
name: wherobots-develop
description: Use when writing Wherobots job code, submitting or monitoring jobs, or integrating with the Python/TypeScript SDK — covers CLI discovery patterns, non-obvious flags, job workflows, storage options, and data connectivity.
---

# Wherobots Develop

## Data Discovery Before Writing Job Logic

Before implementing spatial job logic, use the MCP server to understand what data is actually available:

- Use `search_documentation_tool` (MCP) to look up Wherobots features, Sedona spatial
  functions, storage integration setup, or catalog configuration before implementing.
  If the MCP server is unavailable, browse [docs.wherobots.com](https://docs.wherobots.com/latest/) directly.
- Browse `wherobots_open_data` with `list_databases_tool` / `list_tables_tool` to find pre-loaded datasets (Overture, FEMA, OpenStreetMap, etc.)
- Use `describe_table_tool` to confirm geometry column names, CRS, and relevant filter fields
- Test the spatial logic with `execute_query_tool` using `LIMIT 10` before committing to a job file

**Job files**: Store job scripts in **Wherobots Managed Storage** (built-in S3, us-west-2) or in your own S3 bucket via a **Storage Integration**. Both are referenceable as `s3://` URIs in `WherobotsRunOperator` and `wherobots job-runs create`.

**Proprietary data**: Five ways to bring in your own data:

- **S3 Storage Integration** — connect your own S3 bucket; Wherobots can register it as a managed catalog so tables appear in Sedona SQL alongside open data
- **AWS Glue Data Catalog** — read and write Iceberg tables in a Glue catalog. Set up through the Data Hub wizard, which provisions a **Cloud Connection** (an IAM role with an external ID that Wherobots assumes) via CloudFormation — no manual IAM policy or Spark config. Choose **read-only** or **read-write** at connection time. Requires an Admin account in a Professional, Innovation, or Enterprise Organization. Known limits: no `CREATE TABLE AS SELECT`, no views, no table renaming
- **Amazon S3 Tables Catalog** — connect an S3 Tables bucket, also via a Cloud Connection
- **Databricks Unity Catalog** — read Delta Lake and Apache Iceberg tables directly from a Databricks workspace (Delta uses a Personal Access Token, Iceberg uses OAuth client credentials; Professional/Enterprise only; writing back to Delta is not currently supported). Databricks foreign catalogs do not use a Wherobots Cloud Connection
- **Havasu tables** — Wherobots' own Iceberg-based spatial table format; store in Managed Storage or your S3 integration and query like any catalog table

Use `wherobots api` CLI commands to discover and configure storage integrations and catalog connections.

## CLI-First for API Operations

When implementing a workflow that involves Wherobots API operations (creating job runs, checking status, listing resources, managing workspaces), **plan to use the `wherobots` CLI first**. The CLI covers the full job lifecycle and is the right tool for operational tasks; reach for the Python or TypeScript SDK only when you need to integrate results into application code.

## CLI Discovery Pattern

The `wherobots` CLI generates `wherobots api` subcommands from a live OpenAPI spec that changes without notice. Never hardcode or memorize `api` subcommand names.

- **Always discover at runtime**: `wherobots --help` and `wherobots <command> --help`
- **Spec cache**: CLI caches the OpenAPI spec at `~/.cache/wherobots/spec.json` (15-min TTL). If commands seem missing after a server update, delete this file.

## Non-Obvious Global Flags

These flags work on all commands but are not shown in the `--help` summary:

- `--output json` -- machine-parseable JSON output (default is human-readable table)
- `--dry-run` -- prints the equivalent `curl` command without executing
- `--tree` -- prints the whole command tree (`wherobots --tree`, `wherobots api --tree`). Faster than
  guessing leaf names in the large, spec-generated `api` surface.
- `-y, --yes` -- skip the confirmation prompt in CI/scripts

**`--dry-run` is not an offline mode.** `WHEROBOTS_API_KEY` must still be exported: the CLI
authenticates before rendering the curl, and `job-runs create` on a *local* script calls the API to
resolve the managed-storage upload path before it can render anything. Point it at an `s3://` script
or pass `--no-upload` if you want a dry run without that call.

## Job Submission Workflow

This multi-step process has non-obvious behavior:

1. **Auto-upload**: Passing a local `.py` file to the CLI auto-uploads it to S3 via a presigned URL (500 MB limit). No manual upload step is needed.
2. **Watch mode**: Use `--watch` on `create` to stream logs inline instead of running `logs` separately afterward.
3. **Re-attach to logs**: `logs --follow` re-attaches to a running job's log stream after disconnecting.
4. **Auth**: `WHEROBOTS_API_KEY` env var is required. `WHEROBOTS_API_URL` overrides the default endpoint.
5. **Prefer `job-runs` over `api runs`**: `job-runs create` takes the script as a positional arg and
   handles local-file auto-upload, `--watch`, and `--dep-pypi`/`--dep-file`. `api runs create-job-run`
   is the raw POST — `s3://` scripts only, via `--runpython`. Use it when you need an API field the
   wrapper does not expose.
6. **Raw `api` bodies are camelCase, the flags are not**: `--runpython` sets `runPython`,
   `--timeoutseconds` sets `timeoutSeconds`. Matters when you hand-write a body via `--json`, which
   overrides the individual field flags. Object/array values must be JSON strings.
7. **Size the runtime deliberately**: the `job-runs create` default is `tiny`, which is too small for
   a real spatial join. Defaults for region (`aws-us-west-2`) and `--timeout` (3600s) are likewise
   sized for smoke tests, not production runs.

## Cost Hygiene

Job runs and interactive SQL sessions both bill compute, so treat every execution as spend:

- Prototype on a bounded sample (`LIMIT`, an AOI bbox prefilter) before running a stage full-table.
- Size `--runtime` to the workload rather than scaling up after a failure.
- **Two failed attempts = stop.** Re-read the schema and reconsider the table choice instead of
  re-submitting a failing job; each retry costs a runtime start.
- Review spend with the `usage` group of the `api` surface (`wherobots api usage --tree` for the
  current leaf names).

## Python SDK (wherobots-python-dbapi)

```python
from wherobots.db import connect, Runtime

# connect() blocks until the runtime is ready (async session startup)
conn = connect(api_key="...", runtime=Runtime.SMALL)
cursor = conn.cursor()
cursor.execute("SELECT ST_Area(geometry) FROM wherobots_open_data.overture.building LIMIT 5")
results = cursor.fetchall()
```

Import is `wherobots.db`, not `wherobots_python_dbapi`. The `connect()` call blocks until the remote Spark session is fully initialized -- this can take 30-120 seconds.

## TypeScript SDK (wherobots-sql-driver)

```typescript
import { Connection } from "wherobots-sql-driver";

const conn = await Connection.connect({ apiKey: "...", runtime: "SMALL" });
const results = await conn.execute("SELECT ...");
// results are Apache Arrow tables
```

The npm package name is `wherobots-sql-driver`, not `wherobots-typescript-sdk`. Results are returned as Apache Arrow tables.

## VS Code Extension

The Wherobots VS Code extension provides an MCP server for Copilot Chat, a Jupyter kernel picker for remote workspaces, and a Data Hub browser. It registers the MCP server automatically -- no manual configuration is needed if the extension is installed and an API key is set.
