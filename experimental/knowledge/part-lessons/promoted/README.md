# Promoted Experimental Lessons

This folder is the stable, curated knowledge base for the experimental WAAM planner.

Use it for lessons that are worth keeping in Git because they are reusable across runs, parts, or prompt iterations.

Do not store every generated lesson here.
Generated lesson bundles under `Waam_tech/experimental/knowledge/part-lessons/<part>/<workspace>/` stay local and disposable.

## Layout

- `strategies/`
  reusable planning strategies that apply to many parts or many layers
- `parts/`
  manually promoted notes for a specific recurring part key, stored as `<part-key>.md`

## Promotion Rule

Promote a lesson only when it is:

- repeatable
- specific enough to guide planner edits
- general enough to help future prompt iterations
- supported by one or more successful experimental runs

## Naming

- strategy files: short descriptive slug, for example `voided-section-hole-safe-fill.md`
- part files: exact part key, for example `schenkel.md`
