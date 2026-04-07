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

## Scheduled / Recurring Jobs

Wherobots has no native Airflow provider. For recurring scheduled jobs, use Apache Airflow with the Python SDK inside `PythonOperator` tasks:

```python
from wherobots.db import connect, Runtime
conn = connect(api_key=Variable.get("WHEROBOTS_API_KEY"), runtime=Runtime.SMALL)
```
