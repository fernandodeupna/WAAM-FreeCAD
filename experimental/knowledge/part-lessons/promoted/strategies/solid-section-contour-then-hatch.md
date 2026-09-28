# Promoted Strategy: Solid Section Contour Then Hatch

## When To Use

- section topology: one outer contour and no inner voids
- expected geometry: bulk or simple closed section
- expected process goal: fill the section efficiently while keeping a predictable boundary

## Avoid When

- the same layer contains one or more true inner voids
- the section breaks into disconnected islands that need separate ordering logic

## Path Order

- first: outer contour
- then: additional contour pass only if wall thickness is too small for hatch
- finally: serpentine hatch across the full material span

## Torch Rules

- keep torch on for the contour
- keep torch on across each deposition hatch segment
- use torch-off travel only for normal repositioning between non-touching paths

## Deposition Rules

- contour rule: establish the boundary first
- infill rule: deposit continuously across the whole section width
- hole / void rule: not applicable in this strategy
- region transition rule: keep the fill direction alternating to reduce long return jumps

## Known Risks

- thermal: repeated short contour loops can overheat small sections
- short segments: very narrow sections may be better as contour-only
- continuity: poor hatch ordering increases empty travel

## Evidence

- archived runs: use as the baseline reference for simple bulk-fill sections
- parts where it worked well: generic no-void layers

## Related Code

- runtime orchestration: `Waam_tech/experimental/runtime/loader.py`
- knowledge wiring: `Waam_tech/experimental/layer_by_layer_scaffold.py`
- slice-plan source: `Waam_tech/slicing/slice_plan.py`
