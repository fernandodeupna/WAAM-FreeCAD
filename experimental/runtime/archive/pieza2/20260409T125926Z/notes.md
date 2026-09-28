# Active Experiment Notes: Pieza2

## Goal
Create the next part-specific experimental improvement from the selected per-part runtime workspace.

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `Waam_tech/experimental/runtime/parts/pieza2/1a00c4a9063d/planner.py` and `Waam_tech/experimental/runtime/parts/pieza2/1a00c4a9063d/helpers.py`.

## Seed
- source: latest archived run with the same features signature
- archive: `Waam_tech/experimental/runtime/archive/pieza2/20260408T134126Z`
## Contour then Y-direction fill (2026-04-09T10:59:01Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 10
- inner contours kept: 30
- fill/link paths generated: 400
- hole crossings inserted: 160
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

