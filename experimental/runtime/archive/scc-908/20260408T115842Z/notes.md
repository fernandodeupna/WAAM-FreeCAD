# Active Experiment Notes: SCC_908

## Goal
Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `runtime/active/planner.py` and `runtime/active/helpers.py`.
## Contour then Y-direction fill (2026-04-08T11:56:33Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 53
- inner contours kept: 0
- fill/link paths generated: 216
- hole crossings inserted: 0
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T11:58:22Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 53
- inner contours kept: 0
- fill/link paths generated: 216
- hole crossings inserted: 0
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

