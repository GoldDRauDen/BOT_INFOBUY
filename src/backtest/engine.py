"""Backtest engine: event-driven tren ohlcv_daily + signals.

- Vao lenh khi signal xuat hien (gia dong cua phien co signal).
- Thoat khi stop-loss / take-profit / het window.
- Phi giao dich 0.3%/chieu + slippage 0.1% (tren gia vao/ra).
- Position sizing: % von (BacktestConfig.position_size_pct, default 0.2).
- Metrics: CAGR, Sharpe (rf=0), Max Drawdown, Win rate. Benchmark: VNINDEX cung ky.
- Luu backtest_runs + backtest_trades. Ham chinh: run_backtest(symbols, signals_df, params).
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("backtest_engine")


@dataclass
class BacktestConfig:
    """Cau hinh backtest."""
    strategy_name: str = "rule_based"
    initial_capital: float = 1_000_000_000.0  # VND
    position_size_pct: float = 0.2            # % von cho 1 lenh
    fee_pct: float = 0.003                    # phi 0.3%/chieu
    slippage_pct: float = 0.001               # slippage 0.1%
    stop_loss_pct: float = 0.08               # 8%
    take_profit_pct: float = 0.15             # 15%
    start_date: Optional[str] = None          # window con trong (None = theo du lieu)
    end_date: Optional[str] = None


def _apply_slippage(price: float, side: str, cfg: BacktestConfig) -> float:
    """Gia thuc hien = gia ly thuyet +/- slippage (buy +, sell -)."""
    if side == "buy":
        return price * (1 + cfg.slippage_pct)
    return price * (1 - cfg.slippage_pct)


def compute_metrics(
    equity: pd.Series, trades: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Tinh CAGR, Sharpe (rf=0), Max Drawdown, Win rate tu equity hang ngay + danh sach lenh."""
    if equity.empty or len(equity) < 2:
        return {
            "cagr": None, "sharpe": None, "max_drawdown": None,
            "win_rate": None, "total_return": None, "n_trades": len(trades),
        }
    days = (equity.index[-1] - equity.index[0]).days
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    years = max(days / 365.25, 1e-9)
    cagr = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0

    daily_ret = equity.pct_change().dropna()
    sharpe = None
    if len(daily_ret) > 1 and daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252))

    cummax = equity.cummax()
    max_dd = float(((equity - cummax) / cummax).min()) if len(equity) else None

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    win_rate = len(wins) / len(trades) if trades else None

    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "total_return": total_return,
        "n_trades": len(trades),
    }


def run_backtest(
    symbols: List[str],
    signals_df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    conn=None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Chay backtest event-driven.

    signals_df: cot bat buoc (symbol, trade_date, strategy_name). Neu co cot score -> uu tien cao.
    Tra ve dict metrics + benchmark.
    """
    cfg = BacktestConfig(**(params or {}))
    from src.db.database import get_conn as _get_conn  # noqa: F401
    own_conn = conn is None
    conn = conn or _get_conn()

    try:
        run_id = f"bt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")

        trades_all: List[Dict[str, Any]] = []
        for symbol in symbols:
            df_price = pd.read_sql_query(
                "SELECT trade_date, open, high, low, close FROM ohlcv_daily "
                "WHERE symbol = ? ORDER BY trade_date",
                conn,
                params=(symbol,),
            )
            if df_price.empty:
                continue
            df_price["trade_date"] = pd.to_datetime(df_price["trade_date"])
            if cfg.start_date:
                df_price = df_price[df_price["trade_date"] >= cfg.start_date]
            if cfg.end_date:
                df_price = df_price[df_price["trade_date"] <= cfg.end_date]

            sym_signals = signals_df[signals_df["symbol"] == symbol].copy()
            if sym_signals.empty:
                continue
            sym_signals["trade_date"] = pd.to_datetime(sym_signals["trade_date"])
            if cfg.start_date:
                sym_signals = sym_signals[sym_signals["trade_date"] >= cfg.start_date]
            if cfg.end_date:
                sym_signals = sym_signals[sym_signals["trade_date"] <= cfg.end_date]
            # Ngay signal: bo cac signal trung ngay, chon score cao nhat
            if "score" in sym_signals.columns:
                sym_signals = (
                    sym_signals.sort_values("score", ascending=False)
                    .drop_duplicates("trade_date")
                    .sort_values("trade_date")
                )
            else:
                sym_signals = sym_signals.drop_duplicates("trade_date")
            signal_dates = set(pd.to_datetime(sym_signals["trade_date"]))

            trades_all.extend(
                _backtest_symbol(df_price, signal_dates, cfg, symbol)
            )

        # Equity curve: tinh tu cac lenh dong (mo hinh don gian, khong margin)
        equity = _build_equity(df_price_all=None, trades=trades_all, cfg=cfg, symbols=symbols, conn=conn)
        metrics = compute_metrics(equity, trades_all)

        # Benchmark: VNINDEX cung ky
        benchmark = compute_benchmark(conn, cfg)
        result = {
            "run_id": run_id,
            "metrics": metrics,
            "benchmark": benchmark,
            "trades": trades_all,
            "params": params or {},
        }

        if persist:
            ended_at = time.strftime("%Y-%m-%d %H:%M:%S")
            upsert_runs = _upsert_run(conn, run_id, cfg, metrics, benchmark, started_at, ended_at)
            logger.info("Backtest run %s: %s", run_id, upsert_runs)
            for t in trades_all:
                t["run_id"] = run_id
            from src.db.database import upsert as _upsert
            if trades_all:
                _upsert(conn, "backtest_trades", trades_all, conflict_cols=["run_id", "symbol", "entry_date"])
        return result
    finally:
        if own_conn:
            conn.close()


def _backtest_symbol(
    df_price: pd.DataFrame, signal_dates: set, cfg: BacktestConfig, symbol: str = ""
) -> List[Dict[str, Any]]:
    """Simulate 1 symbol: vao lenh ngay signal (gia dong), thoat theo SL/TP/het du lieu."""
    trades: List[Dict[str, Any]] = []
    # Vi tri lenh dang mo theo ngay vao
    open_pos: Dict[str, Dict[str, Any]] = {}

    prices = df_price.set_index("trade_date")
    for idx, row in df_price.iterrows():
        date = row["trade_date"]
        close = float(row["close"])
        if date in signal_dates and date not in open_pos:
            # Vao lenh: gia dong + slippage, phi
            entry_price = _apply_slippage(close, "buy", cfg)
            open_pos[date] = {
                "symbol": symbol,
                "entry_date": date.strftime("%Y-%m-%d"),
                "entry_price": entry_price,
                "stop": entry_price * (1 - cfg.stop_loss_pct),
                "target": entry_price * (1 + cfg.take_profit_pct),
            }
            continue

        # Kiem tra lenh dang mo: thoat theo gia phien nay
        for entry_date, pos in list(open_pos.items()):
            high, low = float(row["high"]), float(row["low"])
            exit_price = None
            reason = None
            if low <= pos["stop"]:
                exit_price = _apply_slippage(pos["stop"], "sell", cfg)
                reason = "stop_loss"
            elif high >= pos["target"]:
                exit_price = _apply_slippage(pos["target"], "sell", cfg)
                reason = "take_profit"
            if exit_price is not None:
                _close_trade(trades, pos, exit_price, date, reason, cfg)
                del open_pos[entry_date]

    # Het window: dong cac lenh con mo theo gia cuoi
    if prices.empty:
        return trades
    last_date = prices.index[-1]
    last_close = float(prices.iloc[-1]["close"])
    for entry_date, pos in list(open_pos.items()):
        exit_price = _apply_slippage(last_close, "sell", cfg)
        _close_trade(trades, pos, exit_price, last_date, "end_of_window", cfg)

    return trades


def _close_trade(trades, pos, exit_price, exit_date, reason, cfg):
    """Ghi nhan lenh dong (gom phi ca 2 chieu)."""
    entry_price = pos["entry_price"]
    shares = cfg.initial_capital * cfg.position_size_pct / entry_price
    buy_cost = entry_price * shares
    sell_value = exit_price * shares
    fees = buy_cost * cfg.fee_pct + sell_value * cfg.fee_pct
    pnl = sell_value - buy_cost - fees
    pnl_pct = pnl / buy_cost if buy_cost else 0.0
    trades.append({
        "symbol": pos.get("symbol", ""),
        "entry_date": pos["entry_date"],
        "exit_date": exit_date.strftime("%Y-%m-%d"),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": reason,
    })


def _build_equity(df_price_all, trades, cfg, symbols, conn) -> pd.Series:
    """Duong cong von: bat dau tu initial_capital, cong/trừ pnl khi lenh dong."""
    if not trades:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(sorted({t["exit_date"] for t in trades}))
    capital = cfg.initial_capital
    by_date: Dict[pd.Timestamp, float] = {}
    for t in trades:
        capital += t["pnl"]
        by_date[pd.Timestamp(t["exit_date"])] = capital
    idx = pd.DatetimeIndex(sorted(by_date.keys()))
    return pd.Series([by_date[d] for d in idx], index=idx)


def compute_benchmark(conn, cfg: BacktestConfig) -> Dict[str, Any]:
    """Benchmark VNINDEX cung ky (CAGR, MaxDD, total return)."""
    df = pd.read_sql_query(
        "SELECT trade_date, close FROM ohlcv_daily "
        "WHERE symbol = 'VNINDEX' ORDER BY trade_date",
        conn,
    )
    if df.empty or len(df) < 2:
        return {"cagr": None, "max_drawdown": None, "total_return": None, "note": "khong co du lieu VNINDEX"}
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if cfg.start_date:
        df = df[df["trade_date"] >= cfg.start_date]
    if cfg.end_date:
        df = df[df["trade_date"] <= cfg.end_date]
    if len(df) < 2:
        return {"cagr": None, "max_drawdown": None, "total_return": None, "note": "window qua ngan"}
    close = df["close"].astype(float)
    total_return = close.iloc[-1] / close.iloc[0] - 1
    days = (df["trade_date"].iloc[-1] - df["trade_date"].iloc[0]).days
    years = max(days / 365.25, 1e-9)
    cagr = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0
    cummax = close.cummax()
    max_dd = float(((close - cummax) / cummax).min())
    return {"cagr": cagr, "max_drawdown": max_dd, "total_return": total_return}


def _upsert_run(conn, run_id, cfg, metrics, benchmark, started_at, ended_at) -> int:
    """Ghi 1 dong backtest_runs."""
    from src.db.database import upsert
    return upsert(conn, "backtest_runs", [{
        "run_id": run_id,
        "strategy_name": cfg.strategy_name,
        "started_at": started_at,
        "ended_at": ended_at,
        "params_json": json.dumps({k: v for k, v in cfg.__dict__.items() if k != "strategy_name"}, ensure_ascii=False),
        "metrics_json": json.dumps(metrics, ensure_ascii=False),
        "benchmark_json": json.dumps(benchmark, ensure_ascii=False),
    }], conflict_cols=["run_id"])
