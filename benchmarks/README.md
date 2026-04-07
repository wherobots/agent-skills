# Benchmarks

This directory contains benchmark tasks for evaluating how well AI agents perform Wherobots platform tasks with and without the agent skills loaded.

## Purpose

Each task in `tasks/` defines a self-contained objective that an agent should be able to complete. By running the same task with and without skills loaded, we can measure the impact of each skill on agent success rate.

## Task Format

Each task file contains:

- **Objective**: What the agent must accomplish
- **Starting state**: Pre-conditions (env vars, files, tools available)
- **Success criteria**: How to verify the task was completed correctly
- **Allowed tools**: Which tools/CLIs the agent may use
- **Skill under test**: Which skill(s) to load for the A condition (omit for B condition)

## Running Benchmarks

1. **A condition** (with skill): Load the skill specified in the task, then give the agent the objective.
2. **B condition** (without skill): Give the agent the same objective without loading any Wherobots skills.
3. **Evaluate**: Check each success criterion. Score as pass/fail per criterion.

## Scoring

- Each success criterion is worth 1 point
- Task score = passed criteria / total criteria
- Skill impact = average A score - average B score across all tasks for that skill

Store results in `results/` as JSON files named `{task}-{condition}-{timestamp}.json`.
