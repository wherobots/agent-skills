---
name: wherobots-usage
description: Decision matrix for Wherobots interfaces — MCP, CLI, SDK, and Dashboard — plus auth setup and scheduling guidance.
---

# Wherobots Usage

Use this skill whenever working with the Wherobots spatial analytics platform to choose the right interface and configure authentication.

## Interface Decision Matrix

| Task type | Use | Why |
|---|---|---|
| Data exploration, schema discovery, spatial Q&A in an AI chat session | **MCP server** | Interactive, read-only queries with catalog browsing |
| Operational tasks: submit jobs, stream logs, check status, scripted automation | **`wherobots` CLI** | Full lifecycle control, scriptable, supports `--output json` |
| Programmatic access in notebooks, application code, or pipelines | **Python SDK** (`wherobots-python-dbapi`) or **TypeScript SDK** (`wherobots-sql-driver`) | DB-API 2.0 / Arrow-based interfaces for integration |
| Visual exploration, billing, workspace management | **Wherobots Dashboard** (cloud.wherobots.com) | GUI-only features like team management and billing |

## Authentication

All interfaces use the same API key:

- **Environment variable**: `WHEROBOTS_API_KEY` -- used by CLI, SDKs, and MCP
- **VS Code extension**: stores the key in VS Code SecretStorage (set via Command Palette)
- **Endpoint override**: `WHEROBOTS_API_URL` env var overrides the default `https://api.cloud.wherobots.com`

**For production/automated jobs, always use a service principal API key**, not a personal user key. Service principals (Professional/Enterprise, created by org admins) are not tied to any individual — jobs using them remain functional when team members leave. Personal API keys are for development only.

## Scheduled / Recurring Jobs

Use the official Airflow provider (`pip install airflow-providers-wherobots`). The correct pattern is:

1. Write the job logic as a standalone Python `.py` file (stored in version control)
2. Upload it to S3 (Wherobots managed storage or your own bucket)
3. Use `WherobotsRunOperator` to submit it as a Wherobots job run on a schedule

```python
from wherobots.cloud.airflow.operators.run import WherobotsRunOperator

WherobotsRunOperator(
    task_id="nc_flood_risk",
    app_location="s3://my-bucket/jobs/flood_risk_nc.py",
    runtime="SMALL",
    dag=dag,
)
```

**Do not** use `wherobots-python-dbapi` inside an Airflow `PythonOperator` for this — that opens an interactive SQL session (billed compute) rather than submitting a managed job run. Use `WherobotsSqlOperator` from the same provider only for lightweight SQL-only tasks.
