"""
Unit tests cho URL Selector (Task 7).
Offline, deterministic, khong network.
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from builder.url_selector import UrlSelector


def _capability_report():
    return {
        "hose": {
            "stock_list": {
                "status": "supported",
                "evidence": {"url": "/api/v1/stocks", "matched_field": "json_keys"},
            },
            "current_price": {"status": "supported"},  # khong evidence.url
            "dividends": {"status": "unknown"},        # khong supported
            "sector": {"status": "supported", "evidence": {}},  # evidence rong
        },
        "hnx": {
            "stock_list": {"status": "unknown"},
        },
        "generated_at": "2026-01-01T00:00:00",
    }


def _index_pages():
    return {
        "hose": {
            "urls": [
                "https://example.com/bang-gia",
                "https://example.com/tin-tuc",
                "https://example.com/ve-chung-toi",
            ]
        },
        "hnx": {"urls": []},
    }


class TestGetEvidenceUrl:
    """Test lay evidence.url."""

    def test_supported_with_evidence(self):
        selector = UrlSelector()
        cap = {"status": "supported", "evidence": {"url": "/api/stocks"}}
        assert selector._get_evidence_url(cap) == "/api/stocks"

    def test_not_supported_returns_none(self):
        selector = UrlSelector()
        cap = {"status": "unknown", "evidence": {"url": "/api/stocks"}}
        assert selector._get_evidence_url(cap) is None

    def test_no_evidence(self):
        selector = UrlSelector()
        assert selector._get_evidence_url({"status": "supported"}) is None
        assert selector._get_evidence_url({"status": "supported", "evidence": {}}) is None
        assert selector._get_evidence_url({"status": "supported", "evidence": None}) is None
        assert selector._get_evidence_url(None) is None

    def test_empty_evidence_url(self):
        selector = UrlSelector()
        cap = {"status": "supported", "evidence": {"url": ""}}
        assert selector._get_evidence_url(cap) is None


class TestSelectForCapability:
    """Test chon URL."""

    def test_evidence_url_priority(self):
        selector = UrlSelector()
        cap = {"status": "supported", "evidence": {"url": "/api/stocks"}}
        entry = selector._select_for_capability("stock_list", cap, ["https://a.com/x"])
        assert entry == {
            "status": "planned",
            "url": "/api/stocks",
            "reason": "capability_evidence",
        }

    def test_first_index_page_fallback(self):
        selector = UrlSelector()
        cap = {"status": "supported"}
        urls = ["https://a.com/1", "https://a.com/2"]
        entry = selector._select_for_capability("current_price", cap, urls)
        assert entry == {
            "status": "planned",
            "url": "https://a.com/1",
            "reason": "first_available_index_page",
        }

    def test_no_url_no_entry(self):
        selector = UrlSelector()
        cap = {"status": "supported"}
        assert selector._select_for_capability("x", cap, []) is None

    def test_not_supported_no_entry(self):
        selector = UrlSelector()
        cap = {"status": "unknown"}
        assert selector._select_for_capability("x", cap, ["https://a.com/1"]) is None

    def test_dedup_preserves_order(self):
        selector = UrlSelector()
        urls = ["https://a.com/1", "https://a.com/1", "https://a.com/2"]
        assert selector._dedup_index_urls(urls) == ["https://a.com/1", "https://a.com/2"]


class TestBuildPlan:
    """Test build plan toan bo."""

    def test_build_plan_schema(self):
        selector = UrlSelector()
        plan = selector.build_plan(_capability_report(), _index_pages())

        # Schema: {source: {cap: {status, url, reason}}, generated_at}
        assert "generated_at" in plan
        assert "hose" in plan
        assert "hnx" in plan

        # stock_list: evidence.url
        assert plan["hose"]["stock_list"] == {
            "status": "planned",
            "url": "/api/v1/stocks",
            "reason": "capability_evidence",
        }
        # current_price: khong evidence -> fallback first index page
        assert plan["hose"]["current_price"] == {
            "status": "planned",
            "url": "https://example.com/bang-gia",
            "reason": "first_available_index_page",
        }
        # sector: evidence rong -> fallback
        assert plan["hose"]["sector"]["reason"] == "first_available_index_page"

    def test_unsupported_excluded(self):
        selector = UrlSelector()
        plan = selector.build_plan(_capability_report(), _index_pages())
        # dividends unknown -> khong co trong plan
        assert "dividends" not in plan["hose"]
        # hnx toan unknown -> khong co entry
        assert plan["hnx"] == {}

    def test_no_url_source(self):
        """Source khong co index urls va khong evidence -> khong entry."""
        selector = UrlSelector()
        caps = {"hose": {"cap": {"status": "supported"}}}
        index = {"hose": {"urls": []}}
        plan = selector.build_plan(caps, index)
        assert plan["hose"] == {}

    def test_deterministic(self):
        """Cung input -> cung output (bo timestamp)."""
        selector = UrlSelector()
        p1 = selector.build_plan(_capability_report(), _index_pages())
        p2 = selector.build_plan(_capability_report(), _index_pages())
        p1.pop("generated_at")
        p2.pop("generated_at")
        assert p1 == p2

    def test_no_duplicate_urls(self):
        selector = UrlSelector()
        caps = {"hose": {"a": {"status": "supported"}, "b": {"status": "supported"}}}
        index = {"hose": {"urls": ["https://x.com/1", "https://x.com/1", "https://x.com/2"]}}
        plan = selector.build_plan(caps, index)
        # Dedup giu order: URL dau tien la https://x.com/1
        assert plan["hose"]["a"]["url"] == "https://x.com/1"
        assert plan["hose"]["b"]["url"] == "https://x.com/1"  # cung first index page

    def test_missing_reports(self):
        selector = UrlSelector()
        plan = selector.build_plan(None, None)
        assert plan == {"generated_at": plan["generated_at"]}


class TestSaveReport:
    """Test luu report."""

    def test_save_report(self):
        selector = UrlSelector()
        report = {"hose": {}, "generated_at": "2026-01-01T00:00:00"}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name
        try:
            saved = selector.save_report(report, temp_path)
            assert Path(saved).exists()
            with open(saved, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["generated_at"] == "2026-01-01T00:00:00"
        finally:
            Path(temp_path).unlink(missing_ok=True)
