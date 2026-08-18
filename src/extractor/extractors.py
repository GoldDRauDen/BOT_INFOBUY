"""
Extractors - Trich xuat du lieu chung khoan chuan hoa tu validated_data.
Task 13: Moi capability 1 extractor. Chi parse + normalize + convert.
Khong validate, khong quality check, khong HTTP, khong inference.

FIELD_MAP: bang map field raw -> field chuan (deterministic, khong AI).
"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List


class BaseExtractor:
    """Base class: template method extract() = parse -> normalize -> convert."""

    capability = "base"
    # Output fields chuan hoa
    OUTPUT_FIELDS: List[str] = []
    # Map raw field name -> output field name
    FIELD_MAP: Dict[str, str] = {}

    def extract(self, validated_file: Dict[str, Any]) -> Dict[str, Any]:
        """Template method: trich xuat records tu validated file."""
        records = []
        errors = []

        for entry in validated_file.get("entries", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("status") != 200:
                continue  # entry fail khong duoc extract
            body = entry.get("body")
            if not body:
                continue

            try:
                parsed = self._parse(body, entry.get("content_type", ""))
                for raw_record in parsed:
                    normalized = self._normalize_and_convert(raw_record)
                    if normalized:
                        records.append(normalized)
            except Exception as e:
                errors.append(f"entry_{entry.get('url')}: {type(e).__name__}: {e}")

        return {
            "source": validated_file.get("source"),
            "capability": self.capability,
            "records": records,
            "extract_success": not errors and bool(records),
            "errors": errors,
            "generated_at": datetime.now().isoformat(),
        }

    # ---------- Override hooks ----------

    def _parse(self, body: str, content_type: str) -> List[Dict[str, Any]]:
        """Parse body -> list raw dicts."""
        raise NotImplementedError

    def _normalize_and_convert(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Map raw field -> output field + convert datatype."""
        result = {}
        for raw_key, output_key in self.FIELD_MAP.items():
            if raw_key in raw and raw[raw_key] is not None:
                result[output_key] = self._convert(raw[raw_key], output_key)
        return result

    # ---------- Helpers ----------

    def _convert(self, value: Any, field: str) -> Any:
        """Convert datatype theo field name."""
        if isinstance(value, (int, float)) or value is None:
            return value
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return None
        # Field numeric
        if field in ("price", "open", "high", "low", "close", "volume",
                     "revenue", "profit", "assets", "dividend", "ratio",
                     "buy_volume", "sell_volume", "market_cap", "eps",
                     "pe", "pb"):
            # Bo dau phan cach hang ngan (1,000.5 -> 1000.5)
            cleaned = text.replace(",", "")
            try:
                if "." in cleaned:
                    return float(cleaned)
                return int(cleaned)
            except ValueError:
                return text  # khong convert duoc -> giu nguyen
        return text

    @staticmethod
    def _parse_json_body(body: str) -> Any:
        """Parse body JSON -> object. Raise neu loi."""
        return json.loads(body)

    @staticmethod
    def _extract_records_from_json(data: Any) -> List[Dict[str, Any]]:
        """
        Trich records tu JSON: list, hoac wrapper (data/items/result/records).
        """
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for key in ("data", "items", "results", "result", "records"):
                value = data.get(key)
                if isinstance(value, list):
                    return [d for d in value if isinstance(d, dict)]
                if isinstance(value, dict):
                    return [value]
            if any(k in data for k in ("symbol", "title", "ma_ck", "tieu_de")):
                return [data]
        return []


# ============================================================
# Extractors (16 capabilities)
# ============================================================


class StockListExtractor(BaseExtractor):
    capability = "stock_list"
    OUTPUT_FIELDS = ["symbol", "company_name", "exchange"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol", "code": "symbol",
        "company_name": "company_name", "ten": "company_name", "name": "company_name",
        "exchange": "exchange", "san": "exchange", "market": "exchange",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class CurrentPriceExtractor(BaseExtractor):
    capability = "current_price"
    OUTPUT_FIELDS = ["symbol", "price", "timestamp"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "price": "price", "gia": "price", "last_price": "price", "close": "price",
        "timestamp": "timestamp", "thoi_gian": "timestamp", "time": "timestamp",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class HistoricalPriceExtractor(BaseExtractor):
    capability = "historical_price"
    OUTPUT_FIELDS = ["symbol", "date", "open", "high", "low", "close", "volume"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "date": "date", "ngay": "date", "trading_date": "date",
        "open": "open", "mo_cua": "open", "o": "open",
        "high": "high", "cao_nhat": "high", "h": "high",
        "low": "low", "thap_nhat": "low", "l": "low",
        "close": "close", "dong_cua": "close", "c": "close",
        "volume": "volume", "khoi_luong": "volume", "v": "volume",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class OhlcvExtractor(HistoricalPriceExtractor):
    """ohlcv cung cau truc historical_price (symbol, date, OHLC, volume)."""
    capability = "ohlcv"


class FinancialReportsExtractor(BaseExtractor):
    capability = "financial_reports"
    OUTPUT_FIELDS = ["symbol", "period", "revenue", "profit", "assets"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "period": "period", "ky": "period", "quarter": "period",
        "revenue": "revenue", "doanh_thu": "revenue", "total_revenue": "revenue",
        "profit": "profit", "loi_nhuan": "profit", "net_profit": "profit",
        "assets": "assets", "tai_san": "assets", "total_assets": "assets",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class DividendsExtractor(BaseExtractor):
    capability = "dividends"
    OUTPUT_FIELDS = ["symbol", "date", "dividend", "type"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "date": "date", "ngay": "date", "ex_date": "date",
        "dividend": "dividend", "co_tuc": "dividend", "cash_dividend": "dividend",
        "type": "type", "loai": "type",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class BonusSharesExtractor(BaseExtractor):
    capability = "bonus_shares"
    OUTPUT_FIELDS = ["symbol", "date", "ratio"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "date": "date", "ngay": "date", "ex_date": "date",
        "ratio": "ratio", "ty_le": "ratio", "stock_dividend": "ratio",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class RightsIssueExtractor(BaseExtractor):
    capability = "rights_issue"
    OUTPUT_FIELDS = ["symbol", "date", "ratio"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "date": "date", "ngay": "date", "ex_date": "date",
        "ratio": "ratio", "ty_le": "ratio", "exercise_price": "price",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class ForeignTradingExtractor(BaseExtractor):
    capability = "foreign_trading"
    OUTPUT_FIELDS = ["symbol", "date", "buy_volume", "sell_volume"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "date": "date", "ngay": "date",
        "buy_volume": "buy_volume", "mua": "buy_volume", "foreign_buy": "buy_volume",
        "sell_volume": "sell_volume", "ban": "sell_volume", "foreign_sell": "sell_volume",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class CompanyNewsExtractor(BaseExtractor):
    capability = "company_news"
    OUTPUT_FIELDS = ["title", "date", "url"]
    FIELD_MAP = {
        "title": "title", "tieu_de": "title", "headline": "title",
        "date": "date", "ngay": "date", "published_date": "date",
        "url": "url", "link": "url",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class CompanyAnnouncementsExtractor(BaseExtractor):
    capability = "company_announcements"
    OUTPUT_FIELDS = ["title", "date", "type"]
    FIELD_MAP = {
        "title": "title", "tieu_de": "title",
        "date": "date", "ngay": "date",
        "type": "type", "loai": "type", "announcement_type": "type",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class SectorExtractor(BaseExtractor):
    capability = "sector"
    OUTPUT_FIELDS = ["symbol", "sector"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "sector": "sector", "nganh": "sector", "industry": "sector",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class MarketCapExtractor(BaseExtractor):
    capability = "market_cap"
    OUTPUT_FIELDS = ["symbol", "market_cap"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "market_cap": "market_cap", "von_hoa": "market_cap", "marketcap": "market_cap",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class EpsExtractor(BaseExtractor):
    capability = "eps"
    OUTPUT_FIELDS = ["symbol", "eps"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "eps": "eps", "earnings_per_share": "eps",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class PeRatioExtractor(BaseExtractor):
    capability = "pe_ratio"
    OUTPUT_FIELDS = ["symbol", "pe"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "pe": "pe", "pe_ratio": "pe", "price_earnings": "pe",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


class PbRatioExtractor(BaseExtractor):
    capability = "pb_ratio"
    OUTPUT_FIELDS = ["symbol", "pb"]
    FIELD_MAP = {
        "symbol": "symbol", "ma_ck": "symbol", "ticker": "symbol",
        "pb": "pb", "pb_ratio": "pb", "price_book": "pb",
    }

    def _parse(self, body, content_type):
        return self._extract_records_from_json(self._parse_json_body(body))


# Registry: capability name -> extractor instance (gom ca subclass gian tiep)
def _collect_extractors():
    """Thu thap tat ca extractor classes (ke ca ke thua gian tiep)."""
    registry = {}
    pending = list(BaseExtractor.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.capability != "base":
            registry[cls.capability] = cls()
    return registry


EXTRACTORS: Dict[str, BaseExtractor] = _collect_extractors()
