"""Reproduce the backend measurements reported in the software paper.

The script uses the same public computation functions as the Flask service. It
times only subtree selection; GraphML parsing and forest normalization happen
before the timed region, matching the manuscript protocol.
"""

from __future__ import print_function

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import sys
import time
from collections import deque

import networkx as nx


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import app  # noqa: E402


DATASETS = {
    "ACM topics": ("acm.graphml", "time_series", 30),
    "Chinese equities": ("equities.graphml", "d0", 20),
    "European regional GDP": ("european_regional_gdp.graphml", "time_series", 15),
}


def sha256_file(path):
    """Return the SHA-256 checksum of one binary input file."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def environment_record():
    """Record the runtime needed to interpret benchmark timings."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("Flask", "networkx", "numpy")
        },
    }


def input_records():
    """Record exactly which dataset bytes produced the tables."""
    records = {}
    for filename, _, _ in DATASETS.values():
        path = os.path.join(REPO_ROOT, "datasets", filename)
        records[filename] = {
            "bytes": os.path.getsize(path),
            "sha256": sha256_file(path),
        }
    return records


def parse_series(value):
    """Parse a GraphML time-series attribute into finite floats."""
    if isinstance(value, (list, tuple)):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            raw = json.loads(text)
        except ValueError:
            raw = [part for part in re.split(r"[\s,]+", text.strip("[]()")) if part]
    else:
        return None
    values = []
    for item in raw:
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values or None


def load_dataset(filename, series_key):
    """Load one bundled GraphML hierarchy and its normalized node tuples."""
    path = os.path.join(REPO_ROOT, "datasets", filename)
    source = nx.read_graphml(path)
    graph = nx.DiGraph()
    graph.add_nodes_from(source.nodes())
    graph.add_edges_from(source.edges())
    node_dict = {}
    for node, attrs in source.nodes(data=True):
        series = parse_series(attrs.get(series_key))
        if series is None:
            continue
        metadata = dict(attrs)
        metadata.pop(series_key, None)
        node_dict[node] = (series, float(sum(series)), metadata)
    graph = graph.subgraph(node_dict.keys()).copy()
    return source.number_of_nodes(), graph, node_dict


def breadth_first_prefix(graph, root, size):
    """Return a deterministic connected breadth-first prefix from ``root``."""
    if root not in graph:
        raise KeyError("root {!r} is absent from the hierarchy".format(root))
    queue = deque([root])
    seen = {root}
    order = []
    while queue and len(order) < size:
        node = queue.popleft()
        order.append(node)
        for child in sorted(graph.successors(node), key=str):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    if len(order) < size:
        raise ValueError("branch rooted at {!r} contains only {} nodes".format(root, len(order)))
    return graph.subgraph(order).copy(), order


def prepare_forest(graph, node_dict, method, similarity_setting):
    subset = {node: node_dict[node] for node in graph.nodes()}
    forest, dropped = app.normalize_hierarchy_to_forest(
        graph, subset, method=method, a=similarity_setting
    )
    return forest, subset, dropped


def timed_selection(graph, node_dict, strategy, method, k, similarity_setting,
                    warmups, repetitions):
    for _ in range(warmups):
        app.run_htsa_strategy(
            graph, node_dict, k, strategy=strategy, method=method, a=similarity_setting
        )
    durations = []
    result = None
    for _ in range(repetitions):
        started = time.perf_counter()
        canonical_strategy, canonical_method, result = app.run_htsa_strategy(
            graph, node_dict, k, strategy=strategy, method=method, a=similarity_setting
        )
        durations.append(time.perf_counter() - started)
    groups, objective = result
    selected = set()
    for nodes, _ in groups:
        selected.update(nodes)
    selected_importance = float(sum(node_dict[node][1] for node in selected))
    total_importance = float(sum(node_dict[node][1] for node in graph.nodes()))
    summary_edges, summary_root, _ = app.build_summary_tree(list(graph.edges()), result)
    displayed = {summary_root}
    for parent, child in summary_edges:
        displayed.add(parent)
        displayed.add(child)
    return {
        "strategy": canonical_strategy,
        "method": canonical_method,
        "median_seconds": statistics.median(durations),
        "objective": float(objective),
        "selected_nodes": len(selected),
        "selected_importance": selected_importance,
        "total_importance": total_importance,
        "importance_coverage_fraction": (
            selected_importance / total_importance
            if total_importance > 0 else None
        ),
        "displayed_nodes": len(displayed),
    }


def application_runs(warmups, repetitions):
    rows = []
    for application, (filename, series_key, k) in DATASETS.items():
        raw_nodes, graph, node_dict = load_dataset(filename, series_key)
        forest, subset, dropped = prepare_forest(graph, node_dict, "FDS", 0.9)
        lengths = [len(payload[0]) for payload in subset.values()]
        row = timed_selection(
            forest, subset, "Path-greedy", "FDS", k, 0.9, warmups, repetitions
        )
        row.update({
            "application": application,
            "raw_nodes": raw_nodes,
            "used_nodes": forest.number_of_nodes(),
            "edges": forest.number_of_edges(),
            "series_length_min": min(lengths),
            "series_length_max": max(lengths),
            "k": k,
            "dropped_multi_parent_edges": len(dropped),
        })
        rows.append(row)
    return rows


def strategy_runs(warmups, repetitions):
    _, graph, node_dict = load_dataset("acm.graphml", "time_series")
    rows = []
    for size in (20, 30, 40, 50):
        prefix, nodes = breadth_first_prefix(graph, "Networks", size)
        subset = {node: node_dict[node] for node in nodes}
        for strategy in ("Path-greedy", "Optimal-Search"):
            row = timed_selection(
                prefix, subset, strategy, "FDS", 5, 0.9, warmups, repetitions
            )
            row.update({"nodes": size, "k": 5})
            rows.append(row)
    return rows


def similarity_runs(warmups, repetitions):
    _, graph, node_dict = load_dataset("acm.graphml", "time_series")
    prefix, nodes = breadth_first_prefix(graph, "Computing_methodologies", 50)
    subset = {node: node_dict[node] for node in nodes}
    rows = []
    for method in ("FDS", "Euclidean", "znorm_euclidean", "DTW", "LCSS", "MSM"):
        row = timed_selection(
            prefix, subset, "Path-greedy", method, 5, 0.0, warmups, repetitions
        )
        row.update({"nodes": 50, "k": 5})
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick", action="store_true",
        help="use one untimed-free repetition per case for installation checks"
    )
    parser.add_argument(
        "--output", default=os.path.join(REPO_ROOT, "results", "reproduction.json"),
        help="JSON output path"
    )
    args = parser.parse_args()
    if args.quick:
        app_warmups = strategy_warmups = measure_warmups = 0
        app_repetitions = strategy_repetitions = measure_repetitions = 1
    else:
        app_warmups = strategy_warmups = measure_warmups = 1
        app_repetitions = strategy_repetitions = 7
        measure_repetitions = 3

    report = {
        "environment": environment_record(),
        "inputs": input_records(),
        "protocol": {
            "application_repetitions": app_repetitions,
            "strategy_repetitions": strategy_repetitions,
            "similarity_repetitions": measure_repetitions,
            "timed_region": "subtree selection only",
        },
        "application_runs": application_runs(app_warmups, app_repetitions),
        "strategy_runs": strategy_runs(strategy_warmups, strategy_repetitions),
        "similarity_runs": similarity_runs(measure_warmups, measure_repetitions),
    }
    output_dir = os.path.dirname(os.path.abspath(args.output))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("\nWrote {}".format(os.path.abspath(args.output)))


if __name__ == "__main__":
    main()
