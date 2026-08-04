# HTSA-Explorer

HTSA-Explorer is a research prototype for interactive abstraction of
hierarchical time series. It reads a hierarchy from GraphML, evaluates temporal
similarity within branches, selects representative subtrees, and presents the
result as a compact summary tree in the browser.

The repository contains the original Python/Flask backend, the browser client,
and the GraphML datasets used by the prototype.

## Main capabilities

- Path-greedy and optimal-search subtree selection.
- FDS, Euclidean, z-normalized Euclidean, DTW, LCSS, and MSM similarity.
- Deterministic construction of a summary tree from the selected subtrees.
- Interactive GraphML upload, parameter configuration, tree navigation, and
  time-series visualization.
- Built-in ACM topic and Chinese equity examples.

## Repository contents

```text
app.py                                  Flask service and HTSA algorithms
templates/appnewest.html                Interactive browser client
acm copy.graphml                        ACM topic hierarchy
stockgraph copy.graphml                 Chinese equity hierarchy
region_hierarchy_with_timeseries.graphml  European regional GDP hierarchy
graph copy.graphml                      Original example hierarchy
requirements.txt                        Pinned Python dependencies
```

## Reference environment

The reported prototype environment uses Python 3.6.5 with Flask 1.0.2,
NetworkX 2.1, and NumPy 1.19.5. The complete pinned dependency list is in
`requirements.txt`.

## Run locally

```bash
git clone https://github.com/caracallium/HTSA-Explorer.git
cd HTSA-Explorer
python -m pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000` in a browser. The current prototype starts
Flask's development server with debugging enabled and is intended for local
research use rather than direct public deployment.

The browser visualization loads D3 from jsDelivr, so an internet connection is
required when opening the interface.

## GraphML input

- Directed edges represent parent-child relationships.
- Each analyzable node must contain a numeric time-series attribute.
- The ACM example uses `time_series`; the equity example uses `d0`.
- Series may be supplied as bracketed, comma-separated, or whitespace-separated
  numeric values.

## License

The source code is available under the MIT License; see `LICENSE`. Bundled
datasets retain the rights and attribution requirements of their original
sources and are not relicensed by the MIT software license.

