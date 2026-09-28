# Active Experiment Notes: 3dblock

## Goal
Generate 3dblock.step experimental NC without outer contour, using continuous serpentine zig-zag fill.

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Remove the initial outer contour pass.
- Emit only continuous serpentine zig-zag fill.
- Avoid per-line reset to the top of the part.
- Keep changes local to `Waam_tech/experimental/runtime/active/planner.py` and `Waam_tech/experimental/runtime/active/helpers.py`.

## Blocking issue
- `3dblock.step` is selected correctly.
- Current headless baseline import fails with: `No visible solids imported from STEP for headless WAAM pipeline.`
- So the experimental code is ready, but fresh 3dblock-specific toolpaths cannot be generated until that STEP imports as visible solids.
