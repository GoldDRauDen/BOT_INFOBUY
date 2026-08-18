"""
Probe - Browser-like GET probes an toan.
Task 16: GET duy nhat (khong POST side effects), co gioi han.
Khong brute-force, khong bypass auth. 401/403 -> ghi nhan can auth.
"""
import time
import logging
from urllib.parse import urljoin, urlparse
from typing import Dict, Optional

import requests
from requests.exceptions import RequestException, Timeout, SSLError


class ProbeClient:
    """Probe GET endpoint an toan."""

    USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    MAX_BODY_SAMPLE = 2048  # 2KB

    def __init__(self, logger: logging.Logger = None, timeout: int = 10,
                 retries: int = 2, request_delay: float = 1.0,
                 backoff: float = 1.0):
        self.logger = logger or logging.getLogger("reverser_probe")
        self.timeout = timeout
        self.retries = retries
        self.request_delay = request_delay
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self._ssl_verify_cache = {}
        self._probe_count = 0

    def probe(self, url: str, referer: str = None) -> Optional[Dict]:
        """
        GET url, tra {status, content_type, body_sample, truncated, headers}.
        None neu khong the ket noi (network error hoac non-HTTP).
        401/403/405 van tra ve (thong tin huu ich).
        """
        self._probe_count += 1
        verify = self._ssl_verify_cache.get(urlparse(url).netloc, True)
        headers = {"Accept": "application/json, text/javascript, */*; q=0.01"}
        if referer:
            headers["Referer"] = referer

        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout,
                                            headers=headers, allow_redirects=True,
                                            verify=verify)
                body = response.content
                truncated = len(body) > self.MAX_BODY_SAMPLE
                return {
                    "status": response.status_code,
                    "content_type": response.headers.get("Content-Type", ""),
                    "body_sample": body[: self.MAX_BODY_SAMPLE].decode("utf-8", errors="replace"),
                    "truncated": truncated,
                    "headers": dict(response.headers),
                }
            except SSLError as e:
                self.logger.warning(f"Probe SSL error {url}: {e}")
                if verify:
                    self._ssl_verify_cache[urlparse(url).netloc] = False
                    verify = False
                    continue
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
            except (Timeout, RequestException) as e:
                self.logger.warning(f"Probe loi {url} (lan {attempt + 1}): {e}")
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
        return None
