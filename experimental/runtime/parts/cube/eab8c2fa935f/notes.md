# Active Experiment Notes: Cube

## Goal
Create the next part-specific experimental improvement from the selected per-part runtime workspace.

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `Waam_tech/experimental/runtime/parts/cube/eab8c2fa935f/planner.py` and `Waam_tech/experimental/runtime/parts/cube/eab8c2fa935f/helpers.py`.

## Seed
- source: clean runtime template
## Scaffold export (2026-04-21T07:56:22Z)
- part: cube
- goal: Create the next part-specific experimental improvement from the selected per-part runtime workspace.
- change: copied the current waam_baseline.nc into waam_baseline_experimental.nc
- expected improvement: provide a safe editable starting point for the next iteration

