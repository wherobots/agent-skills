# Task 03: Submit Job

## Objective

Submit the Python script at `/tmp/example_job.py` as a Wherobots job named "benchmark-run", wait for it to complete, and report the final status.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The `wherobots` CLI is installed and on PATH
- `/tmp/example_job.py` exists and contains a valid PySpark script

## Success Criteria

- [ ] Agent uses the CLI (not MCP or SDK) for job submission
- [ ] Agent discovers the correct CLI command via `--help` rather than guessing
- [ ] Job appears in `wherobots job-runs list` output
- [ ] Logs were streamed during execution (via `--watch` or `logs --follow`)
- [ ] Final job status (COMPLETED or FAILED) is reported to the user

## Allowed Tools

- Bash (`wherobots` CLI only)

## Skill Under Test

`wherobots-develop`
