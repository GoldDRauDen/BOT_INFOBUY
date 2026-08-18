"""
Unit tests cho capability_analyzer.
KHONG goi mang - chi test logic phan tich offline.
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scanner.capability_analyzer import CapabilityAnalyzer, run_capability_test


def _make_endpoint(url, found=True, status=200, content_type="application/json",
                   json_keys=None, response_sample="", html_title=None,
                   meta_description=None, h1=None, xml_root_tag=None,
                   redirect_url=None):
    """Tao mot endpoint entry giong discovery_report."""
    return {
        "url": url,
        "status": status,
        "found": found,
        "response_time_ms": 100.0,
        "retry": 0,
        "checked_at": "2026-08-02T12:00:00.000000",
        "content_type": content_type,
        "response_size_bytes": 100,
        "response_sample": response_sample,
        "redirect_url": redirect_url,
        "html_title": html_title,
        "meta_description": meta_description,
        "h1": h1,
        "json_keys": json_keys or [],
        "xml_root_tag": xml_root_tag,
    }


class TestCollectEndpoints:
    """Test gom endpoint tu discovery report."""

    def test_collect_all_types(self):
        analyzer = CapabilityAnalyzer()
        data = {
            "robots": _make_endpoint("/robots.txt", content_type="text/plain"),
            "api_tests": [
                _make_endpoint("/api", found=False, status=404),
                _make_endpoint("/api/v1/stocks", json_keys=["symbol", "price"]),
            ],
        }
        endpoints = analyzer._collect_endpoints(data)
        assert len(endpoints) == 3
        types = sorted(ep["_type"] for ep in endpoints)
        assert types == ["api_tests", "api_tests", "robots"]

    def test_collect_empty(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._collect_endpoints({}) == []
        assert analyzer._collect_endpoints(None) == []
        assert analyzer._collect_endpoints("not dict") == []


class TestMatching:
    """Test keyword matching."""

    def test_match_string_field(self):
        analyzer = CapabilityAnalyzer()
        # URL path: segment-prefix matching
        assert analyzer._match_keywords_in_field("/api/v1/stocks", ["stock"], is_path=True) == ["stock"]
        assert analyzer._match_keywords_in_field("/api/v1/stocks", ["price"], is_path=True) == []
        assert analyzer._match_keywords_in_field(None, ["stock"]) == []
        # Text: word boundary
        assert analyzer._match_keywords_in_field("Stock exchange", ["stock"]) == ["stock"]

    def test_match_list_field(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._match_keywords_in_field(["symbol", "price"], ["price"]) == ["price"]
        assert analyzer._match_keywords_in_field([], ["price"]) == []

    def test_word_boundary_no_false_positive(self):
        """'low' khong match trong 'Allow', 'pe' khong match trong 'price'."""
        analyzer = CapabilityAnalyzer()
        assert analyzer._match_keywords_in_field("User-agent: *\nDisallow:", ["low"]) == []
        assert analyzer._match_keywords_in_field("price", ["pe"]) == []
        assert analyzer._match_keywords_in_field("Allow", ["low"]) == []
        # Nhung van match khi keyword dung la tu doc lap
        assert analyzer._match_keywords_in_field("low price", ["low"]) == ["low"]
        assert analyzer._match_keywords_in_field("P/E ratio", ["p/e"]) == ["p/e"]

    def test_match_endpoint(self):
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/api/v1/stocks", json_keys=["symbol", "price"])
        match = analyzer._match_endpoint(ep, "stock_list", ["symbol", "volume"])
        assert match is not None
        assert match["matched_field"] == "json_keys"
        assert match["matched_keywords"] == ["symbol"]


class TestAnalyzeCapability:
    """Test decision rules."""

    def test_supported_json_keys(self):
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/api/v1/stocks", found=True, json_keys=["symbol", "price"]),
        ]
        result = analyzer._analyze_capability(endpoints, "stock_list", ["symbol", "ticker"])
        assert result["status"] == "supported"
        assert result["evidence"]["matched_field"] == "json_keys"
        assert result["evidence"]["matched_keywords"] == ["symbol"]

    def test_supported_url_match(self):
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/bang-gia", found=True, content_type="text/html",
                           html_title="Bang gia truc tuyen"),
        ]
        result = analyzer._analyze_capability(endpoints, "current_price", ["price", "gia"])
        assert result["status"] == "supported"
        assert result["evidence"]["matched_field"] in ("url", "html_title")

    def test_redirect_url_alone_not_supported(self):
        """Rule 5: redirect_url la evidence phu, khong du de ket luan supported."""
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/redirect", found=True, content_type="text/html",
                           redirect_url="https://example.com/bang-gia"),
        ]
        result = analyzer._analyze_capability(endpoints, "current_price", ["price", "gia"])
        assert result["status"] == "unknown"

    def test_redirect_url_with_content_evidence_supported(self):
        """Rule 5: redirect_url + content evidence khac -> supported."""
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/redirect", found=True, content_type="text/html",
                           redirect_url="https://example.com/bang-gia",
                           html_title="Bang gia truc tuyen"),
        ]
        result = analyzer._analyze_capability(endpoints, "current_price", ["price", "gia"])
        assert result["status"] == "supported"
        assert result["evidence"]["matched_field"] == "html_title"

    def test_unsupported_url_404(self):
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/api/v1/dividends", found=False, status=404,
                           content_type="text/html"),
        ]
        result = analyzer._analyze_capability(endpoints, "dividends", ["dividend"])
        assert result["status"] == "unsupported"
        assert result["evidence"]["http_status"] == 404

    def test_unknown_401(self):
        """401 = endpoint ton tai nhung can auth -> unknown (khong xac minh du lieu)."""
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/api/v1/stocks", found=True, status=401,
                           content_type="text/html"),
        ]
        result = analyzer._analyze_capability(endpoints, "stock_list", ["stock"])
        assert result["status"] == "unknown"
        assert result["evidence"] is None

    def test_unknown_catchall_no_content(self):
        """found=true (200) nhung khong co content match -> unknown."""
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/query", found=True, content_type="text/html",
                           html_title="Homepage", response_sample="<!DOCTYPE html>"),
        ]
        result = analyzer._analyze_capability(endpoints, "dividends", ["dividend"])
        assert result["status"] == "unknown"

    def test_unknown_no_endpoints(self):
        analyzer = CapabilityAnalyzer()
        result = analyzer._analyze_capability([], "dividends", ["dividend"])
        assert result["status"] == "unknown"
        assert result["evidence"] is None

    def test_supported_prefers_found_true(self):
        """Rule 8: neu co ca endpoint 404 va endpoint found=true, uu tien supported."""
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/api/v1/stocks", found=False, status=404),
            _make_endpoint("/api/stocks", found=True, json_keys=["symbol"]),
        ]
        result = analyzer._analyze_capability(endpoints, "stock_list", ["symbol"])
        assert result["status"] == "supported"

    def test_url_match_alone_not_supported(self):
        """Rule 2: URL path khong du lam bang chung duy nhat."""
        analyzer = CapabilityAnalyzer()
        endpoints = [
            _make_endpoint("/api/stocks", found=True, content_type="text/html"),
        ]
        result = analyzer._analyze_capability(endpoints, "stock_list", ["stock"])
        # URL match nhung khong co content evidence -> khong supported
        assert result["status"] == "unknown"


class TestRuleAClassification:
    """Rule A: phan loai endpoint."""

    def test_classify_json(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._classify_endpoint(_make_endpoint("/api", content_type="application/json")) == "json"

    def test_classify_xml(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._classify_endpoint(_make_endpoint("/sitemap.xml", content_type="application/xml")) == "xml"

    def test_classify_xhtml_as_html(self):
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/x", content_type="application/xhtml+xml")
        assert analyzer._classify_endpoint(ep) == "html"

    def test_classify_html(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._classify_endpoint(_make_endpoint("/", content_type="text/html")) == "html"

    def test_classify_error_page(self):
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/api", found=False, status=404, content_type="text/html")
        assert analyzer._classify_endpoint(ep) == "error_page"

    def test_classify_robots(self):
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/robots.txt", found=True, content_type="text/plain")
        ep["_type"] = "robots"
        assert analyzer._classify_endpoint(ep) == "robots"

    def test_classify_openapi(self):
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/openapi.json", found=True, content_type="application/json")
        ep["_type"] = "openapi"
        assert analyzer._classify_endpoint(ep) == "openapi"

    def test_robots_never_content_evidence(self):
        """Gap #7: robots.txt khong duoc match content."""
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/robots.txt", found=True, content_type="text/plain",
                            response_sample="Disallow: /price")
        ep["_type"] = "robots"
        match = analyzer._match_endpoint(ep, "current_price", ["price"])
        assert match is None


class TestRuleBNormalization:
    """Rule B: chuan hoa du lieu."""

    def test_normalize_html_entities(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._normalize("Gia &amp; KL") == "gia & kl"

    def test_normalize_unicode(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._normalize("TRANG CHỦ") == "trang chủ"

    def test_normalize_whitespace(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._normalize("Bang   gia\n truc") == "bang gia truc"

    def test_match_after_unicode_normalize(self):
        analyzer = CapabilityAnalyzer()
        # Sau normalize, keyword ASCII van match
        assert analyzer._match_keywords_in_field("bang gia", ["gia"]) == ["gia"]
        # HTML entities decode truoc khi match
        assert analyzer._match_keywords_in_field("Gia &amp; KL", ["kl"]) == ["kl"]
        assert analyzer._match_keywords_in_field("BANG GIA", ["gia"]) == ["gia"]


class TestRuleCBlacklist:
    """Rule C: blacklist."""

    def test_blacklist_home(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._is_blacklisted("Trang chủ - HOSE") is True

    def test_blacklist_404(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._is_blacklisted("404 Not Found") is True

    def test_not_blacklist_normal(self):
        analyzer = CapabilityAnalyzer()
        assert analyzer._is_blacklisted("Bang gia truc tuyen") is False

    def test_blacklisted_title_not_evidence(self):
        """Rule C: html_title blacklist khong tinh la content evidence."""
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/api/stocks", found=True, content_type="text/html",
                            html_title="404 Not Found", h1="404 Not Found")
        match = analyzer._match_endpoint(ep, "stock_list", ["stock", "symbol"])
        # URL match co the co, nhung khong phai content evidence
        assert match is None or match["matched_field"] not in analyzer.CONTENT_EVIDENCE_FIELDS


class TestRuleDStrongWeak:
    """Rule D: strong/weak keywords."""

    def test_strong_match_supported(self):
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/api/v1/stocks", found=True, json_keys=["symbol"])
        match = analyzer._match_endpoint(ep, "stock_list", ["symbol"])
        assert match is not None
        assert match["matched_field"] == "json_keys"

    def test_weak_match_requires_ok_type(self):
        """Weak keyword 'stock' trong html_title -> duoc tinh (html la nhom OK)."""
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/api/v1/stocks", found=True, content_type="text/html",
                            html_title="Stock exchange listing")
        match = analyzer._match_endpoint(ep, "stock_list", ["stock"])
        assert match is not None

    def test_weak_match_response_sample_html_home(self):
        """Weak keyword trong response_sample HTML chung chung van tinh (html OK)."""
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/", found=True, content_type="text/html",
                            response_sample="<html><title>Home</title>...")
        match = analyzer._match_endpoint(ep, "company_news", ["news"])
        # "news" weak trong sample cua trang home -> khong du manh
        assert match is None or match["matched_field"] == "url"

    def test_json_wrapper_keys_gap4(self):
        """Gap #4: json_keys chi wrapper -> match qua response_sample."""
        analyzer = CapabilityAnalyzer()
        ep = _make_endpoint("/api/data", found=True, json_keys=["data"],
                            response_sample='{"data": [{"symbol": "FPT"}]}')
        match = analyzer._match_endpoint(ep, "stock_list", ["symbol"])
        # "symbol" la strong keyword trong response_sample
        assert match is not None
        assert match["matched_field"] == "response_sample"


class TestAnalyzeSource:
    """Test phan tich ca source."""

    def test_all_16_capabilities(self):
        analyzer = CapabilityAnalyzer()
        data = {
            "robots": _make_endpoint("/robots.txt", content_type="text/plain"),
            "api_tests": [
                _make_endpoint("/api/v1/stocks", json_keys=["symbol"]),
            ],
        }
        caps = analyzer.analyze_source("hose", data)
        assert len(caps) == 16
        assert set(caps.keys()) == set(analyzer.CAPABILITIES.keys())
        for name, item in caps.items():
            assert item["status"] in ("supported", "unsupported", "unknown")
            assert "checked_at" in item

    def test_stock_list_supported(self):
        analyzer = CapabilityAnalyzer()
        data = {
            "api_tests": [_make_endpoint("/api/v1/stocks", json_keys=["symbol", "code"])],
        }
        caps = analyzer.analyze_source("hose", data)
        assert caps["stock_list"]["status"] == "supported"

    def test_unknown_capabilities(self):
        analyzer = CapabilityAnalyzer()
        data = {"robots": _make_endpoint("/robots.txt", content_type="text/plain")}
        caps = analyzer.analyze_source("hose", data)
        assert caps["eps"]["status"] == "unknown"
        assert caps["pb_ratio"]["status"] == "unknown"


class TestAnalyzeAll:
    """Test analyze_all."""

    def test_analyze_all_sources(self):
        analyzer = CapabilityAnalyzer()
        discovery = {
            "hose": {"api_tests": [_make_endpoint("/api/stocks", json_keys=["symbol"])]},
            "hnx": {"api_tests": [_make_endpoint("/api", found=False, status=404)]},
        }
        report = analyzer.analyze_all(discovery)
        assert "hose" in report
        assert "hnx" in report
        assert "generated_at" in report
        assert report["hose"]["stock_list"]["status"] == "supported"

    def test_analyze_all_empty(self):
        analyzer = CapabilityAnalyzer()
        report = analyzer.analyze_all({})
        assert "generated_at" in report
        assert len(report) == 1


class TestSaveReport:
    """Test luu report."""

    def test_save_report(self):
        analyzer = CapabilityAnalyzer()
        report = {"hose": {"eps": {"status": "unknown", "evidence": None}}}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name
        try:
            saved = analyzer.save_report(report, temp_path)
            assert Path(saved).exists()
            with open(temp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["hose"]["eps"]["status"] == "unknown"
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestRunCapabilityTest:
    """Test ham tien ich."""

    def test_run_with_missing_report(self, tmp_path):
        analyzer = CapabilityAnalyzer(base_dir=tmp_path)
        # Khong co discovery_report.json
        result = run_capability_test()
        # Khong crash, tra ve dict rong hoac co generated_at
        assert isinstance(result, dict)

    def test_run_with_real_files(self):
        """Chay voi discovery_report.json that trong output/ (offline)."""
        result = run_capability_test()
        assert isinstance(result, dict)
