"""Unit tests cho historical_ohlcv_fetcher (mock vnstock - khong goi mang that).

Kiem tra: normalize time (UTC -> ngay VN), gia x1000 (nghin VND -> VND),
upsert theo PRIMARY KEY khong trung lap.
"""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.fetcher.historical_ohlcv_fetcher import (  # noqa: E402
    normalize_ohlcv,
    fetch_symbol_ohlcv,
)

from src.db.database import get_conn  # noqa: E402


def _sample_df() -> pd.DataFrame:
    """DataFrame giong KBS: time UTC 07:00, gia nghin VND."""
    return pd.DataFrame({
        "time": pd.to_datetime([
            "2023-01-03 07:00:00",
            "2023-01-04 07:00:00",
            "2023-01-05 07:00:00",
        ]),
        "open": [11.12, 11.54, 11.49],
        "high": [11.47, 11.54, 11.62],
        "low": [11.09, 11.39, 11.44],
        "close": [11.47, 11.44, 11.54],
        "volume": [1547700, 1641400, 2104500],
    })


class TestNormalize:
    """Test chuan hoa DataFrame."""

    def test_time_utc_to_vn_date(self):
        df = normalize_ohlcv(_sample_df())
        # time 07:00 UTC = 14:00 VN cung ngay
        assert df["trade_date"].tolist() == ["2023-01-03", "2023-01-04", "2023-01-05"]

    def test_price_multiplied_by_1000(self):
        df = normalize_ohlcv(_sample_df())
        # Nghin VND -> VND: 11.47 -> 11470.0
        assert df["close"].tolist() == pytest.approx([11470.0, 11440.0, 11540.0])
        assert df["open"].tolist() == pytest.approx([11120.0, 11540.0, 11490.0])

    def test_volume_kept_as_int(self):
        df = normalize_ohlcv(_sample_df())
        assert df["volume"].tolist() == [1547700, 1641400, 2104500]

    def test_empty_input_returns_empty(self):
        assert normalize_ohlcv(pd.DataFrame()).empty
        assert normalize_ohlcv(None).empty

    def test_missing_time_column_returns_empty(self):
        df = pd.DataFrame({"close": [1.0]})
        assert normalize_ohlcv(df).empty


class TestFetchSymbol:
    """Test fetch + upsert (mock Quote.history)."""

    @pytest.fixture
    def conn(self, tmp_path):
        db = get_conn(tmp_path / "test.db")
        yield db
        db.close()

    def test_fetch_new_symbol_inserts_rows(self, conn):
        with patch("src.fetcher.historical_ohlcv_fetcher.Quote") as MockQuote:
            MockQuote.return_value.history.return_value = _sample_df()
            n = fetch_symbol_ohlcv(conn, "ACB", {"delay": 0, "retries": 0}, "2023-01-10")
        assert n == 3
        rows = conn.execute("SELECT COUNT(*) AS c FROM ohlcv_daily WHERE symbol='ACB'").fetchone()
        assert rows["c"] == 3

    def test_upsert_no_duplicate(self, conn):
        with patch("src.fetcher.historical_ohlcv_fetcher.Quote") as MockQuote:
            MockQuote.return_value.history.return_value = _sample_df()
            fetch_symbol_ohlcv(conn, "ACB", {"delay": 0, "retries": 0}, "2023-01-10")
            # Chay lai voi cung du lieu
            fetch_symbol_ohlcv(conn, "ACB", {"delay": 0, "retries": 0}, "2023-01-10")
        rows = conn.execute("SELECT COUNT(*) AS c FROM ohlcv_daily WHERE symbol='ACB'").fetchone()
        assert rows["c"] == 3  # khong trung lap (upsert theo PK symbol+trade_date)

    def test_incremental_fetch_from_last_date_plus_one(self, conn):
        """Da co du lieu -> fetch tu MAX(trade_date)+1 (start truyen vao Quote.history)."""
        with patch("src.fetcher.historical_ohlcv_fetcher.Quote") as MockQuote:
            MockQuote.return_value.history.return_value = _sample_df()
            fetch_symbol_ohlcv(conn, "ACB", {"delay": 0, "retries": 0}, "2023-01-10")
            # lan sau: fetch tu 2023-01-05 + 1 = 2023-01-06
            fetch_symbol_ohlcv(conn, "ACB", {"delay": 0, "retries": 0}, "2023-01-10")
        starts = [c.kwargs["start"] for c in MockQuote.return_value.history.call_args_list]
        assert starts == ["2023-01-01", "2023-01-06"]
        ends = [c.kwargs["end"] for c in MockQuote.return_value.history.call_args_list]
        assert ends == ["2023-01-10", "2023-01-10"]

    def test_empty_history_returns_zero(self, conn):
        with patch("src.fetcher.historical_ohlcv_fetcher.Quote") as MockQuote:
            MockQuote.return_value.history.return_value = pd.DataFrame()
            n = fetch_symbol_ohlcv(conn, "XYZ", {"delay": 0, "retries": 0}, "2023-01-10")
        assert n == 0
