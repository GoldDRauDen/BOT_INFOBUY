"""Unit tests cho backtest engine (du lieu gia da biet - deterministic).

Kiem tra: entry ngay signal, thoát stop-loss/take-profit/het window,
phi 0.3%/chieu + slippage 0.1%, position sizing % von.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.backtest.engine import (  # noqa: E402
    BacktestConfig,
    _apply_slippage,
    _backtest_symbol,
    compute_metrics,
    run_backtest,
)
from src.db.database import get_conn  # noqa: E402


def _price_df(rows):
    """rows: list (date, open, high, low, close)."""
    return pd.DataFrame(
        [{"trade_date": pd.Timestamp(d), "open": o, "high": h, "low": l, "close": c}
         for d, o, h, l, c in rows]
    )


class TestSlippage:
    def test_buy_adds_slippage(self):
        cfg = BacktestConfig()
        assert _apply_slippage(10000.0, "buy", cfg) == pytest.approx(10010.0)

    def test_sell_subtracts_slippage(self):
        cfg = BacktestConfig()
        assert _apply_slippage(10000.0, "sell", cfg) == pytest.approx(9990.0)


class TestBacktestSymbol:
    def test_entry_on_signal_day(self):
        df = _price_df([
            ("2023-01-02", 100, 101, 99, 100),
            ("2023-01-03", 101, 102, 100, 101),  # signal
            ("2023-01-04", 102, 103, 101, 102),
            ("2023-01-05", 103, 104, 102, 103),
        ])
        cfg = BacktestConfig(initial_capital=1_000_000.0, position_size_pct=1.0)
        # Vao lenh ngay 2023-01-03 (gia dong 101 + slippage), ket thuc het window
        trades = _backtest_symbol(df, {pd.Timestamp("2023-01-03")}, cfg)
        assert len(trades) == 1
        t = trades[0]
        assert t["entry_price"] == pytest.approx(101 * 1.001)  # +0.1% slippage
        assert t["exit_reason"] == "end_of_window"
        assert t["exit_date"] == "2023-01-05"

    def test_stop_loss_triggered(self):
        """Gia giam xuong duoi stop (entry -8%) -> thoat stop_loss."""
        entry = 10000.0
        df = _price_df([
            ("2023-01-02", 9900, 10000, 9800, 9950),
            ("2023-01-03", 10000, 10100, 9900, 10000),  # entry
            ("2023-01-04", 9800, 9900, 9100, 9200),     # low < stop (9200)
            ("2023-01-05", 9200, 9300, 9000, 9100),
        ])
        cfg = BacktestConfig(
            initial_capital=1_000_000.0, position_size_pct=1.0,
            stop_loss_pct=0.08, take_profit_pct=0.15,
        )
        trades = _backtest_symbol(df, {pd.Timestamp("2023-01-03")}, cfg)
        assert len(trades) == 1
        t = trades[0]
        assert t["exit_reason"] == "stop_loss"
        # stop = entry_price * 0.92, exit = stop * (1 - slippage 0.1%)
        stop = t["entry_price"] * 0.92
        assert t["exit_price"] == pytest.approx(stop * 0.999)

    def test_take_profit_triggered(self):
        entry = 10000.0
        df = _price_df([
            ("2023-01-02", 9900, 10000, 9800, 9950),
            ("2023-01-03", 10000, 10100, 9900, 10000),  # entry
            ("2023-01-04", 11000, 11600, 10900, 11500), # high > target (entry*1.15)
        ])
        cfg = BacktestConfig(
            initial_capital=1_000_000.0, position_size_pct=1.0,
            stop_loss_pct=0.08, take_profit_pct=0.15,
        )
        trades = _backtest_symbol(df, {pd.Timestamp("2023-01-03")}, cfg)
        assert len(trades) == 1
        t = trades[0]
        assert t["exit_reason"] == "take_profit"
        target = t["entry_price"] * 1.15
        assert t["exit_price"] == pytest.approx(target * 0.999)

    def test_fees_applied_both_sides(self):
        """Phi 0.3%/chieu: fee = buy_cost*0.003 + sell_value*0.003."""
        df = _price_df([
            ("2023-01-02", 10000, 10100, 9900, 10000),
            ("2023-01-03", 10000, 10100, 9900, 10000),  # entry
            ("2023-01-04", 10000, 10050, 9950, 10000),  # flat, exit end
        ])
        cfg = BacktestConfig(
            initial_capital=1_000_000.0, position_size_pct=1.0,
            fee_pct=0.003, slippage_pct=0.0,
        )
        trades = _backtest_symbol(df, {pd.Timestamp("2023-01-03")}, cfg)
        t = trades[0]
        # shares = 1_000_000 / 10000 = 100; buy_cost = 1_000_000
        # sell_value = 10000*100 = 1_000_000; fee = 2 * 1000000 * 0.003 = 6000
        assert t["shares"] == pytest.approx(100.0)
        assert t["pnl"] == pytest.approx(-6000.0)

    def test_no_signal_no_trade(self):
        df = _price_df([
            ("2023-01-02", 100, 101, 99, 100),
            ("2023-01-03", 101, 102, 100, 101),
        ])
        cfg = BacktestConfig()
        assert _backtest_symbol(df, set(), cfg) == []


class TestMetrics:
    def test_win_rate(self):
        trades = [
            {"pnl": 1000.0, "symbol": "A", "entry_date": "d1", "exit_date": "d2",
             "entry_price": 10.0, "exit_price": 11.0, "shares": 100.0,
             "pnl_pct": 0.1, "exit_reason": "take_profit"},
            {"pnl": -500.0, "symbol": "A", "entry_date": "d3", "exit_date": "d4",
             "entry_price": 10.0, "exit_price": 9.5, "shares": 100.0,
             "pnl_pct": -0.05, "exit_reason": "stop_loss"},
        ]
        equity = pd.Series([1000.0, 1000.0], index=pd.to_datetime(["2023-01-01", "2023-01-02"]))
        m = compute_metrics(equity, trades)
        assert m["win_rate"] == pytest.approx(0.5)
        assert m["n_trades"] == 2

    def test_empty_metrics(self):
        m = compute_metrics(pd.Series(dtype=float), [])
        assert m["cagr"] is None
        assert m["n_trades"] == 0


class TestRunBacktest:
    def test_run_backtest_persists_trades(self, tmp_path):
        """run_backtest ghi backtest_runs + backtest_trades vao DB."""
        conn = get_conn(tmp_path / "bt.db")
        # Seed du lieu gia
        rows = []
        dates = pd.date_range("2023-01-02", periods=30, freq="B")
        for i, d in enumerate(dates):
            rows.append((
                "AAA", d.strftime("%Y-%m-%d"), 100.0 + i, 101.0 + i,
                99.0 + i, 100.5 + i, 1_000_000, "KBS"
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv_daily "
            "(symbol, trade_date, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()

        sig = pd.DataFrame({
            "symbol": ["AAA"],
            "trade_date": ["2023-01-03"],
            "strategy_name": ["test"],
            "score": [1.0],
        })
        result = run_backtest(
            symbols=["AAA"], signals_df=sig,
            params={"strategy_name": "test", "initial_capital": 1_000_000.0},
            conn=conn, persist=True,
        )
        assert result["metrics"]["n_trades"] == 1
        runs = conn.execute("SELECT COUNT(*) AS c FROM backtest_runs").fetchone()["c"]
        trades = conn.execute("SELECT COUNT(*) AS c FROM backtest_trades").fetchone()["c"]
        assert runs == 1
        assert trades == 1
        conn.close()
