"""
Index Page Crawler - Thu thap danh sach URL trang thuc te tu homepage, sitemap, RSS.
Task 3: CHI lay URL list, khong lay du lieu chung khoan.
Khong parse bang, khong infer capability, khong tao endpoint, khong score URL.
Deterministic, retry policy nhat quan voi project, configurable delay + max_urls.
"""
import json
import re
import time
import logging
from urllib.parse import urljoin, urlparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests
from requests.exceptions import RequestException, Timeout, SSLError

try:
    from ..utils.source_loader import load_sources
    from ..utils.source_models import SourceConfig
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.source_loader import load_sources
    from utils.source_models import SourceConfig
    from utils.config_loader import load_settings


class IndexCrawler:
    """Crawler lay URL list tu trang chu + sitemap + RSS."""

    # Page extensions duoc giu lai (whitelist)
    PAGE_EXTENSIONS = {".html", ".htm", ".php", ".aspx", ".jsp"}

    DEFAULT_MAX_URLS_PER_SOURCE = 10
    DEFAULT_REQUEST_DELAY = 0.5  # seconds
    DEFAULT_TIMEOUT = 10
    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.0  # seconds, nhat quan voi DiscoveryScanner
    USER_AGENT = "StockScanner/1.0"

    def __init__(
        self,
        logger: logging.Logger = None,
        base_dir: Path = None,
        max_urls_per_source: int = None,
        request_delay: float = None,
    ):
        self.logger = logger or logging.getLogger("index_crawler")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"

        # Config tu settings.yaml (nhat quan voi DiscoveryScanner)
        try:
            settings = load_settings()
            crawler_cfg = settings.get("crawler", {})
        except Exception:
            crawler_cfg = {}
        self.max_urls_per_source = (
            max_urls_per_source
            if max_urls_per_source is not None
            else crawler_cfg.get("max_urls_per_source") or self.DEFAULT_MAX_URLS_PER_SOURCE
        )
        self.request_delay = (
            request_delay
            if request_delay is not None
            else crawler_cfg.get("request_delay") or self.DEFAULT_REQUEST_DELAY
        )

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        # Cache SSL verify per netloc (nhat quan voi DiscoveryScanner)
        self._ssl_verify_cache: Dict[str, bool] = {}
        self._last_retry_count = 0

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            self.logger.error(f"File khong ton tai: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Loi doc {path}: {e}")
            return None

    # ---------- Fetch (retry policy nhat quan voi project) ----------

    def _request_with_retry(self, url: str, verify: bool = True) -> Optional[requests.Response]:
        """GET url voi retry + backoff + SSL fallback (gio'ng DiscoveryScanner)."""
        self._last_retry_count = 0
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            self._last_retry_count = attempt
            try:
                response = self.session.get(
                    url,
                    timeout=self.DEFAULT_TIMEOUT,
                    allow_redirects=True,
                    verify=verify,
                )
                return response
            except SSLError as e:
                last_error = e
                self.logger.warning(
                    f"SSL error khi GET {url} (lan {attempt + 1}/{self.MAX_RETRIES}): {e}"
                )
                if verify:
                    self.logger.info(f"Thu lai {url} voi verify=False")
                    parsed = urlparse(url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    self._ssl_verify_cache[base] = False
                    verify = False
                    continue
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except Timeout as e:
                last_error = e
                self.logger.warning(
                    f"Timeout khi GET {url} (lan {attempt + 1}/{self.MAX_RETRIES})"
                )
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except RequestException as e:
                last_error = e
                self.logger.warning(
                    f"Loi khi GET {url} (lan {attempt + 1}/{self.MAX_RETRIES}): {e}"
                )
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))

        if last_error is not None:
            self.logger.debug(f"GET {url} that bai sau {self.MAX_RETRIES} lan: {last_error}")
        return None

    def _fetch_text(self, url: str) -> Optional[str]:
        """GET url, tra ve text neu status 200, None neu that bai."""
        response = self._request_with_retry(url)
        if response is None:
            return None
        if response.status_code != 200:
            self.logger.debug(f"  {url}: status {response.status_code}")
            return None
        return response.text

    # ---------- URL extraction ----------

    def _extract_links_from_html(self, html_text: str, base_url: str) -> List[str]:
        """Trich xuat href links tu HTML (regex, khong can thu vien)."""
        links = []
        if not html_text:
            return links
        for match in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html_text, re.IGNORECASE):
            raw = match.group(1)
            if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, raw)
            links.append(absolute)
        return links

    def _extract_locs_from_sitemap(self, xml_text: str) -> List[str]:
        """Trich xuat <loc> URLs tu sitemap XML."""
        locs = []
        if not xml_text:
            return locs
        for match in re.finditer(r"<loc[^>]*>(.*?)</loc>", xml_text, re.IGNORECASE | re.DOTALL):
            url = match.group(1).strip()
            if url:
                locs.append(url)
        return locs

    def _extract_links_from_rss(self, rss_text: str) -> List[str]:
        """Trich xuat <link> URLs tu RSS/Atom feed."""
        links = []
        if not rss_text:
            return links
        # RSS: <link>http://...</link>
        for match in re.finditer(r"<link[^>]*>(.*?)</link>", rss_text, re.IGNORECASE | re.DOTALL):
            url = match.group(1).strip()
            if url.startswith("http"):
                links.append(url)
        # Atom: <link href="http://..."/>
        for match in re.finditer(r'<link[^>]+href\s*=\s*["\']([^"\']+)["\']', rss_text, re.IGNORECASE):
            url = match.group(1)
            if url.startswith("http"):
                links.append(url)
        return links

    # ---------- Page URL filter (whitelist) ----------

    def _is_page_url(self, url: str) -> bool:
        """
        Chi giu URL trang: .html .htm .php .aspx .jsp hoac khong extension.
        Loai asset (.css .js .png ...) va file (.pdf .zip .json .xml .txt).
        """
        try:
            path = urlparse(url).path
        except ValueError:
            return False
        if not path or path == "/":
            return False
        # Lay phan cuoi path, bo query/fragment
        clean = path.split("?")[0].split("#")[0]
        name = clean.rsplit("/", 1)[-1]
        if not name:
            return False

        # Khong co extension -> page (extensionless path)
        if "." not in name:
            return True
        # Co extension -> chi giu page extensions
        lower = name.lower()
        for ext in self.PAGE_EXTENSIONS:
            if lower.endswith(ext):
                return True
        return False

    def _filter_page_urls(self, urls: List[str]) -> List[str]:
        """Loc chi giu URL trang."""
        return [u for u in urls if self._is_page_url(u)]

    def _dedup(self, urls: List[str]) -> List[str]:
        """Loai bo URL trung, giu thu tu (preserve crawl order)."""
        seen = set()
        result = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                result.append(u)
        return result

    # ---------- Crawl one source ----------

    def _crawl_source(
        self, source: SourceConfig, discovery: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Crawl mot source: trang chu + sitemap + rss (chi found=true)."""
        result = {
            "source": source.name,
            "base_url": source.base_url,
            "fetched_at": datetime.now().isoformat(),
            "urls": [],
            "sources_used": [],
        }

        # 1. Trang chu (luon lay)
        homepage = source.base_url
        text = self._fetch_text(homepage)
        if text is not None:
            links = self._extract_links_from_html(text, homepage)
            result["urls"].extend(links)
            result["sources_used"].append({"type": "homepage", "url": homepage})
            self.logger.info(f"  {source.name}: trang chu -> {len(links)} links")
        time.sleep(self.request_delay)

        # 2. Sitemap (chi khi found=true)
        sitemap_entry = discovery.get("sitemap", {})
        if isinstance(sitemap_entry, dict) and sitemap_entry.get("found"):
            sitemap_url = sitemap_entry.get("url")
            if sitemap_url:
                full_url = urljoin(source.base_url, sitemap_url)
                xml_text = self._fetch_text(full_url)
                if xml_text is not None:
                    locs = self._extract_locs_from_sitemap(xml_text)
                    result["urls"].extend(locs)
                    result["sources_used"].append({"type": "sitemap", "url": full_url})
                    self.logger.info(f"  {source.name}: sitemap -> {len(locs)} locs")
            time.sleep(self.request_delay)

        # 3. RSS (chi khi found=true)
        rss_entry = discovery.get("rss", {})
        if isinstance(rss_entry, dict) and rss_entry.get("found"):
            rss_url = rss_entry.get("url")
            if rss_url:
                full_url = urljoin(source.base_url, rss_url)
                rss_text = self._fetch_text(full_url)
                if rss_text is not None:
                    links = self._extract_links_from_rss(rss_text)
                    result["urls"].extend(links)
                    result["sources_used"].append({"type": "rss", "url": full_url})
                    self.logger.info(f"  {source.name}: rss -> {len(links)} links")
            time.sleep(self.request_delay)

        # Dedup + loc page URLs + gioi han (preserve crawl order)
        result["urls"] = self._dedup(self._filter_page_urls(result["urls"]))[
            : self.max_urls_per_source
        ]
        result["url_count"] = len(result["urls"])
        return result

    # ---------- Main flow ----------

    def run(self, discovery_data: Dict[str, Any], sources: List[SourceConfig]) -> Dict[str, Any]:
        """Crawl tat ca source co trong discovery report."""
        report = {"generated_at": datetime.now().isoformat(), "sources": {}}
        for source in sources:
            if not source.enabled:
                continue
            key = source.name.lower()
            if key not in discovery_data:
                self.logger.warning(f"Source {source.name} khong co trong discovery_report")
                continue
            report["sources"][key] = self._crawl_source(source, discovery_data[key])
        return report

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        if output_path is None:
            output_path = self.output_dir / "index_pages.json"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_index_crawl(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay crawler cho tat ca nguon. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    crawler = IndexCrawler(logger=logger)

    discovery_data = crawler._read_json(crawler.output_dir / "discovery_report.json")
    if discovery_data is None:
        logger.error("Thieu discovery_report.json, khong the crawl index")
        return {}

    sources = load_sources()
    logger.info("Bat dau thu thap URL index...")
    report = crawler.run(discovery_data, sources)
    report_path = crawler.save_report(report)

    # In tom tat
    print(f"\n  Ket qua thu thap index:")
    for key, src in report.get("sources", {}).items():
        print(f"    - {src['source']}: {src['url_count']} URLs")

    print(f"\n  Bao cao: output/index_pages.json")
    return report
