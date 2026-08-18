"""
Unit tests cho Discovery Enhancement (Task 15).
Offline tests (mock HTTP) cho parsers + engine + merge.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from enhancer.parsers.common import (
    is_valid_endpoint, is_dynamic, guess_type, extract_from_js, dedup_ordered,
)
from enhancer.parsers.html_parser import HtmlParser
from enhancer.engine import DiscoveryEnhancer
from scanner.capability_analyzer import merge_enhanced_discovery


class TestCommon:
    """Test parser helpers."""

    def test_valid_endpoint(self):
        assert is_valid_endpoint("/api/v1/stocks") is True
        assert is_valid_endpoint("https://example.com/api") is True
        assert is_valid_endpoint("wss://example.com/ws") is True
        assert is_valid_endpoint("") is False
        assert is_valid_endpoint("mailto:a@b.com") is False
        assert is_valid_endpoint("/style.css") is False
        assert is_valid_endpoint("javascript:void(0)") is False
        assert is_valid_endpoint("node_modules/x") is False

    def test_dynamic(self):
        assert is_dynamic("/api/quote/${symbol}") is True
        assert is_dynamic("/api/quote/{symbol}") is True
        assert is_dynamic("/api/quote/FPT") is False

    def test_guess_type(self):
        assert guess_type("/graphql") == "graphql"
        assert guess_type("wss://x.com/ws") == "websocket"
        assert guess_type("/swagger.json") == "openapi"
        assert guess_type("/api/stocks") == "rest"

    def test_dedup_ordered(self):
        assert dedup_ordered(["a", "b", "a", "c"]) == ["a", "b", "c"]


class TestExtractFromJs:
    """Test trich endpoint tu JS text."""

    def test_fetch(self):
        js = 'fetch("/api/v1/stocks")'
        candidates = extract_from_js(js, "js_bundle", "https://x.com/bundle.js")
        assert len(candidates) == 1
        assert candidates[0]["url"] == "/api/v1/stocks"
        assert candidates[0]["found_in"] == "js_bundle"
        assert candidates[0]["type"] == "rest"

    def test_axios_post(self):
        js = 'axios.post("/api/orders", data)'
        candidates = extract_from_js(js, "inline_script", "https://x.com/")
        assert candidates[0]["method"] == "POST"

    def test_xhr(self):
        js = 'xhr.open("GET", "/api/quote/FPT")'
        candidates = extract_from_js(js, "js_bundle", "u")
        assert candidates[0]["url"] == "/api/quote/FPT"

    def test_websocket(self):
        js = 'new WebSocket("wss://example.com/ws")'
        candidates = extract_from_js(js, "inline_script", "u")
        assert candidates[0]["type"] == "websocket"
        assert candidates[0]["method"] is None

    def test_graphql(self):
        js = 'const url = "/graphql"'
        candidates = extract_from_js(js, "js_bundle", "u")
        assert any(c["type"] == "graphql" for c in candidates)

    def test_dynamic_template(self):
        js = 'fetch(`/api/quote/${symbol}`)'
        candidates = extract_from_js(js, "js_bundle", "u")
        assert candidates[0]["dynamic"] is True

    def test_asset_filtered(self):
        js = 'fetch("/style.css")'
        assert extract_from_js(js, "js_bundle", "u") == []

    def test_dedup(self):
        js = 'fetch("/api/x"); fetch("/api/x")'
        assert len(extract_from_js(js, "js_bundle", "u")) == 1


class TestHtmlParser:
    """Test HTML parser."""

    def test_extract_js_bundles(self):
        html = '<script src="/static/js/main.abc.js"></script><script>var x=1</script>'
        parser = HtmlParser("https://example.com/")
        result = parser.extract(html)
        assert result["js_bundles"] == ["https://example.com/static/js/main.abc.js"]
        assert len(result["inline_scripts"]) == 1

    def test_doc_links(self):
        html = '<link href="/swagger.json">'
        parser = HtmlParser("https://example.com/")
        assert parser.extract(html)["doc_links"] == ["https://example.com/swagger.json"]

    def test_page_links_asset_filtered(self):
        html = '<a href="/bang-gia">x</a><a href="/logo.png">y</a>'
        parser = HtmlParser("https://example.com/")
        links = parser.extract(html)["page_links"]
        assert "https://example.com/bang-gia" in links
        assert not any("logo.png" in l for l in links)


class TestEngine:
    """Test engine (mock HTTP)."""

    def test_enhance_source_mock(self, tmp_path):
        """Mock HTTP: home page co bundle + fetch endpoint."""
        enhancer = DiscoveryEnhancer(config={"source_maps": False})
        enhancer.output_dir = tmp_path / "output"
        enhancer.output_dir.mkdir(parents=True)

        home_html = (
            '<html><script src="/static/js/main.js"></script>'
            '<script>fetch("/api/v1/stocks")</script></html>'
        )
        bundle_js = 'axios.get("/api/quotes")'

        with patch.object(enhancer.http, "get_text", side_effect=[
            home_html, bundle_js,
        ]) as mock_get:
            result = enhancer._enhance_source(
                "hose", "https://example.com/", {}
            )

        urls = [c["url"] for c in result["endpoint_candidates"]]
        assert "/api/v1/stocks" in urls  # inline script
        assert "/api/quotes" in urls     # js bundle
        assert result["js_bundles_scanned"] == 1
        assert result["html_pages_scanned"] == 1
        assert mock_get.call_count == 2

    def test_enhance_no_js(self):
        """Khong co JS -> khong candidates."""
        enhancer = DiscoveryEnhancer()
        with patch.object(enhancer.http, "get_text", return_value="<html>plain</html>"):
            result = enhancer._enhance_source("hnx", "https://x.com/", {})
        assert result["endpoint_candidates"] == []
        assert result["errors"] == []

    def test_engine_run_skips_disabled(self, tmp_path):
        """enhancer.enabled=false -> khong chay."""
        enhancer = DiscoveryEnhancer(config={"enabled": False})
        assert enhancer.enabled is False


class TestMerge:
    """Test merge enhanced discovery vao discovery."""

    def test_merge_new_endpoints(self):
        discovery = {
            "hose": {
                "robots": {"url": "/robots.txt", "found": True},
                "api_tests": [{"url": "/api/v1/old"}],
            }
        }
        enhanced = {
            "sources": {
                "hose": {
                    "endpoint_candidates": [
                        {"url": "/api/v1/new", "evidence": 'fetch("/api/v1/new")',
                         "method": "GET", "type": "rest", "dynamic": False},
                        {"url": "/api/v1/old", "evidence": "x", "dynamic": False},
                        {"url": "/api/quote/${symbol}", "evidence": "y", "dynamic": True},
                    ]
                }
            }
        }
        merged = merge_enhanced_discovery(discovery, enhanced)

        api_tests = merged["hose"]["api_tests"]
        urls = [e["url"] for e in api_tests]
        # Chi them endpoint moi
        assert "/api/v1/new" in urls
        # Khong trung URL
        assert urls.count("/api/v1/old") == 1
        # Dynamic route bi loai
        assert "/api/quote/${symbol}" not in urls
        # Evidence giu nguyen
        new_entry = [e for e in api_tests if e["url"] == "/api/v1/new"][0]
        assert new_entry["evidence"] == 'fetch("/api/v1/new")'
        assert new_entry["found"] is True

    def test_merge_no_enhanced(self):
        discovery = {"hose": {"api_tests": []}}
        assert merge_enhanced_discovery(discovery, None) == discovery
        assert merge_enhanced_discovery(discovery, {"sources": {}}) == discovery

    def test_merge_new_source(self):
        """Enhanced co source khong co trong discovery -> tao moi."""
        discovery = {"hose": {}}
        enhanced = {
            "sources": {
                "hnx": {"endpoint_candidates": [
                    {"url": "/api/v1/x", "dynamic": False},
                ]}
            }
        }
        merged = merge_enhanced_discovery(discovery, enhanced)
        assert merged["hnx"]["api_tests"][0]["url"] == "/api/v1/x"
        # discovery goc khong doi
        assert merged["hose"] == {}
