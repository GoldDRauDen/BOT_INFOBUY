"""
Discovery Scanner - Kham pha endpoint va tai nguyen cong khai cua nguon.
Chi kham pha, khong lay du lieu chung khoan.
"""
import json
import re
import time
import logging
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime

import requests
from requests.exceptions import (
    RequestException,
    Timeout,
    ConnectionError as ReqConnectionError,
    SSLError,
)

try:
    from ..utils.source_loader import load_sources
    from ..utils.source_models import SourceConfig
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.source_loader import load_sources
    from utils.source_models import SourceConfig
    from utils.config_loader import load_settings
from .discovery_models import DiscoveryResult


class DiscoveryScanner:
    """Scanner kham pha endpoint cua nguon."""

    # Cac endpoint can kiem tra
    RESOURCE_ENDPOINTS = {
        "robots": "/robots.txt",
        "sitemap": "/sitemap.xml",
        "rss": ["/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml"],
        "favicon": "/favicon.ico",
        "graphql": ["/graphql", "/api/graphql", "/query"],
        "swagger": ["/swagger", "/swagger-ui", "/swagger-ui.html", "/api/docs"],
        "openapi": ["/openapi.json", "/openapi.yaml", "/openapi.yml", "/api/openapi.json"],
    }

    API_ENDPOINTS = [
        "/api",
        "/api/v1",
        "/api/v2",
        "/api/v3",
        "/api/v1/stocks",
        "/api/v2/stocks",
        "/api/stocks",
    ]

    # Status code dong nghia voi "ton tai"
    EXISTS_STATUS_CODES = {200, 401, 403, 405}

    # Path dung de kiem tra catch-all (khong ai dat ten endpoint nhu nay)
    CANARY_PATH = "/_nonexistent_canary_check_xyzzy_42"

    DEFAULT_TIMEOUT = 10
    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.0  # seconds
    REQUEST_DELAY = 0.3  # seconds between requests (polite scanning)
    USER_AGENT = "StockScanner/1.0"
    DEFAULT_SAMPLE_SIZE = 200

    def __init__(
        self,
        timeout: int = None,
        max_retries: int = None,
        logger: logging.Logger = None,
        sample_size: int = None,
    ):
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else self.MAX_RETRIES
        self.logger = logger or logging.getLogger("discovery_scanner")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        # Cache: base_url -> whether server has catch-all 200
        self._catchall_cache: Dict[str, bool] = {}
        # Cache: base_url -> whether SSL verification should be disabled
        self._ssl_verify_cache: Dict[str, bool] = {}
        # Sample size for response_sample, from config/settings.yaml
        if sample_size is not None:
            self.sample_size = sample_size
        else:
            try:
                settings = load_settings()
                self.sample_size = (
                    settings.get("discovery", {}).get("sample_size")
                    or self.DEFAULT_SAMPLE_SIZE
                )
            except Exception:
                self.sample_size = self.DEFAULT_SAMPLE_SIZE

    def _build_url(self, base_url: str, path: str) -> str:
        """Build full URL tu base va path."""
        if not base_url.endswith("/"):
            base_url += "/"
        return urljoin(base_url, path.lstrip("/"))

    def _get_verify(self, base_url: str) -> bool:
        """Tra ve True neu nen verify SSL, False neu khong."""
        if base_url in self._ssl_verify_cache:
            return self._ssl_verify_cache[base_url]
        return True

    def _request_with_retry(
        self,
        url: str,
        method: str = "HEAD",
        verify: bool = True,
    ) -> Optional[requests.Response]:
        """
        Gui request voi retry va backoff.
        Returns: Response hoac None neu that bai.
        """
        self._last_retry_count = 0
        last_error = None
        for attempt in range(self.max_retries):
            self._last_retry_count = attempt
            try:
                if method.upper() == "HEAD":
                    response = self.session.head(
                        url, timeout=self.timeout, allow_redirects=True, verify=verify
                    )
                else:
                    response = self.session.get(
                        url,
                        timeout=self.timeout,
                        allow_redirects=True,
                        verify=verify,
                        stream=True,
                    )
                return response
            except SSLError as e:
                last_error = e
                self.logger.warning(
                    f"SSL error khi kiem tra {url} (lan {attempt + 1}/{self.max_retries}): {e}"
                )
                # Thu lai voi verify=False
                if verify:
                    self.logger.info(f"Thu lai {url} voi verify=False")
                    parsed = urlparse(url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    self._ssl_verify_cache[base] = False
                    verify = False
                    continue
                if attempt < self.max_retries - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except Timeout as e:
                last_error = e
                self.logger.warning(
                    f"Timeout khi kiem tra {url} (lan {attempt + 1}/{self.max_retries})"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except ReqConnectionError as e:
                last_error = e
                self.logger.warning(
                    f"Connection error khi kiem tra {url} (lan {attempt + 1}/{self.max_retries})"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except RequestException as e:
                last_error = e
                self.logger.warning(
                    f"Request error khi kiem tra {url} (lan {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))

        if last_error:
            self.logger.error(f"Da het retry cho {url}: {last_error}")
        return None

    def _detect_catchall(self, base_url: str) -> bool:
        """
        Kiem tra xem server co tra ve 200 cho moi path khong (catch-all).
        Neu canary path tra ve 200 => server co catch-all => can kiem tra ky hon.
        """
        if base_url in self._catchall_cache:
            return self._catchall_cache[base_url]

        verify = self._get_verify(base_url)
        canary_url = self._build_url(base_url, self.CANARY_PATH)
        response = self._request_with_retry(canary_url, method="GET", verify=verify)

        is_catchall = False
        if response is not None and response.status_code == 200:
            is_catchall = True
            self.logger.info(
                f"Server {base_url} co catch-all route (canary tra ve 200)"
            )
        else:
            self.logger.debug(
                f"Server {base_url} khong co catch-all (canary: "
                f"{response.status_code if response else 'no response'})"
            )

        self._catchall_cache[base_url] = is_catchall
        return is_catchall

    def _safe_header_content_type(self, response) -> str:
        """Lay Content-Type header an toan (MagicMock-safe)."""
        headers = getattr(response, "headers", None)
        if headers is None:
            return ""
        try:
            value = headers.get("Content-Type", "")
        except Exception:
            return ""
        return value if isinstance(value, str) else ""

    def _validate_endpoint_content(
        self, url: str, resource_type: str, verify: bool = True,
        response: Optional[requests.Response] = None,
    ) -> bool:
        """
        Kiem tra noi dung response de xac nhan endpoint that su ton tai.
        Neu response da co (tu request truoc), dung lai, khong request them.
        """
        if response is None:
            response = self._request_with_retry(url, method="GET", verify=verify)
        if response is None:
            return False

        if response.status_code not in self.EXISTS_STATUS_CODES:
            return False

        # Voi 401/403/405, endpoint ton tai (can auth hoac method khac)
        if response.status_code in {401, 403, 405}:
            return True

        content_type = self._safe_header_content_type(response)
        content_text = getattr(response, "text", None)
        content_text = content_text if isinstance(content_text, str) else ""

        # Kiem tra theo loai tai nguyen
        if resource_type == "robots":
            # robots.txt phai co "User-agent"
            return "user-agent" in content_text.lower()

        elif resource_type == "sitemap":
            # sitemap.xml phai co "<urlset" hoac "<sitemapindex"
            lower_text = content_text.lower()
            return "<urlset" in lower_text or "<sitemapindex" in lower_text

        elif resource_type == "rss":
            # RSS/Atom feed phai co "<rss" hoac "<feed"
            lower_text = content_text.lower()
            return "<rss" in lower_text or "<feed" in lower_text

        elif resource_type == "favicon":
            # favicon phai la image
            return "image" in content_type or "icon" in content_type

        elif resource_type == "graphql":
            # GraphQL phai co "__schema" hoac "graphql"
            return any(t in content_text.lower() for t in ["__schema", "graphql"])

        elif resource_type == "swagger":
            # Swagger phai co "swagger" hoac "openapi" hoac "swagger-ui"
            return any(t in content_text.lower() for t in ["swagger", "openapi", "swagger-ui"])

        elif resource_type == "openapi":
            # OpenAPI spec la JSON hoac YAML, thuong co "openapi" hoac "swagger"
            return any(t in content_text.lower() for t in ["openapi", "swagger"])

        elif resource_type == "api":
            # API endpoint thuong tra ve JSON
            return "json" in content_type

        return True  # Mac dinh tin tuong

    def _extract_metadata(
        self, response: Optional[requests.Response], url_path: str
    ) -> Dict[str, Any]:
        """
        Trich xuat metadata tu response:
        content_type, response_size_bytes, response_sample, redirect_url,
        html_title, meta_description, h1, json_keys, xml_root_tag.
        Chi ghi nhan du lieu tho, khong phan tich.
        """
        if response is None:
            return {
                "content_type": None,
                "response_size_bytes": 0,
                "response_sample": "",
                "redirect_url": None,
                "html_title": None,
                "meta_description": None,
                "h1": None,
                "json_keys": [],
                "xml_root_tag": None,
            }

        content_type = self._safe_header_content_type(response)
        content_type_lower = content_type.lower()

        # Redirect URL: response.url khac URL goc
        redirect_url = None
        original = url_path
        final = getattr(response, "url", "") or ""
        if (
            isinstance(final, str)
            and final
            and final != original
            and urlparse(final).netloc
        ):
            redirect_url = final

        # Response body
        content = getattr(response, "content", None)
        if not isinstance(content, (bytes, bytearray)):
            content = b""
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            text = ""

        response_size_bytes = len(content)
        response_sample = text[: self.sample_size]

        # HTML metadata
        html_title = None
        meta_description = None
        h1 = None
        if "html" in content_type_lower:
            html_title = self._extract_html_tag(text, "title")
            meta_description = self._extract_meta_description(text)
            h1 = self._extract_html_tag(text, "h1")

        # JSON keys
        json_keys = []
        if "json" in content_type_lower:
            json_keys = self._extract_json_keys(text)

        # XML root tag
        xml_root_tag = None
        if "xml" in content_type_lower:
            xml_root_tag = self._extract_xml_root_tag(text)

        return {
            "content_type": content_type or None,
            "response_size_bytes": response_size_bytes,
            "response_sample": response_sample,
            "redirect_url": redirect_url,
            "html_title": html_title,
            "meta_description": meta_description,
            "h1": h1,
            "json_keys": json_keys,
            "xml_root_tag": xml_root_tag,
        }

    def _extract_html_tag(self, text: str, tag: str) -> Optional[str]:
        """Trich xuat noi dung the HTML dau tien (title, h1...)."""
        if not text:
            return None
        pattern = f"<{tag}[^>]*>(.*?)</{tag}>"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        value = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        return value[:200] if value else None

    def _extract_meta_description(self, text: str) -> Optional[str]:
        """Trich xuat meta description tu HTML."""
        if not text:
            return None
        match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            match = re.search(
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
                text,
                re.IGNORECASE | re.DOTALL,
            )
        if not match:
            return None
        value = match.group(1).strip()
        return value[:200] if value else None

    def _extract_json_keys(self, text: str) -> List[str]:
        """Trich xuat top-level keys tu JSON string."""
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        if isinstance(data, dict):
            return list(data.keys())
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return list(data[0].keys())
        return []

    def _extract_xml_root_tag(self, text: str) -> Optional[str]:
        """Trich xuat root tag tu XML string."""
        if not text:
            return None
        match = re.search(r"<([a-zA-Z_][a-zA-Z0-9_.-]*)[^>]*>", text)
        if not match:
            return None
        return match.group(1)

    def _check_endpoint(
        self, url: str, resource_type: str = "", has_catchall: bool = False
    ) -> Dict[str, Any]:
        """
        Kiem tra mot endpoint.
        Returns: Dict containing URL, status, found, response_time_ms, retry, checked_at
                 va metadata: content_type, response_size_bytes, response_sample,
                 redirect_url, html_title, meta_description, h1, json_keys, xml_root_tag.
        """
        verify = self._get_verify(url)
        start_time = time.perf_counter()
        response = self._request_with_retry(url, method="HEAD", verify=verify)
        response_time_ms = (time.perf_counter() - start_time) * 1000
        retry = getattr(self, "_last_retry_count", 0)
        checked_at = datetime.now().isoformat()

        parsed_url = urlparse(url)
        url_path = parsed_url.path if parsed_url.scheme else url

        if response is None:
            result = {
                "url": url_path,
                "status": None,
                "found": False,
                "response_time_ms": round(response_time_ms, 2),
                "retry": retry,
                "checked_at": checked_at,
            }
            result.update(self._extract_metadata(None, url))
            return result

        status_code = response.status_code
        exists = status_code in self.EXISTS_STATUS_CODES

        # Neu status la 200 va can validate noi dung, can GET body
        body_response = response
        if exists and status_code == 200 and resource_type:
            self.logger.debug(
                f"Validating content cho {url}"
            )
            time.sleep(self.REQUEST_DELAY)
            body_response = self._request_with_retry(url, method="GET", verify=verify)
            is_valid = self._validate_endpoint_content(
                url, resource_type, verify=verify, response=body_response
            )
            if not is_valid:
                self.logger.info(
                    f"  {url}: 200 nhung noi dung khong khop (false positive)"
                )
                exists = False

        result = {
            "url": url_path,
            "status": status_code,
            "found": exists,
            "response_time_ms": round(response_time_ms, 2),
            "retry": retry,
            "checked_at": checked_at,
        }
        result.update(self._extract_metadata(body_response, url))
        return result

    def _check_single_endpoint(
        self, base_url: str, path: str, resource_type: str = "", has_catchall: bool = False
    ) -> Dict[str, Any]:
        """Kiem tra mot endpoint don le."""
        url = self._build_url(base_url, path)
        return self._check_endpoint(url, resource_type=resource_type, has_catchall=has_catchall)

    def _check_multiple_endpoints(
        self,
        base_url: str,
        paths: List[str],
        resource_type: str = "",
        has_catchall: bool = False,
    ) -> Dict[str, Any]:
        """
        Kiem tra nhieu endpoint, tra ve ket qua cua endpoint dau tien ton tai.
        Neu khong co endpoint nao ton tai, tra ve ket qua cua endpoint cuoi cung da thu.
        """
        last_result = None
        for path in paths:
            result = self._check_single_endpoint(
                base_url, path, resource_type=resource_type, has_catchall=has_catchall
            )
            last_result = result
            if result["found"]:
                return result
            time.sleep(self.REQUEST_DELAY)
        
        if last_result:
            return last_result
            
        empty = self._extract_metadata(None, "")
        return {
            "url": paths[0] if paths else "",
            "status": None,
            "found": False,
            "response_time_ms": 0.0,
            "retry": 0,
            "checked_at": datetime.now().isoformat(),
            **empty,
        }

    def scan_source(self, source: SourceConfig) -> DiscoveryResult:
        """
        Kham pha tat ca endpoint cua mot nguon.
        """
        result = DiscoveryResult(name=source.name, url=source.base_url)

        try:
            self.logger.info(f"Bat dau kham pha: {source.name} ({source.base_url})")

            # Buoc 1: Kiem tra SSL
            verify = self._get_verify(source.base_url)
            test_response = self._request_with_retry(
                source.base_url, method="HEAD", verify=verify
            )
            if test_response is None:
                result.error = "Khong the ket noi den server"
                self.logger.error(f"Khong the ket noi den {source.name}")
                return result

            # Cap nhat verify cache
            verify = self._get_verify(source.base_url)

            # Buoc 2: Kiem tra catch-all
            has_catchall = self._detect_catchall(source.base_url)
            if has_catchall:
                self.logger.warning(
                    f"{source.name}: Server co catch-all route, se validate content-type"
                )

            # robots.txt
            robots_res = self._check_single_endpoint(
                source.base_url, "/robots.txt", resource_type="robots", has_catchall=has_catchall
            )
            result.robots = robots_res
            self.logger.info(f"  robots.txt: {'OK' if robots_res['found'] else 'Khong'} ({robots_res['status']})")
            time.sleep(self.REQUEST_DELAY)

            # sitemap.xml
            sitemap_res = self._check_single_endpoint(
                source.base_url, "/sitemap.xml", resource_type="sitemap", has_catchall=has_catchall
            )
            result.sitemap = sitemap_res
            self.logger.info(f"  sitemap.xml: {'OK' if sitemap_res['found'] else 'Khong'} ({sitemap_res['status']})")
            time.sleep(self.REQUEST_DELAY)

            # favicon.ico
            favicon_res = self._check_single_endpoint(
                source.base_url, "/favicon.ico", resource_type="favicon", has_catchall=has_catchall
            )
            result.favicon = favicon_res
            self.logger.info(f"  favicon.ico: {'OK' if favicon_res['found'] else 'Khong'} ({favicon_res['status']})")
            time.sleep(self.REQUEST_DELAY)

            # RSS
            rss_res = self._check_multiple_endpoints(
                source.base_url,
                self.RESOURCE_ENDPOINTS["rss"],
                resource_type="rss",
                has_catchall=has_catchall,
            )
            result.rss = rss_res
            self.logger.info(
                f"  RSS: {'OK (' + rss_res['url'] + ')' if rss_res['found'] else 'Khong'} ({rss_res['status']})"
            )

            # GraphQL
            graphql_res = self._check_multiple_endpoints(
                source.base_url,
                self.RESOURCE_ENDPOINTS["graphql"],
                resource_type="graphql",
                has_catchall=has_catchall,
            )
            result.graphql = graphql_res
            self.logger.info(
                f"  GraphQL: {'OK (' + graphql_res['url'] + ')' if graphql_res['found'] else 'Khong'} ({graphql_res['status']})"
            )

            # Swagger
            swagger_res = self._check_multiple_endpoints(
                source.base_url,
                self.RESOURCE_ENDPOINTS["swagger"],
                resource_type="swagger",
                has_catchall=has_catchall,
            )
            result.swagger = swagger_res
            self.logger.info(
                f"  Swagger: {'OK (' + swagger_res['url'] + ')' if swagger_res['found'] else 'Khong'} ({swagger_res['status']})"
            )

            # OpenAPI
            openapi_res = self._check_multiple_endpoints(
                source.base_url,
                self.RESOURCE_ENDPOINTS["openapi"],
                resource_type="openapi",
                has_catchall=has_catchall,
            )
            result.openapi = openapi_res
            self.logger.info(
                f"  OpenAPI: {'OK (' + openapi_res['url'] + ')' if openapi_res['found'] else 'Khong'} ({openapi_res['status']})"
            )

            # API endpoints pho bien
            api_tests = []
            for api_path in self.API_ENDPOINTS:
                api_res = self._check_single_endpoint(
                    source.base_url,
                    api_path,
                    resource_type="api",
                    has_catchall=has_catchall,
                )
                api_tests.append(api_res)
                if api_res["found"]:
                    self.logger.info(f"  API ({api_path}): OK ({api_res['status']})")
                time.sleep(self.REQUEST_DELAY)

            result.api_tests = api_tests
            self.logger.info(
                f"Kham pha hoan tat: {source.name} - "
                f"robots={result.robots['found']}, sitemap={result.sitemap['found']}, "
                f"rss={result.rss['found']}, graphql={result.graphql['found']}, "
                f"swagger={result.swagger['found']}, openapi={result.openapi['found']}, "
                f"api={len(result.possible_api)}"
            )

        except Exception as e:
            result.error = str(e)
            self.logger.error(f"Loi khi kham pha {source.name}: {e}")

        return result

    def scan_all(self, sources: List[SourceConfig] = None) -> List[DiscoveryResult]:
        """
        Kham pha tat ca nguon.
        Neu khong truyen sources, doc tu config.
        """
        if sources is None:
            sources = load_sources()

        results = []
        for source in sources:
            try:
                result = self.scan_source(source)
                results.append(result)
            except Exception as e:
                self.logger.error(f"Loi khi kham pha {source.name}: {e}")
                results.append(
                    DiscoveryResult(
                        name=source.name,
                        url=source.base_url,
                        error=f"Scanner Error: {e}",
                    )
                )

        return results

    def save_report(
        self, results: List[DiscoveryResult], output_path: str = None
    ) -> str:
        """
        Luu ket qua ra JSON.
        """
        if output_path is None:
            output_dir = Path(__file__).parent.parent.parent / "output"
            output_path = output_dir / "discovery_report.json"
        else:
            output_path = Path(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        def to_check_dict(val, default_path):
            if isinstance(val, dict):
                return val
            return {
                "url": default_path,
                "status": 200 if val else 404,
                "found": bool(val),
                "response_time_ms": 0.0,
                "retry": 0,
                "checked_at": datetime.now().isoformat(),
                "content_type": None,
                "response_size_bytes": 0,
                "response_sample": "",
                "redirect_url": None,
                "html_title": None,
                "meta_description": None,
                "h1": None,
                "json_keys": [],
                "xml_root_tag": None,
            }

        report = {
            r.name.lower(): {
                "robots": to_check_dict(r.robots, "/robots.txt"),
                "sitemap": to_check_dict(r.sitemap, "/sitemap.xml"),
                "rss": to_check_dict(r.rss, "/rss"),
                "graphql": to_check_dict(r.graphql, "/graphql"),
                "swagger": to_check_dict(r.swagger, "/swagger"),
                "api_tests": r.api_tests
            }
            for r in results
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_discovery_scan(logger: logging.Logger = None) -> List[DiscoveryResult]:
    """
    Chay kham pha cho tat ca nguon.
    Ham tien ich cho main.py.
    """
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    scanner = DiscoveryScanner(logger=logger)

    # Chi lay nhung source enable
    sources = [s for s in load_sources() if s.enabled]

    logger.info(f"Bat dau kham pha {len(sources)} nguon...")

    results = scanner.scan_all(sources)
    report_path = scanner.save_report(results)

    # In tom tat
    print(f"\n  Ket qua kham pha:")
    for r in results:
        found_items = []
        if r.robots:
            found_items.append("robots")
        if r.sitemap:
            found_items.append("sitemap")
        if r.rss:
            found_items.append("rss")
        if r.favicon:
            found_items.append("favicon")
        if r.graphql:
            found_items.append("graphql")
        if r.swagger:
            found_items.append("swagger")
        if r.openapi:
            found_items.append("openapi")

        api_count = len(r.possible_api)
        found_str = ", ".join(found_items) if found_items else "Khong co"
        api_str = ", ".join(r.possible_api) if r.possible_api else "khong"
        print(f"    - {r.name}: [{found_str}], API: [{api_str}]")
        if r.error:
            print(f"      Loi: {r.error}")

    return results
