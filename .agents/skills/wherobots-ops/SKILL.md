---
name: wherobots-ops
description: Use for Wherobots operational discipline — the `wherobots` CLI command structure, MCP usage sequence, runtimes/regions, API keys, and cost hygiene (bounded LIMIT/COUNT before full runs). Covers job submission via job-runs and the gotchas that waste agent turns.
---

# Wherobots Ops

Operational meta-knowledge for running work on Wherobots cost-effectively and without dead-end loops.

**Read [`references/cli-recipes.md`](references/cli-recipes.md)** for the confirmed `wherobots` CLI
command tree, `job-runs` flags, and the raw `api` surface.

## MCP usage discipline

- **Explore before generating.** List catalogs/databases/tables and `describe_table` the target
  before writing SQL against it. Never query assumed columns.
- **Validate cheap before expensive.** `LIMIT` samples and `COUNT(*)` sanity checks before full-table
  spatial joins. Prefilter to an AOI (bbox scalars) first.
- **Two failed attempts = stop.** Re-read the schema and reconsider the table choice; do not loop
  blindly on a failing query.
- **Anything destined for production goes to a repo file**, not left in chat.

## CLI gotchas

- **The binary is `wherobots`, not `wbc`.** Discover commands with `wherobots --tree`.
- **`WHEROBOTS_API_KEY` is required** — even for `--dry-run` (it authenticates before rendering, and
  `job-runs create` on a local script also calls the API to resolve the upload path).
- **Submit jobs with `wherobots job-runs create <script>`** (auto-uploads local scripts, `--watch`
  streams logs). `wherobots api runs create-job-run` is the raw POST equivalent.
- **Default runtime is `tiny`** — too small for real spatial joins; size up with `--runtime`.

## Cost hygiene

- Executing queries and job runs costs money. Keep exploration bounded, size runtimes to the workload,
  and review spend with `wherobots api usage costs get-workload-costs`.

## Related skills

- `wherobots-usage` — interface decision matrix (MCP vs CLI vs SDK vs dashboard) and auth setup.
- `wherobots-develop` — deeper CLI/SDK job workflows.
