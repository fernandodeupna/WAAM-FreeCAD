# Active Experiment Notes: Pieza2

## Goal
Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `runtime/active/planner.py` and `runtime/active/helpers.py`.
## YZ infill override (2026-04-08T07:50:41Z)
- scope: experimental runtime planner only
- change: projected structured INFILL segments onto constant-X lines
- target fill orientation: YZ plane in torch-path view
- infill segments updated: 150
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_yz.json

## Longitudinal XZ infill override (2026-04-08T07:56:35Z)
- scope: experimental runtime planner only
- change: rewrote structured INFILL paths as longitudinal constant-X stripes
- target fill orientation: longitudinal interior trajectory in XZ plane
- infill segments updated: 150
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_xz_longitudinal.json

## Topology-aware longitudinal experimental planner (2026-04-08T08:02:37Z)
- scope: experimental runtime planner only
- reverted the previous global stripe override
- change: rebuilt INFILL using contour intersections at constant-X stations
- hole handling: subtract INNER contour Y intervals from OUTER material span
- longitudinal infill segments emitted: 336
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_topology_longitudinal.json

## Y-ordered contour-first longitudinal planner (2026-04-08T09:00:49Z)
- scope: experimental runtime planner only
- order: outer contour, inner hole contours, then fill rows
- fill progression: lowest Y to highest Y
- per-row behavior: deposit on material windows, cross holes with LINK travel
- generated infill/link paths: 482
- hole crossings inserted: 166
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_y_ordered_fill.json

## Y-ordered contour-first longitudinal planner (2026-04-08T09:06:28Z)
- scope: experimental runtime planner only
- order: outer contour, inner hole contours, then fill rows
- fill progression: lowest Y to highest Y
- per-row behavior: deposit on material windows, cross holes with LINK travel
- generated infill/link paths: 482
- hole crossings inserted: 166
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_y_ordered_fill.json

## XZ-oriented contour-first planner with torch-off hole crossings (2026-04-08T09:09:26Z)
- scope: experimental runtime planner only
- order: outer contour, inner hole contours, then XZ-oriented fill
- fill direction: constant-X columns, deposition along Y
- hole behavior: LINK paths marked for torch-off travel
- generated infill/link paths: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_xz_fill.json

## Contour-only experimental planner (2026-04-08T09:15:10Z)
- scope: experimental runtime planner only
- output: outer contour plus inner hole contours only
- infill: disabled intentionally for this stage
- outer contours kept: 7
- inner contours kept: 21
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_only.json

## Contour then Y-direction fill (2026-04-08T09:22:01Z)
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T09:27:39Z)
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T09:30:56Z)
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T09:39:28Z)
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T09:42:15Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T09:45:28Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T10:29:05Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T10:37:10Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 522
- hole crossings inserted: 186
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T10:50:10Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 750
- hole crossings inserted: 300
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T10:53:33Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 280
- hole crossings inserted: 112
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Contour then Y-direction fill (2026-04-08T11:54:40Z)
- EXECUTION_MARKER: experimental_runtime_active_helpers_v2
- scope: experimental runtime planner only
- order: outer contour, inner contours, then fill
- fill direction: Y direction
- X progression: low X to high X
- hole behavior: LINK paths for same-line torch-off travel
- outer contours kept: 7
- inner contours kept: 21
- fill/link paths generated: 280
- hole crossings inserted: 112
- temporary plan: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan_experimental_contour_then_y_fill.json

## Archived Source
- archive: `Waam_tech/experimental/runtime/archive/pieza2/20260408T115633Z`
- source notes: `Waam_tech/experimental/runtime/archive/pieza2/20260408T115633Z/notes.md`
