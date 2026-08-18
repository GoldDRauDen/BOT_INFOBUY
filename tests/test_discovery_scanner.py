"""
Unit tests cho discovery_scanner.
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scanner.discovery_models import DiscoveryResult
from scanner.discovery_scanner import DiscoveryScanner
from utils.source_models import SourceConfig


class TestDiscoveryResult:
    """Test DiscoveryResult model."""

    def test_create_empty_result(self):
        result = DiscoveryResult(name="TEST", url="https://test.com")
        assert result.name == "TEST"
        assert result.url == "https://test.com"
        assert result.robots is None
        assert result.sitemap is None
        assert result.rss is None
        assert result.favicon is None
        assert result.graphql is None
        assert result.swagger is None
        assert result.openapi is None
        assert result.possible_api == []
        assert result.error is None

    def test_to_dict(self):
        result = DiscoveryResult(
            name="TEST",
            url="https://test.com",
            robots=True,
            sitemap=True,
            possible_api=["/api/v1"],
        )
        d = result.to_dict()
        assert d["name"] == "TEST"
        assert d["robots"] is True
        assert d["sitemap"] is True
        assert d["possible_api"] == ["/api/v1"]

    def test_summary(self):
        result = DiscoveryResult(
            name="TEST",
            url="https://test.com",
            robots=True,
            rss=False,
            possible_api=["/api/v1"],
        )
        summary = result.summary
        assert summary["resources"]["robots"] is True
        assert summary["resources"]["rss"] is False
        assert summary["possible_api"] == ["/api/v1"]

    def test_to_dict_includes_all_fields(self):
        result = DiscoveryResult(
            name="TEST",
            url="https://test.com",
            robots=True,
            sitemap=False,
            rss=True,
            favicon=True,
            graphql=False,
            swagger=True,
            openapi=False,
            possible_api=["/api", "/api/v2"],
            error="some error",
        )
        d = result.to_dict()
        assert d["robots"] is True
        assert d["sitemap"] is False
        assert d["rss"] is True
        assert d["favicon"] is True
        assert d["graphql"] is False
        assert d["swagger"] is True
        assert d["openapi"] is False
        assert d["possible_api"] == ["/api", "/api/v2"]
        assert d["error"] == "some error"


class TestDiscoveryScanner:
    """Test DiscoveryScanner."""

    def test_init_default_values(self):
        scanner = DiscoveryScanner()
        assert scanner.timeout == 10
        assert scanner.max_retries == 3

    def test_init_custom_values(self):
        scanner = DiscoveryScanner(timeout=15, max_retries=5)
        assert scanner.timeout == 15
        assert scanner.max_retries == 5

    def test_init_zero_values(self):
        """Ensure timeout=0 and max_retries=0 are respected, not replaced by defaults."""
        scanner = DiscoveryScanner(timeout=0, max_retries=0)
        assert scanner.timeout == 0
        assert scanner.max_retries == 0

    def test_build_url(self):
        scanner = DiscoveryScanner()

        # Without trailing slash
        url = scanner._build_url("https://example.com", "/api")
        assert url == "https://example.com/api"

        # With trailing slash
        url = scanner._build_url("https://example.com/", "/api")
        assert url == "https://example.com/api"

        # Path without leading slash
        url = scanner._build_url("https://example.com", "api")
        assert url == "https://example.com/api"

    def test_build_url_with_subpath(self):
        """_build_url strips leading slash, so path is always relative to base_url."""
        scanner = DiscoveryScanner()
        # Leading slash is stripped, so it becomes relative to base
        url = scanner._build_url("https://example.com/base/", "/robots.txt")
        assert url == "https://example.com/base/robots.txt"

        # No leading slash - same behavior
        url = scanner._build_url("https://example.com/base/", "robots.txt")
        assert url == "https://example.com/base/robots.txt"

    def test_check_endpoint_200(self):
        """Test endpoint tra ve 200 = ton tai (no catch-all)."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/robots.txt")
        assert res["found"] is True
        assert res["status"] == 200

    def test_check_endpoint_401(self):
        """Test endpoint tra ve 401 = ton tai."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 401
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/api")
        assert res["found"] is True
        assert res["status"] == 401

    def test_check_endpoint_403(self):
        """Test endpoint tra ve 403 = ton tai."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 403
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/admin")
        assert res["found"] is True
        assert res["status"] == 403

    def test_check_endpoint_405(self):
        """Test endpoint tra ve 405 = ton tai."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 405
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/api")
        assert res["found"] is True
        assert res["status"] == 405

    def test_check_endpoint_404(self):
        """Test endpoint tra ve 404 = khong ton tai."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 404
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/not-exist")
        assert res["found"] is False
        assert res["status"] == 404

    def test_check_endpoint_500(self):
        """Test endpoint tra ve 500 = khong ghi nhan ton tai."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 500
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/error")
        assert res["found"] is False
        assert res["status"] == 500

    def test_check_endpoint_no_response(self):
        """Test khi khong co response (connection failed)."""
        scanner = DiscoveryScanner()
        scanner._request_with_retry = MagicMock(return_value=None)

        res = scanner._check_endpoint("https://example.com/api")
        assert res["found"] is False
        assert res["status"] is None

    def test_check_endpoint_with_catchall_valid_content(self):
        """Test endpoint 200 voi catch-all, content-type hop le."""
        scanner = DiscoveryScanner()

        # HEAD response = 200
        head_response = MagicMock()
        head_response.status_code = 200

        # GET response = 200 with correct content-type and body
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.headers = {"Content-Type": "text/plain; charset=utf-8"}
        get_response.text = "User-agent: *\nDisallow: /admin"

        def mock_request(url, method="HEAD", verify=True):
            if method.upper() == "HEAD":
                return head_response
            return get_response

        scanner._request_with_retry = mock_request

        res = scanner._check_endpoint(
            "https://example.com/robots.txt",
            resource_type="robots",
            has_catchall=True,
        )
        assert res["found"] is True
        assert res["status"] == 200

    def test_check_endpoint_with_catchall_invalid_content(self):
        """Test endpoint 200 voi catch-all, content-type khong hop le (false positive)."""
        scanner = DiscoveryScanner()

        head_response = MagicMock()
        head_response.status_code = 200

        # GET response = 200 but content-type is HTML (not text/plain for robots.txt)
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.headers = {"Content-Type": "text/html; charset=utf-8"}

        def mock_request(url, method="HEAD", verify=True):
            if method.upper() == "HEAD":
                return head_response
            return get_response

        scanner._request_with_retry = mock_request

        res = scanner._check_endpoint(
            "https://example.com/robots.txt",
            resource_type="robots",
            has_catchall=True,
        )
        assert res["found"] is False
        assert res["status"] == 200

    def test_check_endpoint_catchall_401_still_valid(self):
        """Test endpoint 401 voi catch-all van ghi nhan ton tai (khong can validate content)."""
        scanner = DiscoveryScanner()

        head_response = MagicMock()
        head_response.status_code = 401
        scanner._request_with_retry = MagicMock(return_value=head_response)

        res = scanner._check_endpoint(
            "https://example.com/api",
            resource_type="api",
            has_catchall=True,
        )
        assert res["found"] is True
        assert res["status"] == 401

    def test_request_with_retry_timeout(self):
        """Test retry khi timeout."""
        from requests.exceptions import Timeout

        scanner = DiscoveryScanner(max_retries=3, timeout=1)
        scanner.RETRY_BACKOFF = 0  # Speed up test

        scanner.session.head = MagicMock(side_effect=Timeout("timed out"))

        result = scanner._request_with_retry("https://example.com/api")
        assert result is None
        assert scanner.session.head.call_count == 3

    def test_request_with_retry_connection_error(self):
        """Test retry khi connection error."""
        from requests.exceptions import ConnectionError as ReqConnectionError

        scanner = DiscoveryScanner(max_retries=2, timeout=1)
        scanner.RETRY_BACKOFF = 0

        scanner.session.head = MagicMock(
            side_effect=ReqConnectionError("connection refused")
        )

        result = scanner._request_with_retry("https://example.com/api")
        assert result is None
        assert scanner.session.head.call_count == 2

    def test_request_with_retry_ssl_fallback(self):
        """Test SSL error triggers verify=False retry."""
        from requests.exceptions import SSLError

        scanner = DiscoveryScanner(max_retries=3, timeout=1)
        scanner.RETRY_BACKOFF = 0

        call_count = 0
        verify_values = []

        original_head = scanner.session.head

        def mock_head(url, **kwargs):
            nonlocal call_count
            call_count += 1
            verify_values.append(kwargs.get("verify", True))
            if kwargs.get("verify", True):
                raise SSLError("SSL cert verify failed")
            # After SSL fallback, succeed
            resp = MagicMock()
            resp.status_code = 200
            return resp

        scanner.session.head = mock_head

        result = scanner._request_with_retry("https://example.com/api")
        assert result is not None
        assert result.status_code == 200
        # First call with verify=True (fails), second with verify=False (succeeds)
        assert verify_values[0] is True
        assert verify_values[1] is False

    def test_request_with_retry_success_first_try(self):
        """Test thanh cong ngay lan dau."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        scanner.session.head = MagicMock(return_value=mock_response)

        result = scanner._request_with_retry("https://example.com/api")
        assert result is not None
        assert result.status_code == 200
        assert scanner.session.head.call_count == 1

    def test_detect_catchall_true(self):
        """Test detect catch-all khi canary path tra ve 200."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._detect_catchall("https://example.com")
        assert result is True
        assert scanner._catchall_cache["https://example.com"] is True

    def test_detect_catchall_false(self):
        """Test detect khong phai catch-all khi canary path tra ve 404."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 404
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._detect_catchall("https://example.com")
        assert result is False
        assert scanner._catchall_cache["https://example.com"] is False

    def test_detect_catchall_cached(self):
        """Test detect catch-all su dung cache."""
        scanner = DiscoveryScanner()
        scanner._catchall_cache["https://cached.com"] = True
        scanner._request_with_retry = MagicMock()

        result = scanner._detect_catchall("https://cached.com")
        assert result is True
        # Should not have made any request
        scanner._request_with_retry.assert_not_called()

    def test_detect_catchall_no_response(self):
        """Test detect catch-all khi khong co response."""
        scanner = DiscoveryScanner()
        scanner._request_with_retry = MagicMock(return_value=None)

        result = scanner._detect_catchall("https://down.com")
        assert result is False

    def test_check_multiple_endpoints_first_match(self):
        """Test kiem tra nhieu endpoint, lay dau tien ton tai."""
        scanner = DiscoveryScanner()
        scanner.REQUEST_DELAY = 0

        with patch.object(scanner, "_check_single_endpoint") as mock_check:
            mock_check.side_effect = [
                {"url": "/api", "status": 200, "found": True, "response_time_ms": 10.0, "retry": 0, "checked_at": "2026-08-02"},
                {"url": "/api/v1", "status": 404, "found": False, "response_time_ms": 10.0, "retry": 0, "checked_at": "2026-08-02"},
            ]

            res = scanner._check_multiple_endpoints(
                "https://example.com", ["/api", "/api/v1"]
            )

            assert res["found"] is True
            assert res["status"] == 200
            assert res["url"] == "/api"

    def test_check_multiple_endpoints_no_match(self):
        """Test khong co endpoint nao ton tai."""
        scanner = DiscoveryScanner()
        scanner.REQUEST_DELAY = 0

        with patch.object(scanner, "_check_single_endpoint") as mock_check:
            mock_check.return_value = {"url": "/api/v1", "status": 404, "found": False, "response_time_ms": 10.0, "retry": 0, "checked_at": "2026-08-02"}

            res = scanner._check_multiple_endpoints(
                "https://example.com", ["/api", "/api/v1"]
            )

            assert res["found"] is False
            assert res["status"] == 404
            assert res["url"] == "/api/v1"

    def test_scan_source_success(self):
        """Test kham pha nguon thanh cong."""
        scanner = DiscoveryScanner()
        scanner.REQUEST_DELAY = 0

        # Mock _request_with_retry for initial connectivity + catchall check
        initial_response = MagicMock()
        initial_response.status_code = 200

        catchall_response = MagicMock()
        catchall_response.status_code = 404  # No catch-all

        def mock_check_single(base_url, path, resource_type="", has_catchall=False):
            url = path
            if path == "/robots.txt":
                return {"url": url, "status": 200, "found": True, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}
            if path == "/sitemap.xml":
                return {"url": url, "status": 200, "found": True, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}
            if path == "/favicon.ico":
                return {"url": url, "status": 404, "found": False, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}
            if path == "/api/v1":
                return {"url": url, "status": 200, "found": True, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}
            return {"url": url, "status": 404, "found": False, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}

        scanner._check_single_endpoint = mock_check_single

        def mock_check_multiple(base_url, paths, resource_type="", has_catchall=False):
            url = paths[0]
            if "/graphql" in paths:
                return {"url": url, "status": 200, "found": True, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}
            return {"url": url, "status": 404, "found": False, "response_time_ms": 1.0, "retry": 0, "checked_at": "2026-08-02"}

        scanner._check_multiple_endpoints = mock_check_multiple

        request_call_count = 0

        def mock_request(url, method="HEAD", verify=True):
            nonlocal request_call_count
            request_call_count += 1
            resp = MagicMock()
            if "canary" in url.lower() or "nonexistent" in url.lower():
                resp.status_code = 404
            else:
                resp.status_code = 200
            return resp

        scanner._request_with_retry = mock_request
        scanner._detect_catchall = MagicMock(return_value=False)

        source = SourceConfig(
            name="TEST",
            enabled=True,
            type="official",
            base_url="https://test.com",
        )

        result = scanner.scan_source(source)

        assert result.name == "TEST"
        assert result.robots["found"] is True
        assert result.sitemap["found"] is True
        assert result.favicon["found"] is False
        assert result.graphql["found"] is True
        assert result.swagger["found"] is False
        assert "/api/v1" in result.possible_api
        assert result.error is None

    def test_scan_source_connection_failure(self):
        """Test khi khong ket noi duoc den server."""
        scanner = DiscoveryScanner()
        scanner._request_with_retry = MagicMock(return_value=None)

        source = SourceConfig(
            name="DEAD",
            enabled=True,
            type="official",
            base_url="https://dead-server.com",
        )

        result = scanner.scan_source(source)
        assert result.name == "DEAD"
        assert result.error is not None
        assert "Khong the ket noi" in result.error

    def test_scan_source_exception(self):
        """Test khi co exception trong qua trinh scan."""
        scanner = DiscoveryScanner()

        def mock_request(*args, **kwargs):
            raise RuntimeError("Unexpected crash")

        scanner._request_with_retry = mock_request

        source = SourceConfig(
            name="CRASH",
            enabled=True,
            type="official",
            base_url="https://crash.com",
        )

        result = scanner.scan_source(source)
        assert result.name == "CRASH"
        assert result.error is not None

    def test_scan_all_continues_on_error(self):
        """Test khong crash khi mot nguon loi."""
        scanner = DiscoveryScanner()

        def mock_scan(source):
            if source.name == "FAIL":
                raise Exception("Simulated error")
            return DiscoveryResult(name=source.name, url=source.base_url, robots=True)

        scanner.scan_source = mock_scan

        sources = [
            SourceConfig(
                name="OK", enabled=True, type="official", base_url="https://ok.com"
            ),
            SourceConfig(
                name="FAIL",
                enabled=True,
                type="official",
                base_url="https://fail.com",
            ),
            SourceConfig(
                name="OK2",
                enabled=True,
                type="official",
                base_url="https://ok2.com",
            ),
        ]

        results = scanner.scan_all(sources)

        assert len(results) == 3
        assert results[0].name == "OK"
        assert results[0].robots is True
        assert results[1].name == "FAIL"
        assert results[1].error is not None
        assert results[2].name == "OK2"

    def test_validate_endpoint_content_robots(self):
        """Test validate robots.txt voi content-type text/plain."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.text = "User-agent: *\nDisallow: /admin"
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._validate_endpoint_content(
            "https://example.com/robots.txt", "robots"
        )
        assert result is True

    def test_validate_endpoint_content_robots_html_false(self):
        """Test validate robots.txt voi content-type text/html (false positive)."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html><body>404 Not Found</body></html>"
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._validate_endpoint_content(
            "https://example.com/robots.txt", "robots"
        )
        assert result is False

    def test_validate_endpoint_content_sitemap_xml(self):
        """Test validate sitemap.xml voi content-type XML."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/xml"}
        mock_response.text = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._validate_endpoint_content(
            "https://example.com/sitemap.xml", "sitemap"
        )
        assert result is True

    def test_validate_endpoint_content_favicon_image(self):
        """Test validate favicon voi content-type image."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/x-icon"}
        mock_response.text = b""
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._validate_endpoint_content(
            "https://example.com/favicon.ico", "favicon"
        )
        assert result is True

    def test_validate_endpoint_content_api_json(self):
        """Test validate API endpoint voi content-type JSON."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"status": "ok"}'
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._validate_endpoint_content(
            "https://example.com/api", "api"
        )
        assert result is True

    def test_validate_endpoint_content_401(self):
        """Test validate: 401 = ton tai bat ke content-type."""
        scanner = DiscoveryScanner()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {"Content-Type": "text/html"}
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        result = scanner._validate_endpoint_content(
            "https://example.com/api", "api"
        )
        assert result is True

    def test_validate_endpoint_content_no_response(self):
        """Test validate khi khong co response."""
        scanner = DiscoveryScanner()
        scanner._request_with_retry = MagicMock(return_value=None)

        result = scanner._validate_endpoint_content(
            "https://example.com/api", "api"
        )
        assert result is False

    def test_ssl_verify_cache(self):
        """Test SSL verify cache hoat dong dung."""
        scanner = DiscoveryScanner()
        assert scanner._get_verify("https://example.com") is True

        scanner._ssl_verify_cache["https://example.com"] = False
        assert scanner._get_verify("https://example.com") is False

    # ============ Metadata tests (Task 4 Revision) ============

    def _make_mock_response(self, status=200, content_type="text/plain", text="", url="https://example.com/x", headers=None):
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.headers = headers if headers is not None else {"Content-Type": content_type}
        mock_response.text = text
        mock_response.content = text.encode("utf-8")
        mock_response.url = url
        return mock_response

    def test_check_endpoint_metadata_html(self):
        """Test _check_endpoint luu metadata HTML: title, meta, h1."""
        scanner = DiscoveryScanner(sample_size=200)
        html = (
            "<!DOCTYPE html><html><head>"
            "<title>HSX - Trang chu</title>"
            '<meta name="description" content="San giao dich chung khoan">'
            "</head><body><h1>Trang chu HSX</h1></body></html>"
        )
        mock_response = self._make_mock_response(
            status=200, content_type="text/html", text=html,
            url="https://example.com/bang-gia",
        )
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/bang-gia")
        assert res["content_type"] == "text/html"
        assert res["response_size_bytes"] == len(html.encode("utf-8"))
        assert res["html_title"] == "HSX - Trang chu"
        assert res["meta_description"] == "San giao dich chung khoan"
        assert res["h1"] == "Trang chu HSX"
        assert res["json_keys"] == []
        assert res["xml_root_tag"] is None

    def test_check_endpoint_metadata_json(self):
        """Test _check_endpoint luu json_keys cho response JSON."""
        scanner = DiscoveryScanner(sample_size=200)
        body = '{"symbol": "FPT", "price": 100.5, "volume": 1000}'
        mock_response = self._make_mock_response(
            status=200, content_type="application/json", text=body,
            url="https://example.com/api/v1/quotes",
        )
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/api/v1/quotes")
        assert res["json_keys"] == ["symbol", "price", "volume"]
        assert res["html_title"] is None

    def test_check_endpoint_metadata_xml(self):
        """Test _check_endpoint luu xml_root_tag cho response XML."""
        scanner = DiscoveryScanner(sample_size=200)
        body = '<?xml version="1.0"?><urlset><url><loc>https://example.com/</loc></url></urlset>'
        mock_response = self._make_mock_response(
            status=200, content_type="application/xml", text=body,
            url="https://example.com/sitemap.xml",
        )
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/sitemap.xml")
        assert res["xml_root_tag"] == "urlset"

    def test_check_endpoint_metadata_redirect(self):
        """Test _check_endpoint luu redirect_url khi co redirect."""
        scanner = DiscoveryScanner(sample_size=200)
        mock_response = self._make_mock_response(
            status=200, content_type="text/plain", text="redirected",
            url="https://example.com/final-page",
        )
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/start")
        assert res["redirect_url"] == "https://example.com/final-page"

    def test_check_endpoint_metadata_sample_limited(self):
        """Test response_sample bi gioi han boi sample_size."""
        scanner = DiscoveryScanner(sample_size=10)
        body = "1234567890ABCDEF"
        mock_response = self._make_mock_response(
            status=200, content_type="text/plain", text=body,
        )
        scanner._request_with_retry = MagicMock(return_value=mock_response)

        res = scanner._check_endpoint("https://example.com/x")
        assert res["response_sample"] == "1234567890"

    def test_check_endpoint_metadata_no_response(self):
        """Test metadata mac dinh khi khong co response."""
        scanner = DiscoveryScanner(sample_size=200)
        scanner._request_with_retry = MagicMock(return_value=None)

        res = scanner._check_endpoint("https://example.com/x")
        assert res["status"] is None
        assert res["content_type"] is None
        assert res["response_size_bytes"] == 0
        assert res["response_sample"] == ""
        assert res["redirect_url"] is None
        assert res["html_title"] is None
        assert res["meta_description"] is None
        assert res["h1"] is None
        assert res["json_keys"] == []
        assert res["xml_root_tag"] is None

    def test_sample_size_from_settings(self):
        """Test sample_size doc tu settings.yaml."""
        scanner = DiscoveryScanner()
        assert scanner.sample_size > 0

        scanner2 = DiscoveryScanner(sample_size=500)
        assert scanner2.sample_size == 500

    def test_extract_html_tag(self):
        scanner = DiscoveryScanner()
        assert scanner._extract_html_tag("<title>Hello</title>", "title") == "Hello"
        assert scanner._extract_html_tag("<h1>   <b>Title</b>  </h1>", "h1") == "Title"
        assert scanner._extract_html_tag("no tags here", "title") is None

    def test_extract_json_keys(self):
        scanner = DiscoveryScanner()
        assert scanner._extract_json_keys('{"a": 1, "b": 2}') == ["a", "b"]
        assert scanner._extract_json_keys('[{"x": 1}]') == ["x"]
        assert scanner._extract_json_keys("not json") == []
        assert scanner._extract_json_keys("") == []

    def test_extract_xml_root_tag(self):
        scanner = DiscoveryScanner()
        assert scanner._extract_xml_root_tag('<urlset xmlns="http://x">') == "urlset"
        assert scanner._extract_xml_root_tag("plain text") is None
        assert scanner._extract_xml_root_tag("") is None


class TestSaveReport:
    """Test luu bao cao JSON."""

    def test_save_report(self):
        scanner = DiscoveryScanner()

        results = [
            DiscoveryResult(
                name="TEST1",
                url="https://test1.com",
                robots=True,
                possible_api=["/api/v1"],
            ),
            DiscoveryResult(
                name="TEST2", url="https://test2.com", sitemap=True
            ),
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            temp_path = f.name

        try:
            saved_path = scanner.save_report(results, temp_path)
            assert Path(saved_path).exists()

            with open(temp_path, "r", encoding="utf-8") as f:
                report = json.load(f)

            assert len(report) == 2
            assert "test1" in report
            assert "test2" in report
            assert report["test1"]["robots"]["found"] is True
            assert report["test1"]["api_tests"][0]["url"] == "/api/v1"
            assert report["test2"]["sitemap"]["found"] is True
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_save_report_creates_directory(self):
        """Test save_report tao thu muc neu chua co."""
        scanner = DiscoveryScanner()
        results = [
            DiscoveryResult(name="TEST", url="https://test.com"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "subdir" / "report.json"
            # Ensure parent does not exist first, to test creation
            parent_dir = output_path.parent
            if parent_dir.exists():
                import shutil
                shutil.rmtree(parent_dir)
            saved_path = scanner.save_report(results, str(output_path))
            assert Path(saved_path).exists()

            with open(saved_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            assert len(report) == 1

    def test_save_report_empty_results(self):
        """Test save_report voi danh sach rong."""
        scanner = DiscoveryScanner()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            temp_path = f.name

        try:
            scanner.save_report([], temp_path)
            with open(temp_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            assert report == {}
        finally:
            Path(temp_path).unlink(missing_ok=True)


def run_tests():
    """Chay tat ca tests."""
    test_classes = [
        TestDiscoveryResult,
        TestDiscoveryScanner,
        TestSaveReport,
    ]

    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{'='*50}")
        print(f"  {test_class.__name__}")
        print("=" * 50)

        instance = test_class()
        for name in sorted(dir(instance)):
            if name.startswith("test_"):
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    print(f"         {type(e).__name__}: {e}")
                    failed += 1

    print(f"\n{'='*50}")
    print(f"  Ket qua: {passed} passed, {failed} failed")
    print("=" * 50)

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
