"""Verify live snapshot integrity and the unchanged preset interface."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app as app_module


class LiveEquityRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.datasets = Path(self.temp_dir.name)
        self.frozen = b'<?xml version="1.0"?><graphml><graph id="frozen"/></graphml>'
        (self.datasets / "equities.graphml").write_bytes(self.frozen)
        self.dataset_patch = patch.object(app_module, "DATASET_DIR", str(self.datasets))
        self.dataset_patch.start()
        self.addCleanup(self.dataset_patch.stop)
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def write_live_release(self, end_date="2026-11-25", marker="latest"):
        live = self.datasets / "live"
        live.mkdir(exist_ok=True)
        graphml = ('<?xml version="1.0"?><graphml><graph id="' + marker
                   + '"/></graphml>').encode("utf-8")
        run = json.dumps({"ok": True, "method": "FDS", "k": 20}).encode("utf-8")
        manifest = {
            "schema_version": 1,
            "dataset": "stockgraph",
            "start_date": "2023-01-03",
            "end_date": end_date,
            "observations": 942,
            "node_count": 760,
            "edge_count": 758,
            "generated_at": "2026-11-25T10:30:00+00:00",
            "provider": {"library": "akshare", "version": "test-version"},
            "graphml": {
                "file": "equities.graphml",
                "bytes": len(graphml),
                "sha256": hashlib.sha256(graphml).hexdigest(),
            },
            "run": {
                "file": "latest-run.json",
                "sha256": hashlib.sha256(run).hexdigest(),
            },
        }
        (live / "equities.graphml").write_bytes(graphml)
        (live / "latest-run.json").write_bytes(run)
        self.write_manifest(manifest)
        return graphml, manifest

    def write_manifest(self, manifest):
        (self.datasets / "live" / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def assert_release_rejected(self):
        for url in ("/api/dataset/stockgraph", "/api/dataset/stockgraph/status"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 503, url)
            self.assertFalse(response.get_json()["ok"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertNotIn(self.temp_dir.name, response.get_data(as_text=True))

    def test_no_live_release_serves_explicit_frozen_snapshot(self):
        response = self.client.get("/api/dataset/stockgraph")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.frozen)
        self.assertEqual(response.headers["X-HTSA-Dataset-Release"], "frozen-2023")
        self.assertEqual(response.headers["X-HTSA-Dataset-Period"], "2023")
        self.assertNotIn("X-HTSA-Dataset-Start-Date", response.headers)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        status = self.client.get("/api/dataset/stockgraph/status").get_json()
        self.assertEqual(status["release"], "frozen-2023")
        self.assertEqual(status["period"], "2023")
        self.assertNotIn("start_date", status)
        self.assertNotIn("end_date", status)
        self.assertIsNone(status["provider"]["version"])

    def test_complete_live_release_preserves_preset_contract(self):
        graphml, manifest = self.write_live_release()
        manifest["private_notes"] = "must not be returned"
        manifest["provider"]["private_notes"] = "must not be returned"
        self.write_manifest(manifest)
        response = self.client.get("/api/dataset/stockgraph")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, graphml)
        self.assertEqual(response.mimetype, "application/xml")
        self.assertEqual(response.headers["Content-Disposition"],
                         'attachment; filename="equities.graphml"')
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-HTSA-Dataset-Source"], "akshare")
        self.assertEqual(response.headers["X-HTSA-Dataset-Start-Date"], "2023-01-03")
        self.assertEqual(response.headers["X-HTSA-Dataset-End-Date"], "2026-11-25")
        self.assertEqual(response.headers["X-HTSA-Dataset-SHA256"],
                         manifest["graphml"]["sha256"])
        status_response = self.client.get("/api/dataset/stockgraph/status")
        status = status_response.get_json()
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status["release"], "live")
        self.assertEqual(status["graphml"], manifest["graphml"])
        self.assertEqual(status["run"], manifest["run"])
        self.assertNotIn("private_notes", status)
        self.assertNotIn("private_notes", status["provider"])

    def test_next_request_reads_newly_published_release(self):
        old_graphml, _ = self.write_live_release(end_date="2026-11-24", marker="old")
        first = self.client.get("/api/dataset/stockgraph")
        self.assertEqual(first.data, old_graphml)
        new_graphml, _ = self.write_live_release(end_date="2026-11-25", marker="new")
        second = self.client.get("/api/dataset/stockgraph")
        self.assertEqual(second.data, new_graphml)
        self.assertEqual(second.headers["X-HTSA-Dataset-End-Date"], "2026-11-25")

    def test_partial_release_does_not_fall_back_to_old_snapshot(self):
        live = self.datasets / "live"
        live.mkdir()
        (live / "equities.graphml").write_bytes(b"incomplete")
        self.assert_release_rejected()

    def test_corrupt_graphml_or_run_is_rejected(self):
        for filename in ("equities.graphml", "latest-run.json"):
            with self.subTest(filename=filename):
                self.write_live_release()
                path = self.datasets / "live" / filename
                path.write_bytes(path.read_bytes() + b"corrupt")
                self.assert_release_rejected()

    def test_manifest_filenames_cannot_redirect_reads(self):
        for key in ("graphml", "run"):
            with self.subTest(key=key):
                _, manifest = self.write_live_release()
                manifest[key]["file"] = "../equities.graphml"
                self.write_manifest(manifest)
                self.assert_release_rejected()

    def test_invalid_manifest_metadata_is_rejected(self):
        changes = (
            ("schema_version", True),
            ("schema_version", 2),
            ("dataset", "acm"),
            ("end_date", "2026-02-30"),
            ("end_date", "2022-12-31"),
            ("observations", 0),
            ("generated_at", "unknown"),
            ("provider", {"library": "akshare", "version": None}),
        )
        for key, value in changes:
            with self.subTest(key=key, value=value):
                _, manifest = self.write_live_release()
                manifest[key] = value
                self.write_manifest(manifest)
                self.assert_release_rejected()

    def test_incorrect_byte_count_and_invalid_json_are_rejected(self):
        _, manifest = self.write_live_release()
        manifest["graphml"]["bytes"] += 1
        self.write_manifest(manifest)
        self.assert_release_rejected()
        (self.datasets / "live" / "manifest.json").write_text("{", encoding="utf-8")
        self.assert_release_rejected()

    def test_unknown_status_returns_not_found(self):
        self.assertEqual(self.client.get("/api/dataset/not-present/status").status_code, 404)

    def test_frontend_template_is_unchanged(self):
        template = Path(app_module.BASE_DIR) / "templates" / "appnewest.html"
        self.assertEqual(
            hashlib.sha256(template.read_bytes()).hexdigest(),
            "00b9ce5eb777906e11b69ee1fbefcd7600166b335be952ce4bb972ff59bb185c",
        )


if __name__ == "__main__":
    unittest.main()
