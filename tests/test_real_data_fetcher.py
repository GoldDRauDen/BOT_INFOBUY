"""
Unit tests cho Real Data Fetcher (PHA 2).
Offline tests (mock HTTP). Khong network.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fetcher.real_data_fetcher import RealDataFetcher, _parse_stock_trade, _strip_html


SAMPLE_HTML = """
<html><body>
<script>
var _stockTrade={"StockStatus":"","StatusName":"Kết thúc phiên",
"TradingDate":"31/07/2026 14:58","OpenPrice":"22,600",
"LastPrice":"\\u003cspan class=\\"txt-red price\\"\\u003e21,900\\u003c/span\\u003e",
"ClosePrice":"21,900","TotalVol":"15,165,000",
"LowestPrice":"21,800","HighestPrice":"22,600",
"Change":"\\u003cspan class=\\"txt-red\\"\\u003e-450\\u003c/span\\u003e",
"PerChange":"\\u003cspan class=\\"txt-red\\"\\u003e(-2.01%)\\u003c/span\\u003e"};
</script>
</body></html>
"""


class TestParse:
    """Test parse helpers."""

    def test_strip_html(self):
        assert _strip_html("<span class='x'>21,900</span>") == "21,900"
        assert _strip_html("plain text") == "plain text"
        assert _strip_html("<b>-450</b>") == "-450"

    def test_parse_stock_trade(self):
        data = _parse_stock_trade(SAMPLE_HTML)
        assert data is not None
        assert data["ClosePrice"] == "21,900"
        assert data["TotalVol"] == "15,165,000"
        assert data["TradingDate"] == "31/07/2026 14:58"

    def test_parse_no_match(self):
        assert _parse_stock_trade("<html>no data</html>") is None

    def test_parse_invalid_json(self):
        assert _parse_stock_trade("var _stockTrade={broken}") is None


class TestFetchSymbol:
    """Test fetch_symbol (mock HTTP)."""

    def _make_fetcher(self, tmp_path):
        fetcher = RealDataFetcher(config={"watchlist": ["ACB"]})
        fetcher.output_dir = tmp_path / "output"
        return fetcher

    def test_fetch_ok(self, tmp_path):
        fetcher = self._make_fetcher(tmp_path)
        with patch.object(fetcher, "_fetch_page", return_value=SAMPLE_HTML):
            result = fetcher.fetch_symbol("ACB")
        assert result is not None
        assert result["symbol"] == "ACB"
        assert result["price"] == "21,900"
        assert result["change_percent"] == "(-2.01%)"
        assert result["volume"] == "15,165,000"
        assert result["trading_date"] == "31/07/2026 14:58"
        assert "vietstock" in result["source_url"]

    def test_fetch_page_fails(self, tmp_path):
        fetcher = self._make_fetcher(tmp_path)
        with patch.object(fetcher, "_fetch_page", return_value=None):
            result = fetcher.fetch_symbol("ACB")
        assert result is None

    def test_fetch_no_trade_data(self, tmp_path):
        fetcher = self._make_fetcher(tmp_path)
        with patch.object(fetcher, "_fetch_page", return_value="<html>empty</html>"):
            result = fetcher.fetch_symbol("ACB")
        assert result is None

    def test_fetch_empty_price(self, tmp_path):
        """Co _stockTrade nhung khong co gia -> None."""
        html = 'var _stockTrade={"TradingDate":"31/07/2026","TotalVol":"100"}'
        fetcher = self._make_fetcher(tmp_path)
        with patch.object(fetcher, "_fetch_page", return_value=html):
            result = fetcher.fetch_symbol("ACB")
        assert result is None

    def test_run_saves_report(self, tmp_path):
        fetcher = RealDataFetcher(config={"watchlist": ["ACB", "FPT"]})
        fetcher.output_dir = tmp_path / "output"
        with patch.object(fetcher, "_fetch_page", side_effect=[SAMPLE_HTML, "<html>x</html>"]):
            report = fetcher.run()
        assert report["fetched_count"] == 1
        assert report["error_count"] == 1
        saved = tmp_path / "output" / "real_data" / "prices.json"
        assert saved.exists()
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert data["prices"][0]["symbol"] == "ACB"


class TestFetchPage:
    """Test _fetch_page retry (mock session)."""

    def test_retry_then_success(self):
        fetcher = RealDataFetcher(config={"watchlist": ["ACB"]})
        mock_resp_500 = MagicMock(status_code=500)
        mock_resp_ok = MagicMock(status_code=200, text="<html>ok</html>")
        fetcher.session.get = MagicMock(side_effect=[mock_resp_500, mock_resp_ok])
        html = fetcher._fetch_page("ACB")
        assert html == "<html>ok</html>"
        assert fetcher.session.get.call_count == 2

    def test_all_fail(self):
        fetcher = RealDataFetcher(config={"watchlist": ["ACB"]})
        from requests.exceptions import Timeout
        fetcher.session.get = MagicMock(side_effect=Timeout("slow"))
        with patch("fetcher.real_data_fetcher.time.sleep"):
            html = fetcher._fetch_page("ACB")
        assert html is None
