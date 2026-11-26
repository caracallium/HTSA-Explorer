"""Offline safety and calculation checks for daily equity refreshes."""

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import networkx as nx

import app as app_module
from scripts import refresh_equities as refresh_module


class FixtureProvider:
    version = "fixture"

    def __init__(self, records=None):
        self.records = records or {
            "sh000001": [
                {"date": "2023-01-03", "open": 500},
                {"date": "2023-01-04", "open": 501},
                {"date": "2023-01-05", "open": 502},
            ],
            "sh600001": [
                {"date": "2023-01-03", "open": 10},
                {"date": "2023-01-04", "open": 11},
                {"date": "2023-01-05", "open": 12},
            ],
            "sz000002": [
                {"date": "2023-01-03", "open": 2},
                {"date": "2023-01-05", "open": 4},
            ],
        }
        self.calls = []

    def __call__(self, kind, symbol, start_date, as_of):
        self.calls.append((kind, symbol, start_date, as_of))
        result = self.records[symbol]
        if isinstance(result, Exception):
            raise result
        return result


def successful_runner(graph, series):
    return {"ok": True, "analysis_graph": {"vertices": len(graph)}, "fixture": True}


class EquityRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "live"
        self.sources = self.root / "sources.json"
        self.config = {
            "schema_version": 1,
            "start_date": "2023-01-01",
            "nodes": [
                {"id": "SSE_Composite_Index", "chinese_name": "上证指数", "kind": "index", "symbol": "sh000001", "scale": 2},
                {"id": "Industry", "chinese_name": "行业", "kind": "sum"},
                {"id": "Stock_A", "chinese_name": "甲", "kind": "stock", "symbol": "sh600001", "scale": 10},
                {"id": "Stock_B", "chinese_name": "乙", "kind": "stock", "symbol": "sz000002", "scale": 100},
            ],
            "edges": [["SSE_Composite_Index", "Industry"], ["Industry", "Stock_A"], ["Industry", "Stock_B"]],
        }
        self.write_sources()

    def write_sources(self):
        self.sources.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def refresh(self, provider=None, runner=successful_runner, as_of="2023-01-05", **kwargs):
        return refresh_module.refresh(
            self.sources, self.output, as_of=as_of, provider=provider or FixtureProvider(),
            runner=runner, progress=None, **kwargs,
        )

    def release_bytes(self):
        return {p.name: p.read_bytes() for p in self.output.iterdir()}

    def test_alignment_scales_group_sums_and_integrity_manifest(self):
        result = self.refresh()
        self.assertEqual(result["status"], "published")
        graph = nx.read_graphml(self.output / "equities.graphml")
        self.assertEqual(list(graph.edges()), [tuple(e) for e in self.config["edges"]])
        self.assertEqual(json.loads(graph.nodes["Stock_A"]["d0"]), [100, 110, 120])
        self.assertEqual(json.loads(graph.nodes["Stock_B"]["d0"]), [200, 200, 400])
        self.assertEqual(json.loads(graph.nodes["Industry"]["d0"]), [300, 310, 520])
        self.assertEqual(json.loads(graph.nodes["SSE_Composite_Index"]["d0"]), [1000, 1002, 1004])
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(manifest["start_date"], "2023-01-03")
        self.assertEqual(manifest["end_date"], "2023-01-05")
        self.assertEqual(manifest["observations"], 3)
        self.assertEqual(manifest["node_count"], 4)
        self.assertEqual(manifest["edge_count"], 3)
        details = {n["id"]: n for n in manifest["alignment"]["sources"]}
        self.assertEqual(details["Stock_B"]["forward_filled_sessions"], 1)
        self.assertEqual(details["Stock_B"]["actual_observed_end"], "2023-01-05")
        for record in (manifest["graphml"], manifest["run"]):
            self.assertEqual(hashlib.sha256((self.output / record["file"]).read_bytes()).hexdigest(), record["sha256"])
        with patch.object(app_module, "DATASET_DIR", str(self.root)):
            response = app_module.app.test_client().get("/api/dataset/stockgraph/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["release"], "live")

    def test_partial_provider_failure_never_replaces_published_release(self):
        self.refresh()
        before = self.release_bytes()
        provider = FixtureProvider()
        provider.records["sz000002"] = ValueError("missing source symbol")
        with self.assertRaisesRegex(ValueError, "quote refresh failed"):
            self.refresh(provider=provider)
        self.assertEqual(before, self.release_bytes())

    def test_failed_computation_never_replaces_published_release(self):
        self.refresh()
        before = self.release_bytes()
        provider = FixtureProvider()
        provider.records["sh600001"][-1]["open"] = 13
        with self.assertRaisesRegex(ValueError, "no successful result"):
            self.refresh(provider=provider, runner=lambda graph, series: {"ok": False})
        self.assertEqual(before, self.release_bytes())

    def test_noop_preserves_files_and_does_not_rerun_unchanged_data(self):
        self.refresh()
        before = self.release_bytes()
        calls = []
        result = self.refresh(as_of="2023-01-07", runner=lambda graph, series: calls.append(1))
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(calls, [])
        self.assertEqual(before, self.release_bytes())

    def test_insufficient_freshness_rejects_complete_but_stale_batch(self):
        provider = FixtureProvider()
        provider.records["sz000002"] = [{"date": "2023-01-03", "open": 2}]
        with self.assertRaisesRegex(ValueError, "at least 90% required"):
            self.refresh(provider=provider)
        self.assertFalse(self.output.exists())

    def test_changed_observation_provenance_is_published_even_if_prices_match(self):
        self.refresh()
        before = (self.output / "equities.graphml").read_bytes()
        provider = FixtureProvider()
        provider.records["sz000002"].insert(1, {"date": "2023-01-04", "open": 2})
        result = self.refresh(provider=provider)
        self.assertEqual(result["status"], "published")
        self.assertEqual((self.output / "equities.graphml").read_bytes(), before)
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        details = {n["id"]: n for n in manifest["alignment"]["sources"]}
        self.assertEqual(details["Stock_B"]["forward_filled_sessions"], 0)

    def test_leading_missing_price_is_never_invented(self):
        provider = FixtureProvider()
        provider.records["sz000002"] = [{"date": "2023-01-05", "open": 4}]
        with self.assertRaisesRegex(ValueError, "leading values are never fabricated"):
            self.refresh(provider=provider)
        self.assertFalse(self.output.exists())

    def test_zero_open_placeholders_are_filled_and_recorded(self):
        provider = FixtureProvider()
        provider.records["sz000002"].insert(1, {"date": "2023-01-04", "open": 0})
        self.refresh(provider=provider, cache_dir=self.root / "cache")
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        details = {n["id"]: n for n in manifest["alignment"]["sources"]}
        self.assertEqual(details["Stock_B"]["zero_open_sessions"], ["2023-01-04"])
        graph = nx.read_graphml(self.output / "equities.graphml")
        self.assertEqual(json.loads(graph.nodes["Stock_B"]["d0"]), [200, 200, 400])
        cached = json.loads((self.root / "cache/2023-01-05/sz000002.json").read_bytes())
        self.assertEqual(cached["zero_open_dates"], ["2023-01-04"])
        records = refresh_module.fetch_quotes("stock", "sz000002", "2023-01-01", "2023-01-05", None, self.root / "cache")
        self.assertEqual(records.zero_open_dates, ["2023-01-04"])

    def test_delisted_exclusion_skips_fetch_and_recomputes_parent(self):
        self.config["nodes"][-1].update({"status": "delisted", "delisted_on": "2023-01-04", "inactive_policy": "exclude"})
        self.write_sources()
        provider = FixtureProvider()
        provider.records["sz000002"] = ValueError("delisted source must not be fetched")
        self.refresh(provider=provider)
        self.assertNotIn("sz000002", [call[1] for call in provider.calls])
        graph = nx.read_graphml(self.output / "equities.graphml")
        self.assertNotIn("Stock_B", graph)
        self.assertEqual(json.loads(graph.nodes["Industry"]["d0"]), [100, 110, 120])
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(manifest["node_count"], 3)
        self.assertEqual(manifest["edge_count"], 2)
        self.assertEqual(manifest["excluded_nodes"][0]["id"], "Stock_B")

    def test_explicit_carry_forward_policy_retains_delisted_node(self):
        self.config["nodes"][-1].update({"status": "delisted", "delisted_on": "2023-01-04", "inactive_policy": "carry_forward"})
        active, excluded = refresh_module.active_configuration(self.config, "2023-01-05")
        self.assertEqual(len(active["nodes"]), 4)
        self.assertEqual(excluded, [])

    def test_invalid_prices_and_duplicate_dates_are_rejected(self):
        for records in (
            [{"date": "2023-01-03", "open": -1}],
            [{"date": "2023-01-03", "open": float("nan")}],
            [{"date": "2023-01-03", "open": 0}],
            [{"date": "2023-01-03", "open": 1}, {"date": "2023-01-03", "open": 2}],
        ):
            with self.subTest(records=records), self.assertRaises(ValueError):
                refresh_module.normalize_records(records, "2023-01-01", "2023-01-05")

    def test_same_date_cache_contract_and_bounded_transient_retry(self):
        provider = FixtureProvider()
        attempts = []

        def flaky(kind, symbol, start_date, as_of):
            attempts.append(1)
            if len(attempts) < 3:
                raise TimeoutError("transient timeout")
            return provider(kind, symbol, start_date, as_of)

        cache_dir = self.root / "cache"
        records = refresh_module.fetch_quotes("stock", "sh600001", "2023-01-01", "2023-01-05", flaky, cache_dir, sleep=lambda delay: None)
        self.assertEqual(len(attempts), 3)
        cached = json.loads((cache_dir / "2023-01-05/sh600001.json").read_bytes())
        self.assertEqual(cached["provider_function"], "stock_zh_a_hist_tx")
        self.assertEqual(cached["as_of"], "2023-01-05")
        loaded = refresh_module.fetch_quotes("stock", "sh600001", "2023-01-01", "2023-01-05", None, cache_dir)
        self.assertEqual(loaded, records)
        cached["as_of"] = "2023-01-04"
        (cache_dir / "2023-01-05/sh600001.json").write_text(json.dumps(cached), encoding="utf-8")
        refresh_module.fetch_quotes("stock", "sh600001", "2023-01-01", "2023-01-05", provider, cache_dir)
        self.assertEqual(len(provider.calls), 2)

    def test_default_cutoff_uses_completed_china_day(self):
        before = datetime(2026, 11, 26, 3, 3, tzinfo=timezone.utc)
        after = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(refresh_module.default_as_of(before), date(2026, 11, 25))
        self.assertEqual(refresh_module.default_as_of(after), date(2026, 11, 26))

    def test_actual_htsa_endpoint_runs_on_fresh_fixture(self):
        audit = self.root / "audit"
        audit.mkdir()
        with patch.object(app_module, "DATA_DIR", str(audit)):
            result = self.refresh(runner=refresh_module.run_existing_htsa)
        self.assertEqual(result["status"], "published")
        run = json.loads((self.output / "latest-run.json").read_bytes())
        self.assertTrue(run["ok"])
        self.assertEqual(run["method"], "FDS")
        self.assertEqual(run["strategy"], "Path-greedy")
        self.assertEqual(run["k"], 20)
        self.assertEqual(run["a"], 0.9)
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(manifest["htsa"], {
            "method": "FDS", "strategy": "Path-greedy", "k": 20, "a": 0.9,
            "optimal_overflow": "path-greedy",
        })
        self.assertEqual(run["analysis_graph"]["vertices"], 4)
        self.assertTrue(list(audit.glob("*.json")))

    def test_parameter_change_reruns_identical_data_and_updates_provenance(self):
        audit = self.root / "audit"
        audit.mkdir()
        with patch.object(app_module, "DATA_DIR", str(audit)):
            with patch.dict(refresh_module.DAILY_HTSA_PARAMETERS, {"a": 0.0}):
                self.refresh(runner=refresh_module.run_existing_htsa)
            old_graphml = (self.output / "equities.graphml").read_bytes()
            old_run = json.loads((self.output / "latest-run.json").read_bytes())
            self.assertEqual(old_run["a"], 0.0)
            result = self.refresh(runner=refresh_module.run_existing_htsa)
        self.assertEqual(result["status"], "published")
        self.assertEqual((self.output / "equities.graphml").read_bytes(), old_graphml)
        new_run = json.loads((self.output / "latest-run.json").read_bytes())
        new_manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(new_run["a"], 0.9)
        self.assertEqual(new_manifest["htsa"]["a"], 0.9)
        self.assertNotEqual(new_run["audit"]["run_id"], old_run["audit"]["run_id"])

    def test_calendar_start_baseline_and_source_freshness_guards(self):
        self.config["expected_first_session"] = "2023-01-03"
        self.config["baseline_sessions"] = ["2023-01-03", "2023-01-04", "2023-01-05"]
        self.write_sources()
        for remove_date, expected_error in (
            ("2023-01-03", "expected first session"),
            ("2023-01-04", "baseline sessions"),
        ):
            with self.subTest(remove_date=remove_date):
                provider = FixtureProvider()
                provider.records["sh000001"] = [r for r in provider.records["sh000001"] if r["date"] != remove_date]
                with self.assertRaisesRegex(ValueError, expected_error):
                    self.refresh(provider=provider)
                self.assertFalse(self.output.exists())
        self.config.pop("baseline_sessions")
        self.write_sources()
        provider = FixtureProvider()
        provider.records["sh000001"].pop()
        with self.assertRaisesRegex(ValueError, "SSE calendar is stale"):
            self.refresh(provider=provider)
        self.assertFalse(self.output.exists())

    def test_published_calendar_cannot_regress_or_lose_sessions(self):
        self.refresh()
        before = self.release_bytes()
        for scenario in ("regression", "missing-middle"):
            with self.subTest(scenario=scenario):
                provider = FixtureProvider()
                calls = []
                if scenario == "regression":
                    provider.records["sz000002"].insert(1, {"date": "2023-01-04", "open": 2})
                    as_of, expected_error = "2023-01-04", "end date backwards"
                else:
                    for records in provider.records.values():
                        records.append({"date": "2023-01-06", "open": records[-1]["open"] + 1})
                    provider.records["sh000001"] = [r for r in provider.records["sh000001"] if r["date"] != "2023-01-04"]
                    as_of, expected_error = "2023-01-06", "every previously published session"
                with self.assertRaisesRegex(ValueError, expected_error):
                    self.refresh(provider=provider, as_of=as_of, runner=lambda g, s: calls.append(1))
                self.assertEqual(calls, [])
                self.assertEqual(before, self.release_bytes())

    def test_failed_second_or_third_promotion_restores_all_previous_files(self):
        self.refresh()
        before = self.release_bytes()
        real_replace = os.replace
        for fail_on in (2, 3):
            with self.subTest(fail_on=fail_on):
                provider = FixtureProvider()
                provider.records["sh600001"][-1]["open"] = 13
                calls = []

                def fail_during_promotion(source, destination):
                    calls.append((source, destination))
                    if len(calls) == fail_on:
                        raise OSError("simulated promotion failure")
                    return real_replace(source, destination)

                with patch.object(refresh_module.os, "replace", side_effect=fail_during_promotion):
                    with self.assertRaisesRegex(OSError, "simulated promotion failure"):
                        self.refresh(provider=provider)
                self.assertEqual(before, self.release_bytes())
                self.assertEqual(list(self.root.glob(".equity-candidate-*")), [])

    def test_output_lock_rejects_concurrent_writer_and_releases_after_exit(self):
        with refresh_module.output_lock(self.output):
            with self.assertRaisesRegex(ValueError, "another equity refresh"):
                self.refresh()
        self.assertEqual(self.refresh()["status"], "published")


if __name__ == "__main__":
    unittest.main()
