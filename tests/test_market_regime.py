"""Unit tests cho market_regime (VNINDEX vs MA200).

Kiem tra: close > MA200 -> BULLISH, close < MA200 -> BEARISH,
thieu du lieu -> unknown.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import src.db.database as database
from src.db.database import get_conn
from src.risk.market_regime import detect_regime


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_regime.db"
    monkeypatch.setattr(database, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(database, "SCHEMA_PATH", Path(__file__).parent.parent / "data" / "schema.sql")
    conn = get_conn()
    yield conn
    conn.close()

def _seed_vnindex(conn, closes):
    """closes: list gia dong VNINDEX theo thu tu ngay tang dan."""
    for i, c in enumerate(closes):
        d = (pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR IGNORE INTO ohlcv_daily (symbol, trade_date, open, high, low, close, volume, source) "
            "VALUES ('VNINDEX', ?, ?, ?, ?, ?, 0, 'KBS')",
            (d, c, c, c, c),
        )
    conn.commit()

class TestDetectRegime:
    def test_bullish(self, tmp_db):
        # 210 ngay = 1.200 -> MA200 = 1200, close cuoi 1500 > MA200
        closes = [1200.0] * 199 + [1500.0] * 1
        _seed_vnindex(tmp_db, closes)
        info = detect_regime(tmp_db)
        assert info["regime"] == "BULLISH"
        assert info["vnindex_close"] == 1500.0

    def test_bearish(self, tmp_db):
        # close cuoi 1000 < MA200 = 1200
        closes = [1200.0] * 200 + [1000.0] * 1
        _seed_vnindex(tmp_db, closes)
        info = detect_regime(tmp_db)
        assert info["regime"] == "BEARISH"

    def test_insufficient_data(self, tmp_db):
        _seed_vnindex(tmp_db, [1200.0] * 50)
        info = detect_regime(tmp_db)
        assert info["regime"] == "unknown"
