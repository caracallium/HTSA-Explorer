"""Refresh the equity preset through AKShare and run the existing HTSA service.

The paper's frozen dataset is never written. A complete candidate is checked
and computed before its three release files replace datasets/live/.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import networkx as nx


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SHANGHAI = timezone(timedelta(hours=8))
PROVIDER_FUNCTIONS = {
    "stock": "stock_zh_a_hist_tx",
    "index": "index_zh_daily_tx",
}
CALENDAR_NODE = "SSE_Composite_Index"
DAILY_HTSA_PARAMETERS = {
    "method": "FDS", "strategy": "Path-greedy", "k": 20, "a": 0.9,
    "optimal_overflow": "path-greedy",
}


def default_as_of(now=None):
    """Use yesterday until the daily post-close cutoff at 18:00 Shanghai."""
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    return current.date() - timedelta(days=int(current.hour < 18))


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"invalid date: {value!r}")
    return date.fromisoformat(value)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def load_sources(path):
    raw = Path(path).read_bytes()
    config = json.loads(raw)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("unsupported equity source configuration")
    _date(config.get("start_date"))
    if "expected_first_session" in config:
        _date(config["expected_first_session"])
    if "baseline_sessions" in config:
        baseline = config["baseline_sessions"]
        if (not isinstance(baseline, list) or not baseline
                or [_date(d).isoformat() for d in baseline] != sorted(set(baseline))):
            raise ValueError("baseline sessions must be nonempty, unique, sorted ISO dates")
        if config.get("expected_first_session", baseline[0]) != baseline[0]:
            raise ValueError("baseline sessions disagree with the expected first session")
    nodes = config.get("nodes")
    edges = config.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        raise ValueError("source configuration requires nodes and edges")
    by_id = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise ValueError("each source node requires a string identifier")
        if node["id"] in by_id:
            raise ValueError(f"duplicate source node: {node['id']}")
        if node.get("kind") not in ("stock", "index", "sum"):
            raise ValueError(f"unknown node kind: {node['id']}")
        if node["kind"] != "sum":
            if not isinstance(node.get("symbol"), str) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", node["symbol"]):
                raise ValueError(f"invalid market symbol: {node['id']}")
            scale = node.get("scale")
            if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
                raise ValueError(f"invalid fixed scale: {node['id']}")
        if node.get("status") == "delisted":
            _date(node.get("delisted_on"))
            if node.get("inactive_policy", "exclude") not in ("exclude", "carry_forward"):
                raise ValueError(f"invalid inactive policy: {node['id']}")
        by_id[node["id"]] = node
    graph = nx.DiGraph()
    graph.add_nodes_from(by_id)
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2 or any(n not in by_id for n in edge):
            raise ValueError("invalid source hierarchy edge")
        pair = tuple(edge)
        if pair in seen_edges:
            raise ValueError("duplicate source hierarchy edge")
        seen_edges.add(pair)
        graph.add_edge(*edge)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("source hierarchy must be acyclic")
    if by_id.get(CALENDAR_NODE, {}).get("kind") != "index":
        raise ValueError("SSE_Composite_Index is required as the trading calendar")
    if not any(n["kind"] == "stock" for n in nodes):
        raise ValueError("source hierarchy has no stocks")
    for node in nodes:
        if node["kind"] == "sum" and not graph.out_degree(node["id"]):
            raise ValueError(f"sum node has no children: {node['id']}")
    return config, _sha256(raw)


def active_configuration(config, as_of):
    excluded = []
    for node in config["nodes"]:
        if (node.get("status") == "delisted" and _date(as_of) >= _date(node["delisted_on"])
                and node.get("inactive_policy", "exclude") == "exclude"):
            excluded.append({
                "id": node["id"], "symbol": node["symbol"],
                "reason": "delisted; configured policy excludes inactive securities",
                "delisted_on": node["delisted_on"],
            })
    excluded_ids = {n["id"] for n in excluded}
    active = {
        **config,
        "nodes": [n for n in config["nodes"] if n["id"] not in excluded_ids],
        "edges": [e for e in config["edges"] if not excluded_ids.intersection(e)],
    }
    return active, excluded


class QuoteRecords(list):
    """Validated observations plus explicitly missing zero-open placeholders."""

    def __init__(self, records, zero_open_dates=()):
        super().__init__(records)
        self.zero_open_dates = sorted(set(zero_open_dates))


def normalize_records(records, start_date, as_of):
    """Retain valid unadjusted opening observations in the requested window."""
    if hasattr(records, "to_dict"):
        records = records.to_dict(orient="records")
    if not isinstance(records, list) or not records:
        raise ValueError("provider returned no opening-price records")
    start_date, as_of = _date(start_date), _date(as_of)
    clean, seen, zeros = {}, set(), []
    for record in records:
        if not isinstance(record, dict) or "date" not in record or "open" not in record:
            raise ValueError("provider records require date and open fields")
        observed = _date(record["date"])
        raw_open = record["open"]
        if isinstance(raw_open, bool):
            raise ValueError("invalid opening price")
        price = float(raw_open)
        if not math.isfinite(price) or price < 0:
            raise ValueError("opening prices must be finite and nonnegative")
        if not start_date <= observed <= as_of:
            continue
        key = observed.isoformat()
        if key in seen:
            raise ValueError(f"duplicate provider date: {key}")
        seen.add(key)
        if price == 0:
            zeros.append(key)
            continue
        clean[key] = price
    if not clean:
        raise ValueError("provider returned no records in the requested date window")
    return QuoteRecords([{"date": d, "open": clean[d]} for d in sorted(clean)], zeros)


class AKShareProvider:
    def __init__(self, timeout=30):
        import akshare as ak
        self.ak = ak
        self.version = importlib.metadata.version("akshare")
        ak.set_timeout(timeout)

    def __call__(self, kind, symbol, start_date, as_of):
        kwargs = {
            "symbol": symbol,
            "start_date": start_date.replace("-", ""),
            "end_date": as_of.replace("-", ""),
        }
        if kind == "stock":
            return self.ak.stock_zh_a_hist_tx(**kwargs, adjust="")
        return self.ak.index_zh_daily_tx(**kwargs)


def _is_transient(exc):
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in {
        "Timeout", "ReadTimeout", "ConnectTimeout", "ConnectionError", "ProxyError",
        "ChunkedEncodingError", "NetworkError", "RateLimitError",
    }:
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {429, 500, 502, 503, 504}


def _atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch_quotes(kind, symbol, start_date, as_of, provider, cache_dir, attempts=3, sleep=time.sleep):
    """Use same-date raw quotes, or fetch through the named AKShare interface."""
    cache_path = Path(cache_dir) / as_of / f"{symbol}.json" if cache_dir else None
    expected = {
        "symbol": symbol, "kind": kind, "as_of": as_of,
        "provider_function": PROVIDER_FUNCTIONS[kind],
    }
    if cache_path and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_bytes())
            if all(cached.get(k) == v for k, v in expected.items()):
                records = normalize_records(cached.get("records"), start_date, as_of)
                recorded_zeros = cached.get("zero_open_dates", [])
                if not isinstance(recorded_zeros, list):
                    raise ValueError("invalid cached zero-open dates")
                zeros = [_date(d).isoformat() for d in recorded_zeros]
                return QuoteRecords(records, records.zero_open_dates + zeros)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    for attempt in range(attempts):
        try:
            raw = provider(kind, symbol, start_date, as_of)
            break
        except Exception as exc:
            if not _is_transient(exc) or attempt + 1 == attempts:
                raise
            sleep(min(2 ** attempt, 4))
    records = normalize_records(raw, start_date, as_of)
    if cache_path:
        _atomic_write(cache_path, _json_bytes({
            **expected, "records": records, "zero_open_dates": records.zero_open_dates,
        }))
    return records


def fetch_all(config, as_of, provider, cache_dir, workers=3, progress=print):
    if workers < 1 or workers > 3:
        raise ValueError("workers must be between 1 and 3")
    sources = sorted({(n["kind"], n["symbol"]) for n in config["nodes"] if n["kind"] != "sum"})
    quotes = {}
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {
            pool.submit(fetch_quotes, kind, symbol, config["start_date"], as_of,
                        provider, cache_dir): (kind, symbol)
            for kind, symbol in sources
        }
        for completed, task in enumerate(as_completed(tasks), 1):
            source = tasks[task]
            try:
                quotes[source] = task.result()
            except Exception as exc:
                failures.append({"kind": source[0], "symbol": source[1], "error": str(exc)})
            if progress and (completed % 25 == 0 or completed == len(sources)):
                progress(f"equity quotes: {completed}/{len(sources)}, failed={len(failures)}")
    if failures:
        raise ValueError("quote refresh failed: " + json.dumps(failures, ensure_ascii=False))
    return quotes


def build_graph(config, quotes, as_of):
    """Align on SSE sessions, scale observations, and sum configured children."""
    nodes = {n["id"]: n for n in config["nodes"]}
    calendar_source = nodes[CALENDAR_NODE]
    calendar_records = quotes[("index", calendar_source["symbol"])]
    calendar = [r["date"] for r in calendar_records]
    if not calendar:
        raise ValueError("the SSE trading calendar is empty")
    if config.get("expected_first_session", calendar[0]) != calendar[0]:
        raise ValueError("the SSE calendar is truncated before the expected first session")
    baseline = config.get("baseline_sessions", [])
    if calendar[:len(baseline)] != baseline:
        raise ValueError("the SSE calendar does not preserve the configured baseline sessions")
    end_date = calendar[-1]
    for (kind, symbol), records in quotes.items():
        if records[-1]["date"] > end_date:
            raise ValueError(f"the SSE calendar is stale: {kind} {symbol} has observations after {end_date}")
    stock_sources = {(n["kind"], n["symbol"]) for n in nodes.values() if n["kind"] == "stock"}
    if not stock_sources:
        raise ValueError("the active hierarchy has no stocks")
    fresh = sum(any(r["date"] == end_date for r in quotes[s]) for s in stock_sources)
    if fresh / len(stock_sources) < 0.90:
        raise ValueError(f"only {fresh}/{len(stock_sources)} stocks have an observed bar on {end_date}; at least 90% required")

    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from(config["edges"])
    series = {}
    source_details = []
    for node_id, node in nodes.items():
        if node["kind"] == "sum":
            continue
        records = quotes[(node["kind"], node["symbol"])]
        position, previous, filled = 0, None, 0
        values = []
        for session in calendar:
            observed_today = False
            while position < len(records) and records[position]["date"] <= session:
                previous = records[position]["open"]
                observed_today = records[position]["date"] == session
                position += 1
            if previous is None:
                raise ValueError(f"{node_id} has no observed price at the calendar start {calendar[0]}; leading values are never fabricated")
            if not observed_today:
                filled += 1
            value = previous * node["scale"]
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid scaled series for {node_id}")
            values.append(value)
        series[node_id] = values
        actual_end = records[-1]["date"]
        source_details.append({
            "id": node_id, "kind": node["kind"], "symbol": node["symbol"],
            "provider_function": PROVIDER_FUNCTIONS[node["kind"]],
            "actual_observed_start": records[0]["date"],
            "actual_observed_end": actual_end,
            "forward_filled_sessions": filled,
            "zero_open_sessions": [d for d in getattr(records, "zero_open_dates", []) if d in calendar],
            "observed_on_calendar_end": any(r["date"] == end_date for r in records),
        })
    for node_id in reversed(list(nx.topological_sort(graph))):
        if nodes[node_id]["kind"] == "sum":
            children = list(graph.successors(node_id))
            if not children:
                raise ValueError(f"sum node {node_id} has no active children")
            series[node_id] = [math.fsum(series[c][i] for c in children) for i in range(len(calendar))]
            if not all(math.isfinite(v) and v > 0 for v in series[node_id]):
                raise ValueError(f"invalid aggregated series for {node_id}")
    graph.graph.update({
        "start_date": calendar[0], "end_date": end_date,
        "observation_dates": json.dumps(calendar, separators=(",", ":")),
        "price_field": "open", "stock_adjustment": "unadjusted",
        "alignment": "SSE trading sessions; forward fill after first observation",
    })
    for node_id, node in nodes.items():
        attrs = {
            "d0": json.dumps(series[node_id], separators=(",", ":"), allow_nan=False),
            "kind": node["kind"],
            "chinese_name": node.get("chinese_name", ""),
        }
        if node["kind"] != "sum":
            attrs.update({"symbol": node["symbol"], "scale": float(node["scale"])})
        graph.nodes[node_id].update(attrs)
    output = io.BytesIO()
    nx.write_graphml(graph, output, encoding="utf-8", named_key_ids=True)
    metadata = {
        "start_date": calendar[0], "end_date": end_date, "observations": len(calendar),
        "node_count": graph.number_of_nodes(), "edge_count": graph.number_of_edges(),
        "as_of": as_of, "price_field": "open", "stock_adjustment": "unadjusted",
        "calendar_source": CALENDAR_NODE,
        "stock_freshness": {"observed_on_calendar_end": fresh, "total_symbols": len(stock_sources), "required_fraction": 0.90},
        "alignment": {"policy": "forward fill after first observation; no leading fill", "sources": source_details},
        "stale_sources": [s for s in source_details if not s["observed_on_calendar_end"]],
    }
    return output.getvalue(), graph, series, metadata


def run_existing_htsa(graph, series):
    """Exercise the unchanged service endpoint, including its validation/audit."""
    import app
    payload = {
        "filename": "equities.graphml", "ts_key": "d0",
        "htsa": dict(DAILY_HTSA_PARAMETERS),
        "node_dict": {node: {"time_series": values} for node, values in series.items()},
        "edges": [list(edge) for edge in graph.edges()],
    }
    with app.app.test_client() as client:
        response = client.post("/api/htsa", json=payload)
        result = response.get_json()
    if response.status_code != 200 or not isinstance(result, dict) or not result.get("ok"):
        raise ValueError(f"daily HTSA computation failed: {result}")
    return result


def _release_matches(output_dir, graphml, metadata, config_sha256):
    try:
        old = json.loads((output_dir / "manifest.json").read_bytes())
        return (
            old.get("source_config_sha256") == config_sha256
            and old.get("htsa") == DAILY_HTSA_PARAMETERS
            and old.get("end_date") == metadata["end_date"]
            and old.get("start_date") == metadata["start_date"]
            and all(old.get(key) == metadata.get(key) for key in (
                "observations", "node_count", "edge_count", "stock_freshness",
                "alignment", "stale_sources", "excluded_nodes",
            ))
            and old["graphml"]["sha256"] == _sha256(graphml)
            and (output_dir / "equities.graphml").read_bytes() == graphml
            and old["run"]["sha256"] == _sha256((output_dir / "latest-run.json").read_bytes())
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


@contextmanager
def output_lock(output_dir):
    """Prevent concurrent local writers; the OS releases the lock on exit."""
    identity = os.path.normcase(str(Path(output_dir).resolve()))
    lock_dir = REPO_ROOT / "runtime_data" / "equity-refresh-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / (_sha256(identity.encode("utf-8")) + ".lock")
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("another equity refresh is using this output directory") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def check_calendar_history(output_dir, calendar):
    """Reject regressions or missing sessions before spending time on HTSA."""
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_bytes())
        if _date(calendar[-1]) < _date(previous["end_date"]):
            raise ValueError("the refreshed calendar would move the live end date backwards")
    graph_path = output_dir / "equities.graphml"
    if graph_path.is_file():
        previous_graph = nx.read_graphml(graph_path)
        previous_calendar = json.loads(previous_graph.graph["observation_dates"])
        if (not isinstance(previous_calendar, list) or not previous_calendar
                or previous_calendar != sorted(set(previous_calendar))
                or calendar[:len(previous_calendar)] != previous_calendar):
            raise ValueError("the refreshed calendar must preserve every previously published session")


def promote_release(output_dir, files):
    """Stage complete files and restore the previous set if promotion fails."""
    names = ("equities.graphml", "latest-run.json", "manifest.json")
    if set(files) != set(names):
        raise ValueError("a release requires exactly the three known output files")
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=".equity-candidate-", dir=output_dir.parent))
    preserve_backup = False
    previous = {}
    try:
        for name in names:
            (candidate / name).write_bytes(files[name])
            path = output_dir / name
            previous[name] = path.read_bytes() if path.exists() else None
            if previous[name] is not None:
                (candidate / ("old-" + name)).write_bytes(previous[name])
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            for name in names:
                os.replace(candidate / name, output_dir / name)
        except BaseException:
            restoration_errors = []
            for name in names:
                try:
                    if previous[name] is None:
                        (output_dir / name).unlink(missing_ok=True)
                    else:
                        # Direct restoration also works when replace itself is
                        # the failing filesystem operation. Backups remain until
                        # every previous file has been restored successfully.
                        (output_dir / name).write_bytes(previous[name])
                except OSError as exc:
                    restoration_errors.append(str(exc))
            if restoration_errors:
                preserve_backup = True
                raise RuntimeError(f"release rollback failed; backups retained at {candidate}: {restoration_errors}")
            raise
    finally:
        if not preserve_backup:
            for name in names:
                (candidate / name).unlink(missing_ok=True)
                (candidate / ("old-" + name)).unlink(missing_ok=True)
            candidate.rmdir()


def _refresh_locked(sources, output_dir, as_of=None, cache_dir=None, workers=3,
                    provider=None, runner=run_existing_htsa, progress=print):
    config, config_sha256 = load_sources(sources)
    as_of = _date(as_of or default_as_of()).isoformat()
    if _date(config["start_date"]) > _date(as_of):
        raise ValueError("as-of date precedes the configured start date")
    config, excluded = active_configuration(config, as_of)
    if not any(node["id"] == CALENDAR_NODE for node in config["nodes"]):
        raise ValueError("the calendar index cannot be excluded")
    provider = provider or AKShareProvider()
    quotes = fetch_all(config, as_of, provider, cache_dir, workers, progress)
    graphml, graph, series, metadata = build_graph(config, quotes, as_of)
    metadata["excluded_nodes"] = excluded
    output_dir = Path(output_dir)
    check_calendar_history(output_dir, json.loads(graph.graph["observation_dates"]))
    if _release_matches(output_dir, graphml, metadata, config_sha256):
        return {"status": "unchanged", "as_of": as_of, "end_date": metadata["end_date"], "sha256": _sha256(graphml)}
    result = runner(graph, series)
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError("daily HTSA runner returned no successful result")
    run = _json_bytes(result)
    manifest = {
        "schema_version": 1, "dataset": "stockgraph", **metadata,
        "htsa": dict(DAILY_HTSA_PARAMETERS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": {"library": "akshare", "version": str(provider.version)},
        "source_config_sha256": config_sha256,
        "graphml": {"file": "equities.graphml", "bytes": len(graphml), "sha256": _sha256(graphml)},
        "run": {"file": "latest-run.json", "sha256": _sha256(run)},
    }
    promote_release(output_dir, {
        "equities.graphml": graphml, "latest-run.json": run,
        "manifest.json": _json_bytes(manifest),
    })
    return {
        "status": "published", "as_of": as_of, "end_date": metadata["end_date"],
        "observations": metadata["observations"], "node_count": metadata["node_count"],
        "sha256": manifest["graphml"]["sha256"], "stale_sources": len(metadata["stale_sources"]),
    }


def refresh(sources, output_dir, as_of=None, cache_dir=None, workers=3,
            provider=None, runner=run_existing_htsa, progress=print):
    with output_lock(output_dir):
        return _refresh_locked(sources, output_dir, as_of, cache_dir, workers,
                               provider, runner, progress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=_date, default=None)
    parser.add_argument("--sources", type=Path, default=REPO_ROOT / "datasets" / "equity_sources.json")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "datasets" / "live")
    parser.add_argument("--cache-dir", type=Path, default=REPO_ROOT / "runtime_data" / "equity-cache")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    try:
        result = refresh(args.sources, args.output_dir, args.as_of, args.cache_dir, args.workers,
                         progress=lambda message: print(message, flush=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
