"""Unit tests cho paper portfolio manager (deterministic, khong goi mang).

Kiem tra: cat lo, tai can bang top N, tinh PnL/cash dung, ghi trade.
Dung DB tam (tmp_path) de khong lam ban data/bot_buy.db that.
"""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import src.db.database as database
from src.db.database import get_conn, init_db
import src.portfolio.manager as manager


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """DB tam + patch DEFAULT_DB_PATH/SCHEMA_PATH/OUTPUT_PATH cua modules."""
    db_path = tmp_path / "test_portfolio.db"
    monkeypatch.setattr(database, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(database, "SCHEMA_PATH", Path(__file__).parent.parent / "data" / "schema.sql")
    monkeypatch.setattr(manager, "OUTPUT_PATH", tmp_path / "portfolio_report.json")
    conn = get_conn()
    yield conn
    conn.close()

class FakeDT(datetime):
    """Fake datetime.now -> 2026-08-17 (thu 2) - deterministic rebalance test."""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 17, 9, 0)


def _seed(conn):
    """Ghi du lieu mau: 12 symbol co signal + gia va vi the san cho 2 symbol."""
    # Schema da duoc tao boi get_conn()
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH",
               "III", "JJJ", "KKK", "LLL"]
    # symbols + signals
    for i, sym in enumerate(symbols):
        conn.execute("INSERT OR IGNORE INTO symbols (symbol, exchange, is_active) VALUES (?, 'HOSE', 1)", (sym,))
        conn.execute(
            "INSERT OR IGNORE INTO signals (symbol, trade_date, strategy_name, score, metadata_json) "
            "VALUES (?, '2026-08-17', 'safe_quality_growth', ?, '{}')",
            (sym, float(12 - i)),  # AAA cao nhat, LLL thap nhat
        )
    # OHLCV: gia = 10000 + i*100 cho moi symbol (ngay 2026-08-17 moi nhat)
    for i, sym in enumerate(symbols):
        conn.execute(
            "INSERT OR IGNORE INTO ohlcv_daily (symbol, trade_date, open, high, low, close, volume, source) "
            "VALUES (?, '2026-08-17', 10000, 10000, 10000, ?, 1000000, 'KBS')",
            (sym, 10000 + i * 100),
        )
    conn.commit()


class TestCashBalance:
    def test_cash_initial(self, tmp_db):
        assert manager._cash_balance(tmp_db) == manager.VON_DAU_TU

    def test_cash_after_buy_sell(self, tmp_db):
        manager._buy(tmp_db, "AAA", "2026-08-17", 10000.0, "init_entry")
        cash1 = manager._cash_balance(tmp_db)
        assert cash1 == pytest.approx(manager.VON_DAU_TU - 10_000_000.0)
        manager._sell(tmp_db, "AAA", "2026-08-17", 10500.0, "test")
        cash2 = manager._cash_balance(tmp_db)
        assert cash2 == pytest.approx(manager.VON_DAU_TU + 10500.0 * (10_000_000.0 / 10000.0) - 10_000_000.0)


class TestBuySell:
    def test_buy_creates_position(self, tmp_db):
        manager._buy(tmp_db, "AAA", "2026-08-17", 10000.0, "init_entry")
        pos = tmp_db.execute(
            "SELECT * FROM portfolio_positions WHERE symbol='AAA' AND status='active'"
        ).fetchone()
        assert pos is not None
        assert pos["shares"] == pytest.approx(1000.0)  # 10tr / 10k
        assert pos["stop_price"] == pytest.approx(9200.0)  # -8%

    def test_sell_closes_position(self, tmp_db):
        manager._buy(tmp_db, "AAA", "2026-08-17", 10000.0, "init_entry")
        manager._sell(tmp_db, "AAA", "2026-08-17", 9500.0, "stop_loss")
        pos = tmp_db.execute(
            "SELECT * FROM portfolio_positions WHERE symbol='AAA' AND status='active'"
        ).fetchone()
        assert pos is None
        trades = tmp_db.execute(
            "SELECT * FROM portfolio_trades WHERE symbol='AAA' ORDER BY id"
        ).fetchall()
        assert [t["action"] for t in trades] == ["buy", "sell"]
        assert trades[-1]["reason"] == "stop_loss"

    def test_buy_insufficient_cash_skips(self, tmp_db):
        # Mua 2 lan de tran tien mat - lan 2 voi tien > VON_DAU_TU
        manager._buy(tmp_db, "AAA", "2026-08-17", 10000.0, "init_entry")
        manager._buy(tmp_db, "BBB", "2026-08-17", 10000.0, "new_entry")  # van du 90tr
        cash = manager._cash_balance(tmp_db)
        assert cash == pytest.approx(manager.VON_DAU_TU - 20_000_000.0)


class TestRunDaily:
    def test_rebalance_full(self, tmp_db, monkeypatch):
        """Thu 2: mua 10 ma top dau, khong ban ai (chua co vi the)."""
        _seed(tmp_db)
        monkeypatch.setattr(manager, "datetime", FakeDT)  # force thu 2
        summary = manager.run_daily(tmp_db)
        assert summary["trade_date"] == "2026-08-17"
        assert len(summary["positions"]) == 10
        # 10 ma co diem cao nhat: AAA den JJJ
        top_syms = {p["symbol"] for p in summary["positions"]}
        assert "AAA" in top_syms and "JJJ" in top_syms
        assert "LLL" not in top_syms
        # PnL = 0 vi gia vao = gia hien tai
        assert summary["pnl"] == pytest.approx(0.0, abs=1.0)

    def test_stop_loss_exit(self, tmp_db, monkeypatch):
        """Gia giam duoi stop_price -> ban het vi the do."""
        _seed(tmp_db)
        monkeypatch.setattr(manager, "datetime", FakeDT)
        # Mua AAA truoc voi gia cao (stop thap) roi ha gia xuong duoi stop
        manager._buy(tmp_db, "AAA", "2026-08-10", 10000.0, "init_entry")  # stop 9200
        # Gia moi nhat cua AAA trong seed la 10000 -> chua cham stop
        summary = manager.run_daily(tmp_db)
        assert any(p["symbol"] == "AAA" for p in summary["positions"])
        # Ha gia AAA xuong 9000 (duoi stop 9200)
        tmp_db.execute(
            "UPDATE ohlcv_daily SET close=9000, high=9000, low=9000, open=9000 "
            "WHERE symbol='AAA' AND trade_date='2026-08-17'"
        )
        tmp_db.commit()
        summary2 = manager.run_daily(tmp_db)
        assert all(p["symbol"] != "AAA" for p in summary2["positions"])
        sell = tmp_db.execute(
            "SELECT * FROM portfolio_trades WHERE symbol='AAA' AND action='sell' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert sell["reason"] == "stop_loss"
        assert sell["price"] == pytest.approx(9000.0)
