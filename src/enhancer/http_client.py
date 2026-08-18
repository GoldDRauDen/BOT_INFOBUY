"""
HTTP Client - Policy HTTP cho Discovery Enhancement (Task 15).
GET duy nhat, chi xu ly 200, retry 3, delay 1.0s, SSL fallback.
Nhat quan voi policy project.
"""
import time
import logging
from urllib.parse import urlparse
from typing import Optional

import requests
from requests.exceptions import RequestException, Timeout, SSLError


class EnhancerHttpClient:
    """HTTP client voi policy rieng cua enhancer."""

    USER_AGENT = "StockScanner/1.0"

    def __init__(self, logger: logging.Logger = None, timeout: int = 15,
                 retries: int = 3, request_delay: float = 1.0,
                 backoff: float = 1.0):
        self.logger = logger or logging.getLogger("enhancer_http")
        self.timeout = timeout
        self.retries = retries
        self.request_delay = request_delay
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self._ssl_verify_cache = {}
        self._last_retry_count = 0
        self._request_count = 0

    def _should_delay(self, url: str, seen_urls: set) -> bool:
        """Delay giua cac request khac nhau (bo qua request trung)."""
        return url not in seen_urls

    def get_text(self, url: str) -> Optional[str]:
        """
        GET url, tra text chi khi status == 200. None neu loi/non-200.
        """
        self._last_retry_count = 0
        verify = self._ssl_verify_cache.get(urlparse(url).netloc, True)
        for attempt in range(self.retries + 1):
            self._last_retry_count = attempt
            try:
                response = self.session.get(url, timeout=self.timeout,
                                            allow_redirects=True, verify=verify)
                self._request_count += 1
                if response.status_code == 200:
                    return response.text
                self.logger.debug(f"GET {url} -> status {response.status_code}, bo qua")
                return None
            except SSLError as e:
                self.logger.warning(f"SSL error {url} (lan {attempt + 1}): {e}")
                if verify:
                    self._ssl_verify_cache[urlparse(url).netloc] = False
                    verify = False
                    continue
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
            except Timeout:
                self.logger.warning(f"Timeout {url} (lan {attempt + 1})")
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
            except RequestException as e:
                self.logger.warning(f"Loi {url} (lan {attempt + 1}): {e}")
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
        return None

    def get_json(self, url: str) -> Optional[dict]:
        """GET url, parse JSON neu 200. None neu loi."""
        text = self.get_text(url)
        if text is None:
            return None
        try:
            import json
            return json.loads(text)
        except ValueError:
            return None
