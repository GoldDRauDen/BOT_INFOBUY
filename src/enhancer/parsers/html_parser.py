"""
HTML Parser - Parse HTML day du: script src, link, a href.
Task 15: Trich JS bundle URLs, doc links, page links.
"""
import re
from urllib.parse import urljoin

from .common import is_valid_endpoint, ASSET_EXTENSIONS


class HtmlParser:
    """Parse HTML de tim JS bundles + links."""

    SCRIPT_SRC_PATTERN = re.compile(r"""<script[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    LINK_HREF_PATTERN = re.compile(r"""<link[^>]+href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    A_HREF_PATTERN = re.compile(r"""<a[^>]+href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    INLINE_SCRIPT_PATTERN = re.compile(
        r"""<script(?![^>]*src)[^>]*>(.*?)</script>""", re.IGNORECASE | re.DOTALL
    )

    def __init__(self, base_url: str):
        self.base_url = base_url

    def _absolute(self, url: str) -> str:
        """Chuyen relative -> absolute."""
        if url.startswith(("http://", "https://")):
            return url
        return urljoin(self.base_url, url)

    def extract(self, html: str) -> dict:
        """Trich JS bundles, doc links, page links, inline scripts."""
        js_bundles = []
        doc_links = []
        page_links = []
        inline_scripts = []

        for m in self.SCRIPT_SRC_PATTERN.finditer(html):
            url = m.group(1)
            # Bundle la asset can fetch, khong phai endpoint - chi check .js
            if url.endswith(".js"):
                js_bundles.append(self._absolute(url))

        for m in self.LINK_HREF_PATTERN.finditer(html):
            url = m.group(1)
            # Chi giu doc/api links (swagger, openapi, api docs)
            low = url.lower()
            if any(k in low for k in ("swagger", "openapi", "api-doc", "api/")):
                doc_links.append(self._absolute(url))

        for m in self.A_HREF_PATTERN.finditer(html):
            url = m.group(1)
            path = url.split("?")[0].split("#")[0].lower()
            if url.startswith(("#", "javascript:", "mailto:")):
                continue
            if any(path.endswith(ext) for ext in ASSET_EXTENSIONS):
                continue
            page_links.append(self._absolute(url))

        for m in self.INLINE_SCRIPT_PATTERN.finditer(html):
            inline_scripts.append(m.group(1))

        return {
            "js_bundles": js_bundles,
            "doc_links": doc_links,
            "page_links": page_links,
            "inline_scripts": inline_scripts,
        }
