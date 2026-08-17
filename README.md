# HTSA-Explorer

HTSA-Explorer is a browser-based research software system for abstracting a
large hierarchy of time series into disjoint representative subtrees and an
interactive summary tree. It combines a Python/Flask computation service with
a D3 browser client and supports FDS, Euclidean, z-normalized Euclidean, DTW,
LCSS, and MSM similarity measures.

This repository is the local archival candidate accompanying the manuscript
*HTSA-Explorer: Interactive Abstraction and Application Analysis of
Hierarchical Time Series*.

Public demonstration: <https://htsa-explorer.onrender.com/>. The demonstration
uses an on-demand hosting tier, so the first request after inactivity can be
slower than subsequent requests. For reproducibility, use the local workflow
below.

## Quick start

The supported reference environment is Windows 11 with Python 3.12.13,
Flask 3.1.2, NetworkX 3.5, and NumPy 2.2.6. These are the versions used for
the reported backend measurements. Python 3.6 is no longer supported.

With Conda:

```text
conda env create -f environment.yml
conda activate htsa-explorer
python app.py
```

With an existing Python 3.12 environment:

```text
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`. The ACM, equity, and European regional GDP
examples can be launched directly from the home screen. Any compatible GraphML
hierarchy can also be uploaded.

The three home-screen presets use the manuscript configurations:

| Preset | Similarity | Strategy | Budget |
| --- | --- | --- | ---: |
| ACM hierarchy | FDS | Path-greedy | 30 |
| Equity hierarchy | FDS | Path-greedy | 20 |
| European regional GDP | FDS | Path-greedy | 15 |

The client vendors D3 7.9.0 under `static/vendor`, so the analytical interface
does not require a JavaScript CDN at runtime.

## Run the tests

```text
python -m unittest discover -s tests -v
```

The test suite checks browser and health routes, bundled dataset access,
request validation, both public selection strategies, all six similarity
methods, deterministic multi-parent normalization, and the bounded exact-search
guard.

## Reproduce the computational tables

A quick installation check uses one run per case:

```text
python scripts/reproduce_tables.py --quick
```

The manuscript protocol uses one warmup, seven timed application and strategy
runs, and three timed similarity-measure runs:

```text
python scripts/reproduce_tables.py
```

The script writes machine-readable output to `results/reproduction.json` and
prints the same records to the terminal. The checked-in
`results/reference-results.json` records one complete Python 3.12 reference
run, including package versions and input checksums. GraphML parsing and forest
normalization are excluded from the timed region, matching the paper; elapsed
times remain machine dependent.

## Programmatic use

The browser calls the JSON endpoint directly. A minimal request is:

```python
import requests

payload = {
    "filename": "example.graphml",
    "ts_key": "time_series",
    "node_dict": {
        "root": {"time_series": [1, 2, 3], "value": 3},
        "child": {"time_series": [1, 2, 4], "value": 2},
    },
    "edges": [["root", "child"]],
    "htsa": {"method": "FDS", "strategy": "Path-greedy", "k": 1},
}

result = requests.post("http://127.0.0.1:5000/api/htsa", json=payload)
result.raise_for_status()
print(result.json())
```

The response reports coverage according to the KDD definition: the sum of
importance values in the selected subtrees. `coverage.importance_fraction`
normalizes that mass by total analyzed importance for cross-dataset display.
`coverage.selected_vertices` is provided separately and must not be interpreted
as the coverage metric.

For large hierarchies use `Path-greedy`. `Optimal-Search` is intentionally
limited to at most 50 analyzable vertices per request.

## Repository layout

```text
app.py                       Flask service and abstraction algorithms
templates/appnewest.html     Single-page analytical client
static/vendor/               Pinned local D3 bundle and license
datasets/                    Three GraphML application datasets
scripts/reproduce_tables.py  Computational reproduction entry point
tests/                       Backend, route, and algorithm tests
runtime_data/                Generated summary-edge audit files (ignored)
exports/                     Browser SVG exports (ignored)
results/                     Reference and generated reproduction outputs
```

## GraphML contract

- Directed edges encode parent-child relationships.
- Each analyzable node contains a numeric time series in the selected GraphML
  attribute.
- Series may be bracketed, comma-separated, or whitespace-separated.
- Missing or unusable series are excluded.
- Multiple parents are resolved deterministically by the selected temporal
  similarity, with identifier-based tie breaking.
- A node's default importance is the sum of its parsed observations; API users
  may supply a nonnegative `value` field instead.

## Deployment boundary

The default server binds to `127.0.0.1:5000` with debugging disabled. Set
`HTSA_HOST`, `HTSA_PORT`, or `HTSA_DEBUG=1` to change local behavior. A public
deployment should add authentication, request isolation, rate limits, HTTPS,
and persistent storage. See `SECURITY.md`.

## Citation and license

Citation metadata are provided in `CITATION.cff`. Source code is released under
the MIT License. D3 and all datasets are governed separately; see
`datasets/README.md`, `datasets/manifest.json`, and `THIRD_PARTY_NOTICES.md`.
The complete author list and contact details are in `AUTHORS.md`.
All three datasets are included in this repository with project-author
authorization confirmed on 2026-08-17. That confirmation does not place the
datasets under MIT or grant downstream reuse rights; the ACM and equity source
terms still need to be documented.
