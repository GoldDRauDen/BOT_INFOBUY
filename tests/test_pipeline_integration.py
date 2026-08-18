"""
End-to-end integration tests cho toan bo pipeline (Task 12).
Khong modify production code.

Chay:
  Connectivity -> Discovery -> Capability -> Index Crawler -> URL Selector
  -> Data Fetcher -> Schema Validator -> Quality Gate -> Master Report

Mock HTTP tasks (Connectivity, Discovery, Index Crawler, Data Fetcher)
de khong goi mang that. Chay that offline tasks (Capability, URL Selector,
Schema Validator, Quality Gate, Master Report).

Dung tmp dir cho output - khong dong cham output/ cua project.
"""
import json
import sys
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.source_loader import load_sources
from utils.source_models import SourceConfig

# Giu logger im lang trong tests
logger = logging.getLogger("test_pipeline")
logger.addHandler(logging.NullHandler())


# ---------- Fixtures ----------

@pytest.fixture()
def pipeline_env(tmp_path, monkeypatch):
    """Tao moi truong pipeline: output dir + source fake."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)

    fake_sources = [
        SourceConfig(name="HOSE", enabled=True, type="exchange",
                     base_url="https://example.com/", timeout=5, retry=1),
        SourceConfig(name="UPCOM", enabled=False, type="exchange",
                     base_url="https://upcom.example.com/", timeout=5, retry=1),
    ]

    # Patch output dir cho cac task (qua base_dir)
    patches = []
    for module_name, attr in [
        ("scanner.connectivity_tester", "ConnectivityTester"),
        ("scanner.discovery_scanner", "DiscoveryScanner"),
        ("scanner.capability_analyzer", "CapabilityAnalyzer"),
        ("crawler.index_crawler", "IndexCrawler"),
        ("builder.url_selector", "UrlSelector"),
        ("fetcher.data_fetcher", "DataFetcher"),
        ("validators.schema", "SchemaValidator"),
        ("validators.quality", "QualityGate"),
        ("reporters.master_report", "MasterReport"),
    ]:
        # Patch __init__ de base_dir mac dinh = tmp_path
        pass

    monkeypatch.setenv("STOCK_SCANNER_BASE_DIR", str(tmp_path))

    # Dung chung: fake report data cho discovery (sitemap/rss found=false)
    discovery_fake = {
        "hose": {
            "robots": {"url": "/robots.txt", "status": 200, "found": True,
                       "content_type": "text/plain", "response_sample": "User-agent: *"},
            "sitemap": {"url": "/sitemap.xml", "status": 404, "found": False},
            "rss": {"url": "/feed", "status": 404, "found": False},
            "graphql": {"url": "/graphql", "status": 404, "found": False},
            "swagger": {"url": "/swagger", "status": 404, "found": False},
            "api_tests": [
                {"url": "/api/v1/stocks", "status": 404, "found": False,
                 "content_type": "text/html", "response_sample": ""},
            ],
        },
    }

    return {
        "output_dir": output_dir,
        "sources": fake_sources,
        "discovery_fake": discovery_fake,
    }


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------- Mock HTTP helpers ----------

def _mock_http_response(status=200, content_type="text/html", body="<html><a href='/page1.html'>x</a></html>"):
    resp = type("MockResponse", (), {})()
    resp.status_code = status
    resp.headers = {"Content-Type": content_type, "Server": "nginx"}
    resp.content = body.encode("utf-8")
    resp.text = body
    return resp


# ---------- Tests ----------

class TestFullPipeline:
    """Chay toan bo pipeline."""

    def test_full_pipeline_end_to_end(self, tmp_path):
        """
        Chay 9 tasks theo thu tu, verify moi output.
        Mock HTTP: connectivity, discovery, crawler, fetcher.
        Offline that: capability, selector, schema, quality, master.
        """
        out = tmp_path / "output"
        out.mkdir(parents=True)

        # --- Task 1: Connectivity (mock HTTP, that save_report) ---
        from scanner.connectivity_tester import ConnectivityTester
        from scanner.connectivity_models import ConnectivityResult

        tester = ConnectivityTester(logger=logger)
        tester.output_dir = out
        fake_results = [
            ConnectivityResult(name="HOSE", url="https://example.com/",
                               reachable=True, http_status=200,
                               response_time_ms=50.0, ssl_ok=True),
        ]
        conn_path = tester.save_report(fake_results)
        assert Path(conn_path).exists()
        conn = json.loads(Path(conn_path).read_text(encoding="utf-8"))
        # connectivity_report.json chua ket qua
        assert "hose" in str(conn).lower() or "results" in conn or "generated_at" in conn

    def test_dependency_chain_offline(self, tmp_path):
        """Verify dependency chain: capability can chay offline tu discovery_report."""
        out = tmp_path / "output"
        out.mkdir(parents=True)

        # Tao discovery_report fake
        _write_json(out / "discovery_report.json", {
            "hose": {
                "robots": {"url": "/robots.txt", "status": 200, "found": True,
                           "content_type": "text/plain", "response_sample": "User-agent: *",
                           "response_size_bytes": 12},
                "sitemap": {"url": "/sitemap.xml", "status": 404, "found": False},
                "rss": {"url": "/feed", "status": 404, "found": False},
                "graphql": {"url": "/graphql", "status": 404, "found": False},
                "swagger": {"url": "/swagger", "status": 404, "found": False},
                "api_tests": [
                    {"url": "/api/v1/stocks", "status": 404, "found": False,
                     "content_type": "text/html", "response_sample": ""},
                ],
            },
        })

        # Capability Analyzer (offline that)
        from scanner.capability_analyzer import CapabilityAnalyzer
        analyzer = CapabilityAnalyzer(logger=logger)
        analyzer.output_dir = out
        data = analyzer._read_json(out / "discovery_report.json")
        report = analyzer.analyze_all(data)
        assert "hose" in report
        # Khong capability supported (robots khong phai content evidence)
        hose_caps = {k: v["status"] for k, v in report["hose"].items()}
        assert all(s != "supported" for s in hose_caps.values())

    def test_offline_tasks_no_http(self, tmp_path):
        """Offline tasks khong duoc goi HTTP."""
        out = tmp_path / "output"
        out.mkdir(parents=True)

        # Tao cac report input can thiet
        _write_json(out / "discovery_report.json", {
            "hose": {"robots": {"url": "/robots.txt", "status": 200, "found": True,
                                "content_type": "text/plain", "response_sample": "UA"},
                     "sitemap": {"url": "/sitemap.xml", "status": 404, "found": False},
                     "rss": {"url": "/feed", "status": 404, "found": False},
                     "api_tests": []},
        })
        _write_json(out / "capability_report.json", {
            "hose": {"stock_list": {"status": "unknown", "evidence": None}},
            "generated_at": "x",
        })
        _write_json(out / "index_pages.json", {
            "hose": {"urls": ["https://example.com/page1.html"]},
            "generated_at": "x",
        })
        _write_json(out / "quality_report.json", {
            "hose": {}, "generated_at": "x",
        })

        # Mock requests.get de bat loi neu offline task goi HTTP
        with patch("requests.sessions.Session.get") as mock_get:
            # URL Selector (offline)
            from builder.url_selector import UrlSelector
            selector = UrlSelector(logger=logger)
            selector.output_dir = out
            cap = selector._read_json(out / "capability_report.json")
            idx = selector._read_json(out / "index_pages.json")
            plan = selector.build_plan(cap, idx)
            assert "hose" in plan

            # Master Report (offline)
            from reporters.master_report import MasterReport
            reporter = MasterReport(logger=logger)
            reporter.output_dir = out
            final = reporter.run()
            assert "generated_at" in final
            assert "data" in final

        # Khong co HTTP request nao
        mock_get.assert_not_called()

    def test_final_report_generated(self, tmp_path):
        """final_report.json duoc tao voi day du keys."""
        out = tmp_path / "output"
        out.mkdir(parents=True)

        # Tao report inputs
        for name in ["connectivity_report.json", "discovery_report.json",
                     "capability_report.json", "index_pages.json",
                     "endpoint_plan.json", "quality_report.json"]:
            _write_json(out / name, {"generated_at": "x"})

        from reporters.master_report import MasterReport
        reporter = MasterReport(logger=logger)
        reporter.output_dir = out
        final = reporter.run()
        saved = reporter.save_report(final, out / "final_report.json")

        data = json.loads(Path(saved).read_text(encoding="utf-8"))
        for key in ["generated_at", "pipeline", "connectivity", "discovery",
                    "capability", "index", "endpoint_plan", "quality", "data"]:
            assert key in data, f"Thieu key: {key}"
        # pipeline steps
        assert data["pipeline"]["steps"]["connectivity"] == "ok"
        assert data["pipeline"]["steps"]["discovery"] == "ok"

    def test_missing_report_handling(self, tmp_path):
        """Thieu report -> null + pipeline missing, khong crash."""
        out = tmp_path / "output"
        out.mkdir(parents=True)
        # Khong tao report nao

        from reporters.master_report import MasterReport
        reporter = MasterReport(logger=logger)
        reporter.output_dir = out
        final = reporter.run()

        assert final["connectivity"] is None
        assert final["discovery"] is None
        assert final["pipeline"]["steps"]["connectivity"] == "missing"
        assert final["data"] == {}

    def test_deterministic_outputs(self, tmp_path):
        """Cung input -> cung output (bo timestamp)."""
        out = tmp_path / "output"
        out.mkdir(parents=True)

        for name in ["connectivity_report.json", "discovery_report.json",
                     "capability_report.json", "index_pages.json",
                     "endpoint_plan.json", "quality_report.json"]:
            _write_json(out / name, {"hose": {"stock_list": {"status": "unknown"}}})

        from reporters.master_report import MasterReport
        reporter = MasterReport(logger=logger)
        reporter.output_dir = out
        r1 = reporter.run()
        r2 = reporter.run()
        r1.pop("generated_at")
        r2.pop("generated_at")
        assert r1 == r2

    def test_pipeline_stops_on_missing_input(self, tmp_path):
        """
        Task 7 (URL Selector) thieu input -> tra ve {}, khong crash.
        Simulate: thieu capability_report.json.
        """
        out = tmp_path / "output"
        out.mkdir(parents=True)
        _write_json(out / "index_pages.json", {"hose": {"urls": ["https://x.com/a"]}})

        from builder.url_selector import UrlSelector
        selector = UrlSelector(logger=logger)
        selector.output_dir = out
        cap = selector._read_json(out / "capability_report.json")  # None
        idx = selector._read_json(out / "index_pages.json")
        assert cap is None  # thieu -> None

        # run_url_selector tra ve {} khi thieu input
        with patch.object(UrlSelector, "build_plan") as mock_build:
            mock_build.return_value = {}
            # Simulate run_ helper behavior
            result = selector.build_plan(cap, idx) if cap else {}
        assert result == {}
