# Engine Block

## Part identity
- part key: `engine-block`
- geometry family: bulk_fill
- status: active

## Geometry summary
- describe the part in manufacturing terms
- note the intended WAAM objective

## Build orientation assumption
- state the intended build Z and why
- status: confirmed / suspected / needs verification

## Expected topology zones
- Zone 1:
- Zone 2:
- Zone 3:

## Baseline NC observations
- what does the current `waam_baseline.nc` do well?
- what does it get wrong?

## Failure modes observed
- record concrete path-planning or topology failures

## Experimental planner path
- which experimental planner / module is being used?
- `Waam_tech/experimental/runtime/parts/engine-block/0f3f8c3f70e7/planner.py`
- which files were created or modified?
- lesson: `Waam_tech/experimental/knowledge/part-lessons/engine-block/0f3f8c3f70e7/lesson.md`
- lesson context: `Waam_tech/experimental/knowledge/part-lessons/engine-block/0f3f8c3f70e7/context.json`
- experimental NC: `/Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/nc_files/waam_baseline_experimental.nc`
- slice plan: `/Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_slice_plan.json`
- features: `/Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/features/features.json`

## Iterations tried
- Attempt 1:
- Attempt 2:
- Attempt 3:

## Current best result
- describe the current best NC behavior
- note what is still wrong

## Reusable lessons
- distill anything that may apply to future parts

## Promotion candidates
- planner-pattern candidate:
- failure-mode candidate:
- production promotion:

## Notes for future AI review
- what should a future model read first?
- `Waam_tech/experimental/knowledge/part-lessons/engine-block/0f3f8c3f70e7/lesson.md`
- `Waam_tech/experimental/knowledge/part-lessons/engine-block/0f3f8c3f70e7/context.json`
- `Waam_tech/experimental/knowledge/part-lessons/promoted/strategies/solid-section-contour-then-hatch.md`
- `Waam_tech/experimental/knowledge/part-lessons/promoted/strategies/voided-section-hole-safe-fill.md`
- `Waam_tech/experimental/knowledge/part-lessons/engine-block/20260409T105311Z/lesson.md`
- `Waam_tech/experimental/knowledge/part-lessons/pieza2/20260409T125926Z/lesson.md`
- `Waam_tech/experimental/knowledge/part-lessons/scc-908/20260409T105901Z/lesson.md`
- `Waam_tech/experimental/knowledge/part-lessons/scc-908/20260409T100238Z/lesson.md`
- `Waam_tech/experimental/runtime/parts/engine-block/0f3f8c3f70e7/planner.py`
- `Waam_tech/experimental/runtime/parts/engine-block/0f3f8c3f70e7/helpers.py`
- what remains uncertain?
- what evidence would most improve confidence?
