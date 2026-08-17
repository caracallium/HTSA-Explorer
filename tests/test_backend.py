import math
import tempfile
import unittest
from unittest.mock import patch

import networkx as nx

import app as app_module


def sample_payload(strategy="Path-greedy", method="FDS", k=1):
    return {
        "filename": "unit.graphml",
        "ts_key": "time_series",
        "htsa": {
            "strategy": strategy,
            "method": method,
            "a": 0.25,
            "k": k,
        },
        "node_dict": {
            "root": {"time_series": [1, 2, 3], "value": 3},
            "child": {"time_series": [1, 2, 4], "value": 2},
        },
        "edges": [["root", "child"]],
        "G": {
            "nodes": ["root", "child"],
            "edges": [["root", "child"]],
        },
    }


class StrategyDispatchTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def _post_with_mocks(self, strategy, dispatcher_name):
        result = [({"root", "child"}, 2.5)], 2.5
        summary = ([["S_root", "S_child"]], "S_root", ["root"])
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(app_module, "DATA_DIR", temp_dir), patch.object(
                app_module, dispatcher_name, return_value=result
            ) as dispatcher, patch.object(
                app_module, "build_summary_tree", return_value=summary
            ):
                response = self.client.post(
                    "/api/htsa", json=sample_payload(strategy=strategy)
                )
        return response, dispatcher

    def test_health_and_browser_routes_are_available(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"HTSA-Explorer", home.data)
        self.assertIn(b"Regional GDP", home.data)
        self.assertIn(
            b"acm.graphml', tsKey:'time_series', k:30", home.data
        )
        self.assertIn(
            b"equities.graphml', tsKey:'d0', k:20", home.data
        )
        self.assertIn(
            b"european_regional_gdp.graphml', tsKey:'time_series', k:15",
            home.data,
        )
        self.assertIn(b"filter(token => token.length)", home.data)

        d3_bundle = self.client.get("/static/vendor/d3.v7.9.0.min.js")
        self.assertEqual(d3_bundle.status_code, 200)
        self.assertIn(b"d3js.org v7.9.0", d3_bundle.data[:100])
        d3_bundle.close()

    def test_all_bundled_datasets_are_served(self):
        for name in ("acm", "stockgraph", "regional-gdp"):
            with self.subTest(name=name):
                response = self.client.get("/api/dataset/{}".format(name))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"<graphml", response.data.lower())

    def test_unknown_dataset_is_rejected(self):
        response = self.client.get("/api/dataset/not-present")
        self.assertEqual(response.status_code, 404)

    def test_path_greedy_dispatches_to_ppgreedy(self):
        response, dispatcher = self._post_with_mocks(
            "Path-greedy", "PPGreedy"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["strategy"], "Path-greedy")
        self.assertEqual(data["method"], "FDS")
        self.assertEqual(data["coverage"]["selected_vertices"], 2)
        self.assertEqual(data["coverage"]["total_vertices"], 2)
        self.assertEqual(data["coverage"]["selected_importance"], 5.0)
        self.assertEqual(data["coverage"]["total_importance"], 5.0)
        self.assertEqual(data["coverage"]["importance_fraction"], 1.0)
        self.assertEqual(data["analysis_graph"], {"vertices": 2, "edges": 1})
        dispatcher.assert_called_once()
        self.assertEqual(dispatcher.call_args[1]["method"], "FDS")
        self.assertEqual(dispatcher.call_args[1]["a"], 0.25)

    def test_optimal_search_dispatches_to_oss(self):
        response, dispatcher = self._post_with_mocks(
            "Optimal-Search", "ossoss"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["strategy"], "Optimal-Search")
        dispatcher.assert_called_once()
        self.assertEqual(dispatcher.call_args[1]["method"], "FDS")

    def test_unknown_strategy_is_rejected(self):
        response = self.client.post(
            "/api/htsa", json=sample_payload(strategy="not-an-algorithm")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown strategy", response.get_json()["error"])

    def test_non_positive_k_is_rejected(self):
        response = self.client.post(
            "/api/htsa", json=sample_payload(k=0)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("positive integer", response.get_json()["error"])

    def test_both_public_strategies_complete_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app_module, "DATA_DIR", temp_dir
        ):
            for strategy in ("Path-greedy", "Optimal-Search"):
                with self.subTest(strategy=strategy):
                    response = self.client.post(
                        "/api/htsa", json=sample_payload(strategy=strategy)
                    )
                    self.assertEqual(response.status_code, 200)
                    data = response.get_json()
                    self.assertTrue(data["ok"])
                    self.assertEqual(data["strategy"], strategy)
                    self.assertGreaterEqual(len(data["subgraphs"]), 1)

    def test_optimal_search_rejects_oversized_request(self):
        graph = nx.path_graph(
            app_module.MAX_OPTIMAL_SEARCH_NODES + 1,
            create_using=nx.DiGraph(),
        )
        node_dict = {
            node: ([1, 2, 3], 1.0, {}) for node in graph.nodes
        }
        with self.assertRaisesRegex(ValueError, "limited to"):
            app_module.run_htsa_strategy(
                graph,
                node_dict,
                1,
                strategy="Optimal-Search",
                method="FDS",
            )


class SimilarityPropagationTests(unittest.TestCase):
    def setUp(self):
        self.graph = nx.DiGraph([("root", "child")])
        self.node_dict = {
            "root": ([1, 2, 3], 3.0, {}),
            "child": ([1, 2, 4], 2.0, {}),
        }

    def test_each_subtree_algorithm_accepts_method_and_alpha(self):
        for algorithm in (app_module.VGreedy, app_module.PGreedy, app_module.oss):
            with self.subTest(algorithm=algorithm.__name__):
                nodes, score = algorithm(
                    self.graph,
                    self.node_dict,
                    "root",
                    method="Euclidean",
                    a=0.1,
                )
                self.assertIn("root", nodes)
                self.assertTrue(math.isfinite(score))

    def test_all_ui_similarity_methods_are_implemented(self):
        methods = ["FDS", "Euclidean", "znorm_euclidean", "DTW", "LCSS", "MSM"]
        for method in methods:
            with self.subTest(method=method):
                value = app_module.sim([1, 2, 3], [1, 2, 4], method, a=0.1)
                self.assertTrue(math.isfinite(value))

    def test_multi_parent_node_keeps_most_similar_parent(self):
        graph = nx.DiGraph([
            ("similar_parent", "child"),
            ("different_parent", "child"),
        ])
        node_dict = {
            "similar_parent": ([1, 2, 3], 1.0, {}),
            "different_parent": ([9, 1, 9], 1.0, {}),
            "child": ([1, 2, 3], 1.0, {}),
        }
        forest, dropped = app_module.normalize_hierarchy_to_forest(
            graph, node_dict, method="Euclidean"
        )
        self.assertTrue(forest.has_edge("similar_parent", "child"))
        self.assertFalse(forest.has_edge("different_parent", "child"))
        self.assertEqual(dropped, [("different_parent", "child")])


if __name__ == "__main__":
    unittest.main()
