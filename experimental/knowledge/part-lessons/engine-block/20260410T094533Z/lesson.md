# Active Experiment Notes: Engine_block

## Goal
Create the next part-specific experimental improvement from the selected per-part runtime workspace.

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `Waam_tech/experimental/runtime/parts/engine-block/aa79bb1eb946/planner.py` and `Waam_tech/experimental/runtime/parts/engine-block/aa79bb1eb946/helpers.py`.

## Seed
- source: latest archived run for the same part key
- archive: `Waam_tech/experimental/runtime/archive/engine-block/20260409T130925Z`

## Archived Source
- archive: `Waam_tech/experimental/runtime/archive/engine-block/20260410T094533Z`
- source notes: `Waam_tech/experimental/runtime/archive/engine-block/20260410T094533Z/notes.md`
