# Active Experiment Notes: Engine_block

## Goal
Create the next part-specific experimental improvement from the selected per-part runtime workspace.

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `Waam_tech/experimental/runtime/parts/engine-block/f6aee62660e3/planner.py` and `Waam_tech/experimental/runtime/parts/engine-block/f6aee62660e3/helpers.py`.

## Seed
- source: latest archived run for the same part key
- archive: `Waam_tech/experimental/runtime/archive/engine-block/20260409T130310Z`
## Contour then layer-by-layer zig-zag fill (2026-04-21T09:45:21Z)
- scope: experimental runtime planner only
- order: outer contour, inner contours, then zig-zag fill
- fill direction: alternating Y-direction zig-zag by X columns
- X progression: alternating path continuity
- hole behavior: LINK paths for torch-off travel with extra clearance around hole spans
- contour refinement: closed-loop resampling at 0.45 mm target step plus closed-loop smoothing
- infill density: 12 X columns for more conservative interior deposition
- outer contours kept: 110
- inner contours kept: 220
- fill/link paths generated: 5390
- hole crossings inserted: 2640
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

