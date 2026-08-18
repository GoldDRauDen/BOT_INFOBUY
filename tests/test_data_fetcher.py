"""
Unit tests cho Data Fetcher (Task 8).
Khong goi mang that - dung mock.
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fetcher.data_fetcher import DataFetcher


def _make_response(status=200, content_type="application/json", body='{"symbol": "FPT"}'):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Content-Type": content_type, "Server": "nginx"}
    resp.content = body.encode("utf-8")
    return resp


class TestFetchOne:
    """Test fetch mot URL."""

    def test_fetch_success_stores_raw(self):
        fetcher = DataFetcher()
        resp = _make_response(body="raw body exact")
        with patch.object(fetcher, "_request_with_retry", return_value=resp):
            entry = fetcher._fetch_one("https://example.com/api", "hose", "stock_list")

        assert entry["source"] == "hose"
        assert entry["capability"] == "stock_list"
        assert entry["url"] == "https://example.com/api"
        assert entry["status"] == 200
        assert entry["content_type"] == "application/json"
        assert entry["headers"]["Server"] == "nginx"
        assert entry["body"] == "raw body exact"  # giu nguyen body
        assert entry["response_size_bytes"] == len("raw body exact".encode())
        assert "fetched_at" in entry
        assert "response_time_ms" in entry

    def test_fetch_failure_graceful(self):
        """HTTP error / request fail -> entry van co, status None, khong crash."""
        fetcher = DataFetcher()
        with patch.object(fetcher, "_request_with_retry", return_value=None):
            entry = fetcher._fetch_one("https://example.com/api", "hose", "x")

        assert entry["status"] is None
        assert entry["body"] is None
        assert entry["response_size_bytes"] == 0
        assert entry["headers"] == {}

    def test_http_error_status_stored(self):
        """Status 404 duoc luu, khong crash."""
        fetcher = DataFetcher()
        with patch.object(fetcher, "_request_with_retry", return_value=_make_response(status=404)):
            entry = fetcher._fetch_one("https://example.com/404", "hose", "x")

        assert entry["status"] == 404
        assert entry["body"] == '{"symbol": "FPT"}'


class TestFetchCapability:
    """Test fetch ca capability."""

    def test_max_urls_limited(self):
        fetcher = DataFetcher(max_urls_per_capability=2, request_delay=0)
        with patch.object(fetcher, "_request_with_retry", return_value=_make_response()):
            result = fetcher._fetch_capability(
                "hose", "stock_list",
                ["https://a.com/1", "https://a.com/2", "https://a.com/3"],
            )

        assert len(result["entries"]) == 2
        assert result["source"] == "hose"
        assert result["capability"] == "stock_list"

    def test_no_duplicate_fetches(self):
        """URL trung -> fetch 1 lan."""
        fetcher = DataFetcher(request_delay=0)
        with patch.object(fetcher, "_request_with_retry", return_value=_make_response()) as mock_fetch:
            result = fetcher._fetch_capability(
                "hose", "stock_list",
                ["https://a.com/1", "https://a.com/1", "https://a.com/1"],
            )

        assert len(result["entries"]) == 1
        assert mock_fetch.call_count == 1

    def test_preserve_fetch_order(self):
        fetcher = DataFetcher(request_delay=0)
        with patch.object(fetcher, "_request_with_retry", return_value=_make_response()):
            result = fetcher._fetch_capability(
                "hose", "stock_list",
                ["https://a.com/1", "https://a.com/2", "https://a.com/3"],
            )

        urls = [e["url"] for e in result["entries"]]
        assert urls == ["https://a.com/1", "https://a.com/2", "https://a.com/3"]

    def test_continue_on_failure(self):
        """URL 1 fail -> van fetch URL 2."""
        fetcher = DataFetcher(request_delay=0)
        responses = [None, _make_response()]
        with patch.object(fetcher, "_request_with_retry", side_effect=responses):
            result = fetcher._fetch_capability(
                "hose", "stock_list",
                ["https://a.com/1", "https://a.com/2"],
            )

        assert len(result["entries"]) == 2
        assert result["entries"][0]["status"] is None
        assert result["entries"][1]["status"] == 200


class TestRun:
    """Test run toan bo."""

    def test_run_plan(self):
        fetcher = DataFetcher(request_delay=0)
        plan = {
            "hose": {
                "stock_list": {"status": "planned", "url": "https://a.com/stocks", "reason": "capability_evidence"},
                "current_price": {"status": "planned", "url": "https://a.com/price", "reason": "first_available_index_page"},
            },
            "hnx": {},  # khong co plan entries -> skip
            "generated_at": "2026-01-01T00:00:00",
        }
        with patch.object(fetcher, "_request_with_retry", return_value=_make_response()):
            report = fetcher.run(plan)

        assert "hose" in report["sources"]
        assert "hnx" not in report["sources"]  # skip source khong entries
        assert "stock_list" in report["sources"]["hose"]["capabilities"]
        assert "current_price" in report["sources"]["hose"]["capabilities"]

    def test_run_empty_plan(self):
        fetcher = DataFetcher()
        report = fetcher.run({"generated_at": "x"})
        assert report["sources"] == {}

    def test_run_skips_missing_url(self):
        fetcher = DataFetcher(request_delay=0)
        plan = {"hose": {"cap": {"status": "planned"}}}  # thieu url
        with patch.object(fetcher, "_request_with_retry", return_value=_make_response()) as mock_fetch:
            report = fetcher.run(plan)
        assert mock_fetch.call_count == 0
        # Source co entry nhung thieu URL -> khong fetch, capabilities rong
        assert report["sources"]["hose"]["capabilities"] == {}


class TestSaveReport:
    """Test luu raw data."""

    def test_save_report_creates_files(self):
        fetcher = DataFetcher()
        report = {
            "sources": {
                "hose": {"capabilities": {
                    "stock_list": {"source": "hose", "capability": "stock_list", "entries": [{"url": "/api"}]},
                }},
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher.raw_dir = Path(tmpdir) / "raw_data"
            saved = fetcher.save_report(report)
            assert (Path(saved) / "hose" / "stock_list.json").exists()
            with open(Path(saved) / "hose" / "stock_list.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["capability"] == "stock_list"


class TestConfig:
    """Test configurable settings."""

    def test_defaults(self):
        fetcher = DataFetcher()
        assert fetcher.timeout == DataFetcher.DEFAULT_TIMEOUT
        assert fetcher.retries == DataFetcher.DEFAULT_RETRIES
        assert fetcher.request_delay == DataFetcher.DEFAULT_REQUEST_DELAY
        assert fetcher.max_urls_per_capability == DataFetcher.DEFAULT_MAX_URLS_PER_CAPABILITY

    def test_overrides(self):
        fetcher = DataFetcher(timeout=5, retries=1, request_delay=0.1, max_urls_per_capability=3)
        assert fetcher.timeout == 5
        assert fetcher.retries == 1
        assert fetcher.request_delay == 0.1
        assert fetcher.max_urls_per_capability == 3


class TestRetry:
    """Test retry policy."""

    def test_retry_on_timeout(self):
        fetcher = DataFetcher(retries=2)
        with patch.object(fetcher.session, "get", side_effect=requests.exceptions.Timeout()):
            assert fetcher._request_with_retry("https://example.com/") is None
        assert fetcher._last_retry_count == fetcher.retries

    def test_success_first_attempt(self):
        fetcher = DataFetcher(retries=2)
        resp = _make_response()
        with patch.object(fetcher.session, "get", return_value=resp):
            result = fetcher._request_with_retry("https://example.com/")
        assert result is resp
        assert fetcher._last_retry_count == 0

    def test_retry_then_success(self):
        """Fail 1 lan, thanh cong lan 2."""
        fetcher = DataFetcher(retries=2)
        resp = _make_response()
        with patch.object(fetcher.session, "get", side_effect=[requests.exceptions.Timeout(), resp]):
            result = fetcher._request_with_retry("https://example.com/")
        assert result is resp
        assert fetcher._last_retry_count == 1
