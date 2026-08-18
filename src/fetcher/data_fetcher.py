"""
Data Fetcher - Lay du lieu thuc tu cac URL trong endpoint_plan, luu raw.
Task 8: CHI fetch + luu raw response. Khong validate, khong normalize,
khong transform, khong evaluate schema, khong xac dinh pass/fail.
GET chi cac URL trong endpoint_plan.json. Khong tao URL moi, khong crawl,
khong discover, khong infer capability.
"""
import json
import time
import logging
from urllib.parse import urlparse
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


class DataFetcher:
    """Fetch raw data theo endpoint plan."""

    DEFAULT_TIMEOUT = 15
    DEFAULT_RETRIES = 2
    DEFAULT_REQUEST_DELAY = 0.5  # seconds
    DEFAULT_MAX_URLS_PER_CAPABILITY = 5
    RETRY_BACKOFF = 1.0  # seconds, nhat quan voi project
    USER_AGENT = "StockScanner/1.0"

    def __init__(
        self,
        logger: logging.Logger = None,
        base_dir: Path = None,
        timeout: int = None,
        retries: int = None,
        request_delay: float = None,
        max_urls_per_capability: int = None,
    ):
        self.logger = logger or logging.getLogger("data_fetcher")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"
        self.raw_dir = self.output_dir / "raw_data"

        # Config tu settings.yaml
        try:
            settings = load_settings()
            fetcher_cfg = settings.get("fetcher", {})
        except Exception:
            fetcher_cfg = {}
        self.timeout = timeout if timeout is not None else fetcher_cfg.get("timeout") or self.DEFAULT_TIMEOUT
        self.retries = retries if retries is not None else fetcher_cfg.get("retries") or self.DEFAULT_RETRIES
        self.request_delay = (
            request_delay
            if request_delay is not None
            else fetcher_cfg.get("request_delay") or self.DEFAULT_REQUEST_DELAY
        )
        self.max_urls_per_capability = (
            max_urls_per_capability
            if max_urls_per_capability is not None
            else fetcher_cfg.get("max_urls_per_capability") or self.DEFAULT_MAX_URLS_PER_CAPABILITY
        )

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        # Cache SSL verify per netloc (nhat quan voi project)
        self._ssl_verify_cache: Dict[str, bool] = {}
        self._last_retry_count = 0
        # Task 16: endpoint profiles enrichment (None-safe)
        self._profiles = self._load_profiles()

    def _load_profiles(self) -> Dict[str, Any]:
        """Doc endpoint_profiles.json (Task 16) - tra {} neu thieu/loi."""
        path = self.output_dir / "endpoint_profiles.json"
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profiles = {}
            for src_key, src_data in (data.get("sources") or {}).items():
                for profile in (src_data.get("profiles") or []):
                    if isinstance(profile, dict) and profile.get("url"):
                        profiles[profile["url"]] = profile
            return profiles
        except Exception as e:
            self.logger.warning(f"Loi doc endpoint_profiles.json: {e}")
            return {}

    def _find_profile(self, url: str) -> Optional[Dict[str, Any]]:
        """Tim profile cho url (match path)."""
        for profile_url, profile in self._profiles.items():
            if profile_url in url or url in profile_url:
                return profile
        return None

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Any]:
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
        """GET url voi retry + backoff + SSL fallback + profile headers."""
        self._last_retry_count = 0
        last_error = None
        # Task 16: them headers tu profile (neu co)
        extra_headers = {}
        profile = self._find_profile(url)
        if profile:
            required = profile.get("required_headers") or {}
            for name, value in required.items():
                if isinstance(value, str) and not value.startswith(("${", "$.")):
                    extra_headers[name] = value
        for attempt in range(self.retries + 1):
            self._last_retry_count = attempt
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    verify=verify,
                    headers=extra_headers or None,
                )
                return response
            except SSLError as e:
                last_error = e
                self.logger.warning(
                    f"SSL error khi GET {url} (lan {attempt + 1}/{self.retries + 1}): {e}"
                )
                if verify:
                    self.logger.info(f"Thu lai {url} voi verify=False")
                    parsed_url = urlparse(url)
                    base = f"{parsed_url.scheme}://{parsed_url.netloc}"
                    self._ssl_verify_cache[base] = False
                    verify = False
                    continue
                if attempt < self.retries:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except Timeout as e:
                last_error = e
                self.logger.warning(
                    f"Timeout khi GET {url} (lan {attempt + 1}/{self.retries + 1})"
                )
                if attempt < self.retries:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))
            except RequestException as e:
                last_error = e
                self.logger.warning(
                    f"Loi khi GET {url} (lan {attempt + 1}/{self.retries + 1}): {e}"
                )
                if attempt < self.retries:
                    time.sleep(self.RETRY_BACKOFF * (attempt + 1))

        if last_error is not None:
            self.logger.debug(f"GET {url} that bai sau {self.retries + 1} lan: {last_error}")
        return None

    # ---------- Fetch one capability ----------

    def _fetch_one(self, url: str, source: str, capability: str) -> Dict[str, Any]:
        """
        Fetch mot URL, luu raw response.
        Giu body dung nhu nhan duoc (khong xu ly).
        """
        started = time.perf_counter()
        response = self._request_with_retry(url)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        base = {
            "source": source,
            "capability": capability,
            "url": url,
        }

        if response is None:
            base.update({
                "status": None,
                "content_type": None,
                "headers": {},
                "response_size_bytes": 0,
                "fetched_at": datetime.now().isoformat(),
                "response_time_ms": elapsed_ms,
                "body": None,
            })
            return base

        # Giu body dung nhu nhan duoc
        body = response.content  # bytes goc, khong encode
        base.update({
            "status": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
            "headers": dict(response.headers),
            "response_size_bytes": len(body),
            "fetched_at": datetime.now().isoformat(),
            "response_time_ms": elapsed_ms,
            "body": body.decode("utf-8", errors="replace"),
        })
        return base

    def _fetch_capability(
        self, source: str, cap_name: str, urls: List[str]
    ) -> Dict[str, Any]:
        """
        Fetch cac URL cua mot capability (toi da max_urls_per_capability).
        Continue neu mot URL fail. Preserve fetch order.
        """
        entries = []
        # Dedup giu order
        seen = set()
        unique_urls = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

        limited = unique_urls[: self.max_urls_per_capability]
        for url in limited:
            entry = self._fetch_one(url, source, cap_name)
            entries.append(entry)
            time.sleep(self.request_delay)

        return {"source": source, "capability": cap_name, "entries": entries}

    # ---------- Main flow ----------

    def run(self, endpoint_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch toan bo plan.
        Chi xu ly entries trong plan. Skip source khong co plan entries.
        """
        if not isinstance(endpoint_plan, dict):
            return {"generated_at": datetime.now().isoformat(), "sources": {}}

        report: Dict[str, Any] = {"generated_at": datetime.now().isoformat(), "sources": {}}

        for source_key, source_plan in endpoint_plan.items():
            if source_key == "generated_at":
                continue
            if not isinstance(source_plan, dict) or not source_plan:
                # Skip source khong co plan entries
                continue

            source_result = {"fetched_at": datetime.now().isoformat(), "capabilities": {}}
            for cap_name, cap_plan in source_plan.items():
                if not isinstance(cap_plan, dict):
                    continue
                url = cap_plan.get("url")
                if not isinstance(url, str) or not url:
                    continue
                self.logger.info(f"  Fetch {source_key}/{cap_name}: {url}")
                source_result["capabilities"][cap_name] = self._fetch_capability(
                    source_key, cap_name, [url]
                )
            report["sources"][source_key] = source_result

        return report

    def save_report(self, report: Dict[str, Any]) -> str:
        """Luu raw data vao thu muc raw_data/{source}/{capability}.json."""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        for source_key, source_result in report.get("sources", {}).items():
            source_dir = self.raw_dir / source_key
            source_dir.mkdir(parents=True, exist_ok=True)
            for cap_name, cap_data in source_result.get("capabilities", {}).items():
                path = source_dir / f"{cap_name}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cap_data, f, indent=2, ensure_ascii=False)
                self.logger.debug(f"  Da luu: {path}")
        return str(self.raw_dir)


def run_data_fetcher(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay fetcher theo endpoint plan. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    fetcher = DataFetcher(logger=logger)

    endpoint_plan = fetcher._read_json(fetcher.output_dir / "endpoint_plan.json")
    if endpoint_plan is None:
        logger.error("Thieu endpoint_plan.json, khong the fetch data")
        return {}

    logger.info("Bat dau fetch du lieu theo plan...")
    report = fetcher.run(endpoint_plan)
    raw_dir = fetcher.save_report(report)

    # In tom tat
    total = 0
    print(f"\n  Ket qua fetch du lieu:")
    for key, src in report.get("sources", {}).items():
        n = sum(len(c.get("entries", [])) for c in src.get("capabilities", {}).values())
        total += n
        print(f"    - {key}: {n} responses")
    print(f"\n  Raw data: {raw_dir}")
    return report
