# Wherobots Agent Skills

Agent skills for the [Wherobots](https://wherobots.com) spatial analytics platform. These skills provide AI coding assistants with the meta-knowledge they need to work effectively with Wherobots tools.

## Skills

| Skill | Description |
|---|---|
| `wherobots-usage` | Decision matrix for choosing between MCP, CLI, SDK, and Dashboard; auth setup; scheduling guidance |
| `wherobots-explore` | MCP workflow patterns for data discovery, schema exploration, and spatial query generation |
| `wherobots-develop` | CLI discovery patterns, non-obvious flags, job submission workflow, cost hygiene, Python and TypeScript SDK usage |
| `wherobots-open-data-catalog` | What's in `wherobots_open_data` (Overture, Foursquare, rasters): table selection, join keys, CRS, quirks, snapshots |
| `wherobots-spatial-sql-patterns` | WherobotsDB/Sedona Spatial SQL patterns and dialect gotchas: joins, KNN, CRS transforms, EXPLODE |
| `wherobots-pipeline-designer` | Design/audit medallion (Bronze/Silver/Gold) geospatial pipelines over Iceberg/Havasu |
| `wherobots-area-weighted-interpolation` | Areal interpolation / spatial enrichment between non-matching polygon layers (extensive vs intensive) |

## Installation

### Via skills.sh (any agent)

Installs the skills only. To get the Wherobots MCP server as well, use one of the plugins below.

```bash
npx skills add wherobots/agent-skills@wherobots-usage
npx skills add wherobots/agent-skills@wherobots-explore
npx skills add wherobots/agent-skills@wherobots-develop
npx skills add wherobots/agent-skills@wherobots-open-data-catalog
npx skills add wherobots/agent-skills@wherobots-spatial-sql-patterns
npx skills add wherobots/agent-skills@wherobots-pipeline-designer
npx skills add wherobots/agent-skills@wherobots-area-weighted-interpolation
```

### Via Wherobots VS Code Extension

Skills are bundled in the [Wherobots VS Code extension](https://marketplace.visualstudio.com/items?itemName=Wherobots.wherobotsjobsubmit) and available automatically in Copilot Chat and Claude Code when the extension is installed. This route installs the skills only, not the MCP server.

### Via Claude Code plugin

This repository is also a Claude Code plugin marketplace. Installing the `wherobots` plugin registers the Wherobots MCP server and all seven skills in one step. In Claude Code, run:

```text
/plugin marketplace add wherobots/agent-skills
/plugin install wherobots@wherobots
```

Then run `/reload-plugins`, or restart Claude Code. From a shell, the same steps are `claude plugin marketplace add wherobots/agent-skills` and `claude plugin install wherobots@wherobots`.

The skills appear as `/wherobots:<skill>`, for example `/wherobots:wherobots-explore`, and Claude also loads them on its own when a task matches. The MCP server appears in `/mcp` as `plugin:wherobots:wherobots-mcp-server` and is marked as needing authentication until you sign in: run `/mcp`, select the server, and authenticate. A browser opens to sign in to Wherobots and pick an organization. Headless `claude -p` runs cannot open a browser, so sign in from an interactive session first, or use an API key as described next.

For CI, scripts, or anywhere browser sign-in isn't possible, use a Wherobots API key instead: add the server yourself with an `x-api-key` header as described in [Set Up Wherobots in Claude Code](https://docs.wherobots.com/develop/agentic-tools/claude-code), and turn off the plugin's copy of the server in `/mcp`. The two don't collide: the plugin's copy is always listed as `plugin:wherobots:wherobots-mcp-server`.

The plugin does not install the [Wherobots CLI](https://docs.wherobots.com/develop/cli), which the `wherobots-develop` skill uses to submit jobs. Install it separately; the Claude Code setup page above covers it.

### Via Kiro

This repository includes a Kiro power at [`powers/wherobots`](powers/wherobots). Installing it registers the Wherobots MCP server and all seven skills in one step.

In the Kiro IDE, open the **Powers** panel, choose **Add Custom Power**, then **Import power from GitHub**, and enter:

```text
https://github.com/wherobots/agent-skills/tree/main/powers/wherobots
```

From a clone of this repository, Kiro CLI installs it with `kiro-cli powers install ./powers/wherobots`.

Kiro opens a browser to sign in to Wherobots and pick an organization when you authenticate the server. Manual setup, including the API key alternative to OAuth, is documented at [Set Up Wherobots in Kiro](https://docs.wherobots.com/develop/agentic-tools/editors#kiro).

### Via the Cursor marketplace

This repository is also a Cursor plugin. Installing it registers the Wherobots MCP server and all seven skills in one step, with no manual `.cursor/mcp.json` editing. Search for **Wherobots** in [Cursor's marketplace](https://cursor.com/marketplace).

To install it before the listing is live:

```bash
git clone https://github.com/wherobots/agent-skills ~/.cursor/plugins/local/wherobots
```

Reload Cursor, then check **Cursor Settings > Tools & MCPs** for `wherobots-mcp-server`. The first tool call opens a browser to sign in to Wherobots and pick an organization. Manual setup, including the API key alternative to OAuth, is documented at [Set Up Wherobots in Cursor](https://docs.wherobots.com/develop/agentic-tools/cursor).

## Plugin layout

The repository root is both the Cursor plugin and the Claude Code plugin, so both ship the same `SKILL.md` files that CI validates here. The Kiro power is the one vendored copy, and CI keeps it in sync.

| Path | Purpose |
|---|---|
| `.cursor-plugin/plugin.json` | Cursor plugin manifest, validated in CI against Cursor's published schema |
| `mcp.json` | Registers `wherobots-mcp-server` at `https://api.cloud.wherobots.com/mcp/` for Cursor |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest. Declares the same MCP server inline, plus the skills path |
| `.claude-plugin/marketplace.json` | Claude Code marketplace `wherobots`, listing the plugin at the repo root |
| `powers/wherobots/` | Kiro power: manifest, MCP server entry, and a copy of the skills |
| `schemas/agent-plugin.schema.json` | Agent Plugins manifest schema, vendored for CI |
| `assets/logo.svg` | Marketplace listing icon |

A few constraints shape this layout. Cursor manifest paths cannot use parent traversal, so a plugin in a subdirectory could not reach `.agents/skills/`, which is why the plugin sits at the repo root. Both manifests set `skills` to `.agents/skills` rather than the default `skills/` directory, because the [VS Code extension](https://marketplace.visualstudio.com/items?itemName=Wherobots.wherobotsjobsubmit) resolves skills from that path. And the two tools disagree on the MCP entry: Claude Code requires `"type": "http"` on a remote server and drops an entry without it, while Cursor rejects that field. So `mcp.json` stays Cursor-shaped, the Claude manifest carries its own copy, and CI fails if the two drift apart.

Kiro needs its own copy of the skills in `powers/wherobots/skills`. Its installer doesn't follow symlinks, and its manifest format has no field for a custom skills path, so the power can't reuse `.agents/skills` directly. CI fails if the copy drifts, and also validates `powers/wherobots/plugin.json` against the vendored Agent Plugins schema. Bump `version` there whenever skills or the MCP server change.

CI runs `claude plugin validate --strict` on both Claude Code manifests. Claude Code keeps users on the installed copy until the `version` in `.claude-plugin/plugin.json` changes, so bump it whenever skills or the MCP server change.

Bump `version` in `.cursor-plugin/plugin.json` for every Cursor marketplace submission. Cursor manually reviews each release.

## Design Philosophy

Every line in a skill must pass this test: **"Does this make the agent measurably more effective, or can the agent already discover this from `--help` / MCP tool descriptions?"**

Skills contain meta-knowledge only:
- Interface selection guidance (when to use MCP vs CLI vs SDK)
- Non-obvious workflows and sequences
- Gotchas that waste agent turns when discovered by trial and error
- Decision points between equally valid approaches

Skills do NOT contain:
- Tool parameter documentation (already in MCP/CLI `--help`)
- API reference material (discoverable at runtime)
- Tutorials or step-by-step guides

### The `references/` exception

A skill MAY carry a `references/*.md` file, loaded on demand rather than up front, when the material
is **validated** and **not runtime-discoverable**: query templates proven against live compute (with
the date and result recorded), layer contracts, dataset behaviors that produce plausible-but-wrong
answers. The bar is the same test above, plus one more: **transcribing `--help` or `describe_table`
output into a reference file is not allowed** — it bloats context and goes stale silently. Prefer
"here is the join key and what surprises you" over "here is every column."

Reference files record the date they were validated and what they were validated against, so a
reader can tell stale content from current.

## Benchmarks

The `benchmarks/` directory contains tasks for evaluating skill effectiveness. See [benchmarks/README.md](benchmarks/README.md) for details.

## Contributing

1. Fork this repository
2. Create a branch (`feat/improve-explore-skill`)
3. Edit the relevant `SKILL.md` file
4. Ensure frontmatter is valid: `name` must match the parent directory, `description` is required
5. If the change should reach Claude Code plugin users, bump `version` in `.claude-plugin/plugin.json`
6. Copy the change to the Kiro power with `rm -rf powers/wherobots/skills && cp -R .agents/skills powers/wherobots/skills`, and bump `version` in `powers/wherobots/plugin.json`
7. Open a pull request -- CI will validate frontmatter, lint markdown, and check every plugin manifest

## License

MIT
