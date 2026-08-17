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

For a production-style local launch on Windows, use the threaded Waitress
entry point:

```text
powershell -ExecutionPolicy Bypass -File scripts/run_production.ps1
```

On Linux/macOS, `scripts/run_production.sh` starts Gunicorn with the checked-in
configuration (two worker processes and four threads per worker by default).
The same WSGI entry point is used by `Procfile` and `render.yaml`.

## Run the tests

```text
python -m unittest discover -s tests -v
```

The test suite checks browser and health routes, bundled dataset access,
request validation, both public selection strategies, all six similarity
methods, deterministic multi-parent normalization, exact-search resource-guard
fallback, and non-overwriting audit records.

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

For large hierarchies use `Path-greedy`. Exact `Optimal-Search` uses a
deployment resource guard because its Pareto frontier can grow rapidly. The
interactive default is 50 analyzable vertices; when a larger request selects
`Optimal-Search`, the API executes `Path-greedy` and records both the requested
and executed strategies in `execution`. Set `optimal_overflow` to `error` for
strict API behavior. Operators can change `HTSA_OPTIMAL_MAX_NODES`, or set it
to `0` for an unguarded controlled run.

Each successful request receives a UUID and normalized-input SHA-256 digest.
The service writes a unique JSON audit record and summary-edge file under
`runtime_data/`; repeated runs do not overwrite one another. The browser also
stores the twelve most recent completed runs in IndexedDB, restores them after
page reload or browser restart, and exports a self-contained run record as
JSON. Uploaded inputs and run records remain local to that browser profile and
deployment unless the user exports them.

## Repository layout

```text
app.py                       Flask service and abstraction algorithms
wsgi.py                      Production WSGI entry point
gunicorn.conf.py             Multiworker/multithread server configuration
render.yaml / Procfile       Reproducible hosted-deployment entry points
templates/appnewest.html     Single-page analytical client
static/vendor/               Pinned local D3 bundle and license
datasets/                    Three GraphML application datasets
scripts/reproduce_tables.py  Computational reproduction entry point
tests/                       Backend, route, and algorithm tests
runtime_data/                Unique JSON and summary-edge audit files (ignored)
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

## Deployment and resource controls

`python app.py` is the local development entry point and binds to
`127.0.0.1:5000` with debugging disabled. Production deployments use
`wsgi:app`: Gunicorn on Unix-like systems and Waitress on Windows. The checked-in
Gunicorn defaults provide two worker processes, four threads per worker,
worker recycling, and a 180-second request timeout. Request bodies default to
64 MiB. Configure these controls with `HTSA_WEB_WORKERS`, `HTSA_WEB_THREADS`,
`HTSA_REQUEST_TIMEOUT_SECONDS`, and `HTSA_MAX_REQUEST_BYTES`.

`HTSA_RUNTIME_DIR` selects the server audit-record directory. Browser history
uses IndexedDB and therefore survives page reloads without sending records to
an external database. A public multiuser deployment that requires shared or
cross-device experiment management should attach a durable database or object
store and add authentication, authorization, rate limits, and process isolation
for untrusted uploads. See `SECURITY.md`.

## Citation and license

Citation metadata are provided in `CITATION.cff`. Source code is released under
the MIT License. D3 and all datasets are governed separately; see
`datasets/README.md`, `datasets/manifest.json`, and `THIRD_PARTY_NOTICES.md`.
The complete author list and contact details are in `AUTHORS.md`.
All three datasets are included in this repository with project-author
authorization confirmed on 2026-08-17 for academic reproduction and research
reuse. They are not covered by the software's MIT license: the regional GDP
snapshot requires Eurostat attribution, the ACM snapshot requires ACM source
attribution and compliance with ACM terms, and the equity snapshot is
non-commercial research data acquired through AKShare and remains subject to
AKShare and underlying-source conditions. The exact provenance, transformation,
and reuse notices are recorded in `datasets/README.md` and
`datasets/manifest.json`.
