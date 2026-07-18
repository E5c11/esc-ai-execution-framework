# Workflows — Framework Development

This directory tracks development and refinement of the ESC AI Execution Framework.
It does not contain feature workflows for consuming projects.

For how to use the framework, see [`INSTRUCTIONS.md`](../../INSTRUCTIONS.md). That file is
the canonical instruction source; framework workflows should reference it rather than
duplicate it.

## Structure

- **`active/`** — execution capabilities currently being specified or implemented.
- **`archive/`** — completed capability workflows retained for history.

## Working Method

Develop one capability at a time:

1. Define the problem and a measurable success condition.
2. Specify the smallest useful vertical slice.
3. Identify what is generic and what belongs in a consuming project's profile.
4. Exercise the slice against at least one real repository.
5. Record limitations and maintenance or staleness risks.
6. Refine the convention before expanding it or starting the next capability.
7. Move the completed workflow from `active/` to `archive/`.

Initial candidate capabilities include profile-driven test/report summaries, task
specifications, repository/component indexes, executable architecture checks, durable
checkpoints, dependency impact maps, and execution-efficiency measurement.

The current agreed sequence and status live in
[`active/roadmap.md`](./active/roadmap.md). Work from that roadmap one capability at a
time; capability-specific workflow documents should be linked from its table.
