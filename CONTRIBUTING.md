# Contributing

1. Create a topic branch from the repository's main branch.
2. Keep algorithm changes deterministic for a fixed input and configuration.
3. Add or update tests under `tests/`.
4. Run `python -m unittest discover -s tests -v`.
5. Run `python scripts/reproduce_tables.py --quick` when changing parsing,
   similarity, normalization, selection, or summary construction.
6. Do not commit generated files from `runtime_data/`, `exports/`, or `results/`.

Changes to a similarity measure should document its input assumptions, output
range, default parameters, and numerical behavior on constant or unequal-length
series. Changes to a selection strategy must preserve the common group output
contract used by `build_summary_tree`.
