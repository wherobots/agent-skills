# Wherobots CLI — Confirmed Command Structure

**Binary:** `wherobots`  (⚠️ **not `wbc`** — CLAUDE.md and older notes call it `wbc`; the installed
and documented binary is `wherobots`.)
**Version confirmed:** `Wherobots CLI 1.0.2` (a newer `v1.1.0` is available; `wherobots upgrade` to update).
**Confirmed:** 2026-07-10 by running `--help` / `--tree` on every group.

> All command structure below was verified by executing `wherobots --tree` and `--help` on each
> subcommand on 2026-07-10 against CLI 1.0.2. Flag names/defaults are copied verbatim from `--help`.

---

## Auth (required before anything runs)

The CLI authenticates via the **`WHEROBOTS_API_KEY`** environment variable. There is **no config
file** (`~/.config/wherobots`, `~/.wherobots` are not used by 1.0.2).

```bash
export WHEROBOTS_API_KEY='<your-api-key>'   # create at https://cloud.wherobots.com/settings#api-keys
```

**Gotcha (confirmed 2026-07-10):** `--dry-run` is **not** an offline mode.
- `wherobots api runs create-job-run … --dry-run` fails fast with `WHEROBOTS_API_KEY is required`
  before rendering the curl — you need a real key exported.
- `wherobots job-runs create <local-script> … --dry-run` fails even *earlier* with
  `unable to resolve managed storage directory via API: … WHEROBOTS_API_KEY is required`, because
  it makes a live API call to resolve the auto-upload path for the local script before it can render.
  Point `job-runs create` at an `s3://` script (no upload) or use `--no-upload` to skip that call.

---

## Top-level structure

```
wherobots
  api          Direct API access to Wherobots services (raw REST, one leaf command per endpoint)
  job-runs     Custom job-runs workflows (ergonomic wrapper — USE THIS for submitting jobs)
  upgrade      Upgrade the CLI to the latest release
  help         Help about any command
```

### Global flags (apply to every command)
| Flag | Meaning |
|---|---|
| `--dry-run` | Print the curl equivalent without executing (still needs API key) |
| `--json string` | Raw JSON request body (overrides individual body-field flags) |
| `-q, --query key=value` | Query pair, repeatable |
| `--tree` | Print the available command tree (great for discovery) |
| `-y, --yes` | Skip confirmation prompt (for CI/scripts) |
| `-v, --version` | CLI version (top level only) |

**Discovery shortcut:** `wherobots --tree` (or `wherobots api --tree`) prints the entire command
tree — the fastest way to find the exact leaf command name.

---

## `job-runs` — the pipeline workhorse

This group is the ergonomic path for submitting and monitoring Spark jobs. Prefer it over `api runs`
for day-to-day pipeline work (it auto-uploads local scripts, streams logs, etc.).

### `wherobots job-runs create <script> [flags]`
Submit a job. `<script>` is a positional arg (local path or `s3://…`).

| Flag | Default | Notes |
|---|---|---|
| `-n, --name` | *(required)* | Job name |
| `-r, --runtime` | `tiny` | Compute runtime size |
| `--run-region` | `aws-us-west-2` | Region for this run |
| `--args` | | Space-separated args passed to the script |
| `--dep-file` (repeatable) | | File dependency S3 URI |
| `--dep-pypi` (repeatable) | | PyPI dep as `name==version` |
| `--jar-main-class` | | Main class (**required for JAR** scripts) |
| `--no-upload` | | Disable auto-upload of local scripts |
| `--upload-path` | | Override upload root as `s3://bucket/prefix` |
| `-c, --spark-config` (repeatable) | | Spark config as `key=value` |
| `--timeout` | `3600` | Job timeout in **seconds** |
| `-w, --watch` | | Stream logs until job completes |
| `--output` | `text` | `text` or `json` |

```bash
# Python job, small runtime, stream logs to completion:
wherobots job-runs create ./silver_mapmatch.py \
  --name fleet-mapmatch-silver \
  --runtime small \
  --run-region aws-us-west-2 \
  --args "--date 2026-07-10" \
  --dep-pypi "h3==4.1.0" \
  --timeout 7200 \
  --watch
```

### `wherobots job-runs list [flags]`
| Flag | Default | Notes |
|---|---|---|
| `-l, --limit` | `20` | Max results |
| `-s, --status` (repeatable) | | Filter by status |
| `--name` | | Filter by name pattern |
| `--region` | | Filter by region |
| `--after` | | Runs created after ISO timestamp |
| `--output` | `text` | `text`/`json` |

### `wherobots job-runs logs <run-id> [flags]`
| Flag | Default | Notes |
|---|---|---|
| `-f, --follow` | | Follow logs until run completes |
| `-t, --tail` | | Show only the last N lines |
| `--interval` | `2` | Poll interval (seconds) |
| `--output` | `text` | `text`/`json` |

### `wherobots job-runs metrics <run-id> [flags]`
Instant metrics for a run. Only `--output text|json`.

---

## `api` — raw REST access (one leaf per endpoint)

`wherobots api <group> <operation> [flags]`. Each leaf maps to an HTTP operation; body/query
fields become flags. **Object/array flag values must be JSON strings.** `--json '{…}'` overrides
individual body flags.

### Groups (from `wherobots api --tree`)
`apikey`, `audit-log`, `catalogs`, `cloud-connections`, `environment-override-preset`,
`environment-preset`, `foreign-catalog`, `me`, `organization`, `rasterflow`, `runs`, `service`,
`storage`, `subscriptions`, `union-executions`, `usage`, `users`.

### `wherobots api runs create-job-run` — `POST /runs`
The raw equivalent of `job-runs create`. **Note body-field casing differs from the flags:**

| Flag | Body field (actual JSON) | Req | Type |
|---|---|---|---|
| `--name` | `name` | ✅ | string |
| `--runpython` | `runPython` | | string (s3:// script) |
| `--runjar` | `runJar` | | string |
| `--runtime` | `runtime` | | string |
| `--timeoutseconds` | `timeoutSeconds` | | integer |
| `--version` | `version` | | string (runtime/DB version) |
| `--environment-json` | `environment` | | object (JSON string) |
| `--region` | *(query param)* | | string |

```bash
# Same submission via the raw API + dry-run to inspect the request:
wherobots api runs create-job-run \
  --name fleet-mapmatch-silver \
  --runpython s3://my-bucket/silver_mapmatch.py \
  --runtime small --timeoutseconds 7200 \
  --region aws-us-west-2 --dry-run
```

### Other high-value `api runs` leaves
- `list-job-runs` — `GET /runs`; query flags `--name --created-after --status --region --cursor --size`.
- `get-job-run` — fetch a single run.
- `runs cancel cancel-job-run` — cancel.
- `runs logs get-job-run-logs` — logs.
- `runs metrics get-job-run-metrics` — metrics.

### Catalog / table exploration (mirrors the MCP tools, from CLI)
```
api catalogs list-catalogs
api catalogs create-catalog
api catalogs hierarchy get-catalog-hierarchy
api catalogs namespaces list-namespaces
api catalogs namespaces tables list-tables
api catalogs namespaces tables get-table
api catalogs namespaces tables delete-table
api catalogs namespaces tables credentials get-table-credentials
api catalogs namespaces views get-view
```

### Storage (managed S3 workspace)
```
api storage list-integration | get-integration | create-integration | delete-integration
api storage directories list-directory-contents | create-directory | delete-directory
api storage files download-file-from-storage | delete-file-from-storage
api storage file-upload-url create-file-upload-url
api storage credentials get-storage-credentials
api storage sample-policy get-sample-policy        # sample IAM policy
api storage trust-relationship get-trust-relationship
```

### Service principals (for scheduled/CI auth — see wherobots-usage)
```
api service list-service-principals
api service register register-service-principal
api service get-service-principal | update-service-principal | delete-service-principal
```

### Org / runtime & region defaults
```
api organization default-runtime set-default-runtime
api organization default-region  set-default-region
api organization get-my-organization | update-my-organization
api organization quota organization list-quotas
```

### Usage & cost (cost hygiene)
```
api usage costs     get-workload-costs      # cost breakdown by workload
api usage history   get-workload-history    # usage history
api usage workloads info get-workload-usage-info
api usage workloads chart get-workload-usage-chart
```

### Notebooks, presets, connections, rasterflow (reference)
```
api me jupyter lab instance create-notebook-instance | list-notebook-instances | get-notebook-instance
api me jupyter lab instance destroy destroy-notebook-instance
api environment-preset create-or-update-environment-preset | list-environment-presets | delete-...
api environment-override-preset  (same shape)
api cloud-connections create-cloud-connection | list-cloud-connections | verify verify-cloud-connection
api cloud-connections cft ... download-*-template   # CloudFormation templates for bindings
api foreign-catalog create-foreign-catalog | list-foreign-catalogs | test-connection test-*
api rasterflow runs cancel-rasterflow-run
api subscriptions get-my-subscription
api union-executions get-union-execution-details
api users me get-me
```

---

## `wherobots upgrade [flags]`
| Flag | Notes |
|---|---|
| `--tag` | Release tag to install (default `latest`) |
| `--install-dir` | Override install directory |
| `--skip-checksum` | Skip SHA-256 verification (avoid unless necessary) |

---

## Gotchas & conventions (confirmed 2026-07-10)

1. **Binary is `wherobots`, not `wbc`.** Update any docs/scripts that say `wbc`.
2. **`WHEROBOTS_API_KEY` is mandatory**, even for `--dry-run`. No config-file fallback in 1.0.2.
3. **`job-runs` vs `api runs`:** `job-runs create` is the ergonomic wrapper (positional `<script>`,
   local-file auto-upload, `--watch`, `--dep-pypi`/`--dep-file`). `api runs create-job-run` is the
   raw POST (s3:// script only via `--runpython`, no auto-upload). Prefer `job-runs` for pipelines.
4. **Body-field casing:** raw API bodies are camelCase (`runPython`, `timeoutSeconds`) even though
   the flags are lowercase (`--runpython`, `--timeoutseconds`). Matters when using `--json`.
5. **Default runtime is `tiny`; default region `aws-us-west-2`; default timeout 3600s.** Override
   `--runtime`/`--run-region`/`--timeout` for real workloads — `tiny` will be too small for spatial joins.
6. **Object/array flag values in `api` must be JSON strings** (e.g. `--environment-json '{"FOO":"bar"}'`).
7. **`--tree` is the discovery tool.** The `api` surface is large; `wherobots api <group> --tree`
   beats guessing leaf names.
8. **`-y/--yes`** for non-interactive/CI; **`--output json`** (job-runs) or default JSON (api) for scripting.
