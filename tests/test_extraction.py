"""
Unit tests cho Data Extraction Engine (Task 13).
Offline, deterministic, khong network.
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from extractor.extractors import (
    EXTRACTORS, StockListExtractor, HistoricalPriceExtractor,
)
from extractor.engine import ExtractionEngine


def _validated_file(source="hose", capability="stock_list", entries=None):
    return {
        "source": source,
        "capability": capability,
        "schema_valid": True,
        "entries": entries if entries is not None else [],
    }


def _entry(status=200, body='[{"symbol": "FPT", "company_name": "CTCP FPT"}]',
           content_type="application/json"):
    return {
        "url": "/api/stocks", "status": status,
        "content_type": content_type,
        "response_size_bytes": len(body.encode()),
        "fetched_at": "2026-01-01T00:00:00",
        "body": body,
    }


def _make_quality(passed_caps=None):
    passed = passed_caps if passed_caps is not None else ["stock_list"]
    q = {"hose": {}, "generated_at": "x"}
    for cap in ["stock_list", "current_price", "dividends", "historical_price"]:
        q["hose"][cap] = {"quality": "pass" if cap in passed else "fail",
                          "checked_entries": 1, "passed_entries": 1,
                          "failed_entries": 0, "reason": None}
    return q


class TestExtractors:
    """Test tung extractor."""

    def test_stock_list_normalize(self):
        """FIELD_MAP: ma_ck -> symbol, ten -> company_name."""
        ext = StockListExtractor()
        result = ext.extract(_validated_file(entries=[
            _entry(body='[{"ma_ck": "FPT", "ten": "CTCP FPT", "san": "HOSE"}]'),
        ]))
        assert result["extract_success"] is True
        assert result["records"] == [
            {"symbol": "FPT", "company_name": "CTCP FPT", "exchange": "HOSE"}
        ]

    def test_convert_datatype(self):
        """Convert string numeric -> float/int."""
        ext = HistoricalPriceExtractor()
        result = ext.extract(_validated_file(entries=[
            _entry(body='[{"ma_ck": "FPT", "ngay": "2026-01-01", "dong_cua": "100.5", "khoi_luong": "1000"}]'),
        ]))
        record = result["records"][0]
        assert record["close"] == 100.5
        assert record["volume"] == 1000

    def test_parse_failure(self):
        """Body JSON loi -> extract_success=false + errors."""
        ext = StockListExtractor()
        result = ext.extract(_validated_file(entries=[
            _entry(body="{bad json"),
        ]))
        assert result["extract_success"] is False
        assert len(result["errors"]) == 1
        assert "JSONDecodeError" in result["errors"][0]

    def test_entry_status_not_200_skipped(self):
        """Entry status != 200 -> bo qua, khong loi."""
        ext = StockListExtractor()
        result = ext.extract(_validated_file(entries=[
            _entry(status=404, body="nf"),
            _entry(status=200, body='[{"symbol": "FPT"}]'),
        ]))
        assert result["extract_success"] is True
        assert len(result["records"]) == 1
        assert result["errors"] == []

    def test_no_records_failure(self):
        """Khong record nao -> extract_success=false."""
        ext = StockListExtractor()
        result = ext.extract(_validated_file(entries=[
            _entry(body='{"message": "no data"}'),
        ]))
        assert result["extract_success"] is False
        assert result["records"] == []

    def test_wrapper_json(self):
        """Wrapper data/items -> extract records."""
        ext = StockListExtractor()
        result = ext.extract(_validated_file(entries=[
            _entry(body='{"data": [{"symbol": "FPT", "name": "FPT Corp"}]}'),
        ]))
        assert result["records"] == [{"symbol": "FPT", "company_name": "FPT Corp"}]

    def test_all_16_capabilities_registered(self):
        assert len(EXTRACTORS) == 16
        for cap in ["stock_list", "current_price", "historical_price", "ohlcv",
                    "financial_reports", "dividends", "bonus_shares", "rights_issue",
                    "foreign_trading", "company_news", "company_announcements",
                    "sector", "market_cap", "eps", "pe_ratio", "pb_ratio"]:
            assert cap in EXTRACTORS, f"Thieu extractor: {cap}"


class TestEngine:
    """Test orchestrator."""

    def test_run_only_passed(self, tmp_path):
        engine = ExtractionEngine()
        out = tmp_path / "output"
        (out / "validated_data" / "hose").mkdir(parents=True)

        (out / "quality_report.json").write_text(
            json.dumps(_make_quality(passed_caps=["stock_list", "current_price"])),
            encoding="utf-8")
        (out / "validated_data" / "hose" / "stock_list.json").write_text(
            json.dumps(_validated_file(entries=[_entry()])), encoding="utf-8")
        (out / "validated_data" / "hose" / "current_price.json").write_text(
            json.dumps(_validated_file(capability="current_price", entries=[
                _entry(body='[{"symbol": "FPT", "price": "100.5"}]')])),
            encoding="utf-8")
        (out / "validated_data" / "hose" / "dividends.json").write_text(
            json.dumps(_validated_file(capability="dividends", entries=[_entry()])),
            encoding="utf-8")

        engine.output_dir = out
        engine.validated_dir = out / "validated_data"
        report = engine.run()

        # Chi 2 capability pass duoc extract
        assert "hose" in report["sources"]
        assert "stock_list" in report["sources"]["hose"]
        assert "current_price" in report["sources"]["hose"]
        # dividends fail -> khong extract
        assert "dividends" not in report["sources"]["hose"]
        # current_price convert dung
        assert report["sources"]["hose"]["current_price"]["records"][0]["price"] == 100.5

    def test_run_missing_quality(self, tmp_path):
        engine = ExtractionEngine()
        engine.output_dir = tmp_path / "output"
        report = engine.run()
        assert report["sources"] == {}

    def test_save_extracted(self, tmp_path):
        engine = ExtractionEngine()
        report = {
            "sources": {
                "hose": {"stock_list": {"source": "hose", "records": [{"symbol": "FPT"}]}},
            }
        }
        engine.extracted_dir = tmp_path / "extracted_data"
        saved = engine.save_extracted(report)
        assert (Path(saved) / "hose" / "stock_list.json").exists()
        with open(Path(saved) / "hose" / "stock_list.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["records"][0]["symbol"] == "FPT"
