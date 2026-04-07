# Wherobots Agent Skills

Agent skills for the [Wherobots](https://wherobots.com) spatial analytics platform. These skills provide AI coding assistants with the meta-knowledge they need to work effectively with Wherobots tools.

## Skills

| Skill | Description |
|---|---|
| `wherobots-usage` | Decision matrix for choosing between MCP, CLI, SDK, and Dashboard; auth setup; scheduling guidance |
| `wherobots-explore` | MCP workflow patterns for data discovery, schema exploration, and spatial query generation |
| `wherobots-develop` | CLI discovery patterns, non-obvious flags, job submission workflow, Python and TypeScript SDK usage |

## Installation

### Via skills.sh (Claude Code, any agent)

```bash
npx skills add wherobots/agent-skills@wherobots-usage
npx skills add wherobots/agent-skills@wherobots-explore
npx skills add wherobots/agent-skills@wherobots-develop
```

### Via Wherobots VS Code Extension

Skills are bundled in the [Wherobots VS Code extension](https://marketplace.visualstudio.com/items?itemName=Wherobots.wherobotsjobsubmit) and available automatically in Copilot Chat and Claude Code when the extension is installed.

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

## Benchmarks

The `benchmarks/` directory contains tasks for evaluating skill effectiveness. See [benchmarks/README.md](benchmarks/README.md) for details.

## Contributing

1. Fork this repository
2. Create a branch (`feat/improve-explore-skill`)
3. Edit the relevant `SKILL.md` file
4. Ensure frontmatter is valid: `name` must match the parent directory, `description` is required
5. Open a pull request -- CI will validate frontmatter and lint markdown

## License

MIT
