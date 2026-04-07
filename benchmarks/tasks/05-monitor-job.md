# Task 05: Monitor Job

## Objective

Check the status of the most recent Wherobots job run, retrieve its logs, and report whether it succeeded or failed along with any error messages.

## Starting State

- `WHEROBOTS_API_KEY` is set in the environment
- The `wherobots` CLI is installed and on PATH
- At least one job run exists in the account (from a previous submission)

## Success Criteria

- [ ] Agent uses CLI to list recent job runs
- [ ] Agent uses `--output json` to get machine-parseable output for status extraction
- [ ] Agent identifies the most recent job run by timestamp or ID
- [ ] Agent retrieves and displays logs for the identified job
- [ ] Final status and any error messages are clearly reported

## Allowed Tools

- Bash (`wherobots` CLI only)

## Skill Under Test

`wherobots-develop`
