---
name: wherobots-develop
description: Meta-knowledge for developing with the Wherobots CLI, Python SDK, and TypeScript SDK — discovery patterns, non-obvious flags, and multi-step workflows.
---

# Wherobots Develop

Use this skill when writing code, submitting jobs, or building integrations with the Wherobots platform.

## CLI Discovery Pattern

The `wherobots` CLI generates `wherobots api` subcommands from a live OpenAPI spec that changes without notice. Never hardcode or memorize `api` subcommand names.

- **Always discover at runtime**: `wherobots --help` and `wherobots <command> --help`
- **Spec cache**: CLI caches the OpenAPI spec at `~/.cache/wherobots/spec.json` (15-min TTL). If commands seem missing after a server update, delete this file.

## Non-Obvious Global Flags

These flags work on all commands but are not shown in the `--help` summary:

- `--output json` -- machine-parseable JSON output (default is human-readable table)
- `--dry-run` -- prints the equivalent `curl` command without executing

## Job Submission Workflow

This multi-step process has non-obvious behavior:

1. **Auto-upload**: Passing a local `.py` file to the CLI auto-uploads it to S3 via a presigned URL (500 MB limit). No manual upload step is needed.
2. **Watch mode**: Use `--watch` on `create` to stream logs inline instead of running `logs` separately afterward.
3. **Re-attach to logs**: `logs --follow` re-attaches to a running job's log stream after disconnecting.
4. **Auth**: `WHEROBOTS_API_KEY` env var is required. `WHEROBOTS_API_URL` overrides the default endpoint.

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
