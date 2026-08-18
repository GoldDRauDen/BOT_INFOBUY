"""
Unit tests cho Index Crawler (Task 3).
Khong goi mang that - dung mock.
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crawler.index_crawler import IndexCrawler
from utils.source_models import SourceConfig


def _make_source(name="HOSE", url="https://example.com"):
    return SourceConfig(name=name, enabled=True, type="exchange", base_url=url)


class TestExtractLinks:
    """Test trich xuat URL."""

    def test_extract_html_links(self):
        crawler = IndexCrawler()
        html = '<a href="/bang-gia">Bang gia</a><a href="https://example.com/tin-tuc">Tin</a><a href="#top">Anchor</a>'
        links = crawler._extract_links_from_html(html, "https://example.com/")
        assert "https://example.com/bang-gia" in links
        assert "https://example.com/tin-tuc" in links
        assert not any("#" in l for l in links)

    def test_extract_html_relative(self):
        crawler = IndexCrawler()
        html = '<a href="bang-gia">x</a>'
        links = crawler._extract_links_from_html(html, "https://example.com/")
        assert links == ["https://example.com/bang-gia"]

    def test_extract_sitemap_locs(self):
        crawler = IndexCrawler()
        xml = '<?xml version="1.0"?><urlset><url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b</loc></url></urlset>'
        locs = crawler._extract_locs_from_sitemap(xml)
        assert locs == ["https://example.com/a", "https://example.com/b"]

    def test_extract_rss_links(self):
        crawler = IndexCrawler()
        rss = '<rss><channel><item><link>https://example.com/news/1</link></item></channel></rss>'
        links = crawler._extract_links_from_rss(rss)
        assert "https://example.com/news/1" in links

    def test_extract_atom_link_href(self):
        crawler = IndexCrawler()
        atom = '<feed><entry><link href="https://example.com/news/2"/></entry></feed>'
        links = crawler._extract_links_from_rss(atom)
        assert "https://example.com/news/2" in links

    def test_dedup(self):
        crawler = IndexCrawler()
        urls = ["https://a.com/1", "https://a.com/1", "https://a.com/2"]
        assert crawler._dedup(urls) == ["https://a.com/1", "https://a.com/2"]

    def test_dedup_preserves_order(self):
        crawler = IndexCrawler()
        urls = ["https://a.com/b", "https://a.com/a", "https://a.com/b", "https://a.com/c"]
        assert crawler._dedup(urls) == ["https://a.com/b", "https://a.com/a", "https://a.com/c"]


class TestPageFilter:
    """Test whitelist page URL filter."""

    def test_keep_page_extensions(self):
        crawler = IndexCrawler()
        for ext in [".html", ".htm", ".php", ".aspx", ".jsp"]:
            assert crawler._is_page_url(f"https://example.com/page{ext}") is True

    def test_keep_extensionless(self):
        crawler = IndexCrawler()
        assert crawler._is_page_url("https://example.com/bang-gia") is True
        assert crawler._is_page_url("https://example.com/") is False  # root, khong phai page

    def test_filter_assets(self):
        crawler = IndexCrawler()
        for asset in [".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                      ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map"]:
            assert crawler._is_page_url(f"https://example.com/file{asset}") is False

    def test_filter_other_files(self):
        crawler = IndexCrawler()
        for f in [".pdf", ".zip", ".json", ".xml", ".txt", ".mp4"]:
            assert crawler._is_page_url(f"https://example.com/file{f}") is False

    def test_asset_with_query(self):
        crawler = IndexCrawler()
        assert crawler._is_page_url("https://example.com/style.css?v=1.5") is False
        assert crawler._is_page_url("https://example.com/bang-gia?v=1") is True

    def test_filter_page_urls(self):
        crawler = IndexCrawler()
        urls = [
            "https://example.com/a.css",
            "https://example.com/bang-gia",
            "https://example.com/c.js",
            "https://example.com/page.html",
        ]
        assert crawler._filter_page_urls(urls) == [
            "https://example.com/bang-gia",
            "https://example.com/page.html",
        ]


class TestCrawlSource:
    """Test crawl mot source."""

    def test_crawl_homepage_only(self):
        """Khong co sitemap/rss found -> chi trang chu."""
        crawler = IndexCrawler()
        source = _make_source()
        discovery = {
            "sitemap": {"found": False},
            "rss": {"found": False},
        }
        html = '<a href="/bang-gia">x</a>'
        with patch.object(crawler, "_fetch_text", return_value=html) as mock_fetch:
            result = crawler._crawl_source(source, discovery)

        assert result["url_count"] == 1
        assert "https://example.com/bang-gia" in result["urls"]
        assert mock_fetch.call_count == 1  # chi trang chu

    def test_crawl_with_sitemap(self):
        crawler = IndexCrawler()
        source = _make_source()
        discovery = {
            "sitemap": {"found": True, "url": "/sitemap.xml"},
            "rss": {"found": False},
        }
        responses = {
            "https://example.com/": '<a href="/a">x</a>',
            "https://example.com/sitemap.xml": '<urlset><url><loc>https://example.com/s</loc></url></urlset>',
        }
        with patch.object(crawler, "_fetch_text", side_effect=lambda url: responses.get(url)):
            result = crawler._crawl_source(source, discovery)

        assert "https://example.com/s" in result["urls"]
        assert any(s["type"] == "sitemap" for s in result["sources_used"])

    def test_crawl_limit(self):
        """Gioi han MAX_URLS_PER_SOURCE."""
        crawler = IndexCrawler()
        source = _make_source()
        discovery = {"sitemap": {"found": False}, "rss": {"found": False}}
        html = "".join(f'<a href="/page{i}">x</a>' for i in range(30))
        with patch.object(crawler, "_fetch_text", return_value=html):
            result = crawler._crawl_source(source, discovery)

        assert result["url_count"] <= crawler.max_urls_per_source

    def test_asset_filter(self):
        """Loc bo asset URLs (css, js, img)."""
        crawler = IndexCrawler()
        source = _make_source()
        discovery = {"sitemap": {"found": False}, "rss": {"found": False}}
        html = (
            '<a href="/style.css">x</a>'
            '<a href="/app.js">x</a>'
            '<a href="/favicon.ico">x</a>'
            '<a href="/logo.png">x</a>'
            '<a href="/bang-gia">x</a>'
            '<a href="/tin-tuc">x</a>'
        )
        with patch.object(crawler, "_fetch_text", return_value=html):
            result = crawler._crawl_source(source, discovery)

        urls = result["urls"]
        assert "https://example.com/bang-gia" in urls
        assert "https://example.com/tin-tuc" in urls
        assert not any(u.endswith((".css", ".js", ".ico", ".png")) for u in urls)

    def test_page_url_with_query(self):
        """URL co query nhung path ket thuc .css -> asset; extensionless -> page."""
        crawler = IndexCrawler()
        assert crawler._is_page_url("https://example.com/style.css?v=1.5") is False
        assert crawler._is_page_url("https://example.com/bang-gia?v=1") is True

    def test_crawl_rss_only_for_company_news_capability(self):
        """RSS found -> lay link tu RSS."""
        crawler = IndexCrawler()
        source = _make_source()
        discovery = {
            "sitemap": {"found": False},
            "rss": {"found": True, "url": "/feed"},
        }
        responses = {
            "https://example.com/": '<a href="/">x</a>',
            "https://example.com/feed": '<rss><channel><item><link>https://example.com/news/1</link></item></channel></rss>',
        }
        with patch.object(crawler, "_fetch_text", side_effect=lambda url: responses.get(url)):
            result = crawler._crawl_source(source, discovery)

        assert "https://example.com/news/1" in result["urls"]


class TestRun:
    """Test run toan bo."""

    def test_run_filters_disabled(self):
        crawler = IndexCrawler()
        discovery = {"hose": {"sitemap": {"found": False}, "rss": {"found": False}}}
        sources = [
            _make_source(name="HOSE"),
            SourceConfig(name="UPCOM", enabled=False, type="exchange", base_url="https://upcom.com"),
        ]
        with patch.object(crawler, "_fetch_text", return_value='<a href="/a">x</a>'):
            report = crawler.run(discovery, sources)

        assert "hose" in report["sources"]
        assert "upcom" not in report["sources"]


class TestConfig:
    """Test configurable settings."""

    def test_default_config(self):
        crawler = IndexCrawler()
        assert crawler.max_urls_per_source == IndexCrawler.DEFAULT_MAX_URLS_PER_SOURCE
        assert crawler.request_delay == IndexCrawler.DEFAULT_REQUEST_DELAY

    def test_override_params(self):
        crawler = IndexCrawler(max_urls_per_source=3, request_delay=0.1)
        assert crawler.max_urls_per_source == 3
        assert crawler.request_delay == 0.1

    def test_max_urls_applied(self):
        """max_urls_per_source configurable - gioi han output."""
        crawler = IndexCrawler(max_urls_per_source=2, request_delay=0)
        source = _make_source()
        discovery = {"sitemap": {"found": False}, "rss": {"found": False}}
        html = '<a href="/a.html">x</a><a href="/b.html">x</a><a href="/c.html">x</a>'
        with patch.object(crawler, "_fetch_text", return_value=html):
            result = crawler._crawl_source(source, discovery)
        assert result["url_count"] == 2


class TestRetry:
    """Test retry policy nhat quan voi project."""

    def test_retry_on_failure(self):
        crawler = IndexCrawler()
        with patch.object(crawler.session, "get", side_effect=requests.exceptions.Timeout()):
            assert crawler._request_with_retry("https://example.com/") is None
        assert crawler._last_retry_count == IndexCrawler.MAX_RETRIES - 1

    def test_success_first_attempt(self):
        crawler = IndexCrawler()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "<html></html>"
        with patch.object(crawler.session, "get", return_value=resp):
            result = crawler._request_with_retry("https://example.com/")
        assert result is resp
        assert crawler._last_retry_count == 0
