"""Walk-forward backtest: windows truot (train 2 nam, test 6 thang, buoc 6 thang).

Chay:
    python -m src.backtest.walk_forward

- Toi thieu 3 windows tren du lieu OHLCV 2023->2026.
- Output: metric trung binh + std giua cac windows.
- Neu std lon (CAGR std > |mean|) -> canh bao ro.
- Ghi run vao DB (strategy_name='wf_rule_based'). KHONG train ML (Phase 5 defer).
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.backtest.engine import run_backtest  # noqa: E402
from src.db.database import get_conn, upsert  # noqa: E402

logger = logging.getLogger("walk_forward")

TRAIN_YEARS = 2
TEST_MONTHS = 6
STEP_MONTHS = 6
MIN_WINDOWS = 3


def build_windows(start: str, end: str) -> List[Dict[str, str]]:
    """Sinh cac window (train_start, train_end, test_start, test_end)."""
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    windows = []
    train_len = pd.DateOffset(years=TRAIN_YEARS)
    test_len = pd.DateOffset(months=TEST_MONTHS)
    step = pd.DateOffset(months=STEP_MONTHS)

    train_start = start_dt
    while True:
        train_end = train_start + train_len
        test_start = train_end
        test_end = test_start + test_len
        if test_end > end_dt:
            break
        windows.append({
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })
        train_start = train_start + step
    return windows


def load_signals(conn, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    """Doc signals tu DB (bo sung cot close/score)."""
    sql = "SELECT symbol, trade_date, strategy_name, score, metadata_json FROM signals"
    conds = []
    params: List[str] = []
    if start:
        conds.append("trade_date >= ?")
        params.append(start)
    if end:
        conds.append("trade_date <= ?")
        params.append(end)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return pd.read_sql_query(sql, conn, params=params)


def run_walk_forward(conn=None, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Chay walk-forward, ghi run vao DB, tra ve tong ket."""
    from src.db.database import get_conn as _get_conn
    own_conn = conn is None
    conn = conn or _get_conn()

    try:
        # Lay pham vi du lieu OHLCV
        row = conn.execute(
            "SELECT MIN(trade_date) AS mn, MAX(trade_date) AS mx FROM ohlcv_daily"
        ).fetchone()
        if not row or not row["mn"] or not row["mx"]:
            logger.error("Khong co du lieu OHLCV - khong chay duoc walk-forward")
            return {"windows": [], "warning": "khong co du lieu"}
        windows = build_windows(row["mn"], row["mx"])
        if len(windows) < MIN_WINDOWS:
            logger.warning(
                "Chi co %d windows (can toi thieu %d) - canh bao du lieu ngan",
                len(windows), MIN_WINDOWS,
            )

        if symbols is None:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM ohlcv_daily WHERE symbol != 'VNINDEX'"
            ).fetchall()
            symbols = [r["symbol"] for r in rows]

        all_signals = load_signals(conn)
        if all_signals.empty:
            logger.warning("Khong co signal nao - walk-forward khong co lenh (van ghi run)")
            all_signals = pd.DataFrame(columns=["symbol", "trade_date", "strategy_name", "score"])

        per_window = []
        for w in windows:
            w_signals = all_signals[
                (all_signals["trade_date"] >= w["test_start"])
                & (all_signals["trade_date"] <= w["test_end"])
            ]
            result = run_backtest(
                symbols=symbols,
                signals_df=w_signals,
                params={
                    "strategy_name": "wf_rule_based",
                    "start_date": w["test_start"],
                    "end_date": w["test_end"],
                },
                conn=conn,
                persist=False,
            )
            trades = result.get("trades", [])
            metrics = result["metrics"]
            per_window.append({
                **w,
                "_trades": trades,
                "cagr": metrics.get("cagr"),
                "sharpe": metrics.get("sharpe"),
                "max_drawdown": metrics.get("max_drawdown"),
                "win_rate": metrics.get("win_rate"),
                "n_trades": metrics.get("n_trades", 0),
                "benchmark_cagr": result["benchmark"].get("cagr"),
            })
            logger.info(
                "Window %s->%s: CAGR=%s Sharpe=%s trades=%d",
                w["test_start"], w["test_end"],
                metrics.get("cagr"), metrics.get("sharpe"), metrics.get("n_trades", 0),
            )

        summary = summarize(per_window)
        run_id = persist_run(conn, windows, per_window, summary)
        # Ghi cac lenh cua tat ca windows vao backtest_trades
        all_trades = [t for w in per_window for t in w.get("_trades", [])]
        if all_trades:
            from src.db.database import upsert as _upsert
            for t in all_trades:
                t["run_id"] = run_id
            _upsert(conn, "backtest_trades", all_trades, conflict_cols=["run_id", "symbol", "entry_date"])
            logger.info("Da ghi %d lenh vao backtest_trades", len(all_trades))
        return summary
    finally:
        if own_conn:
            conn.close()


def summarize(per_window: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tinh mean + std cac metric giua cac windows + canh bao std."""
    import numpy as np

    def agg(key):
        vals = [w.get(key) for w in per_window if w.get(key) is not None]
        if not vals:
            return None, None
        return float(np.mean(vals)), float(np.std(vals))

    cagr_m, cagr_s = agg("cagr")
    sharpe_m, sharpe_s = agg("sharpe")
    dd_m, dd_s = agg("max_drawdown")
    wr_m, wr_s = agg("win_rate")
    n_trades = sum(w.get("n_trades", 0) for w in per_window)

    warning = None
    if cagr_m is not None and cagr_s is not None and abs(cagr_m) > 0 and cagr_s > abs(cagr_m):
        warning = (
            f"CANH BAO: CAGR std ({cagr_s:.4f}) > |mean| ({cagr_m:.4f}) "
            f"- ket qua khong on dinh giua cac windows"
        )
        logger.warning(warning)

    summary = {
        "n_windows": len(per_window),
        "cagr_mean": cagr_m, "cagr_std": cagr_s,
        "sharpe_mean": sharpe_m, "sharpe_std": sharpe_s,
        "max_drawdown_mean": dd_m, "max_drawdown_std": dd_s,
        "win_rate_mean": wr_m, "win_rate_std": wr_s,
        "total_trades": n_trades,
        "warning": warning,
        "windows": per_window,
    }
    print("\n=== WALK-FORWARD SUMMARY ===")
    print(f"  So window: {len(per_window)}")
    print(f"  CAGR   mean={cagr_m} std={cagr_s}")
    print(f"  Sharpe mean={sharpe_m} std={sharpe_s}")
    print(f"  MaxDD  mean={dd_m} std={dd_s}")
    print(f"  WinRate mean={wr_m} std={wr_s}")
    print(f"  Tong lenh: {n_trades}")
    if warning:
        print(f"  {warning}")
    return summary


def persist_run(conn, windows, per_window, summary) -> None:
    """Ghi run vao bang backtest_runs (strategy_name='wf_rule_based')."""
    run_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "run_id": run_id,
        "strategy_name": "wf_rule_based",
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params_json": json.dumps({
            "train_years": TRAIN_YEARS, "test_months": TEST_MONTHS, "step_months": STEP_MONTHS,
            "windows": windows,
        }, ensure_ascii=False),
        "metrics_json": json.dumps(summary, ensure_ascii=False, default=str),
        "benchmark_json": json.dumps({
            "note": "benchmark theo window - xem metrics_json.windows[].benchmark_cagr"
        }, ensure_ascii=False),
    }
    upsert(conn, "backtest_runs", [payload], conflict_cols=["run_id"])
    logger.info("Da ghi run %s (%d windows)", run_id, len(windows))
    return run_id


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        summary = run_walk_forward()
        print(f"\n[walk_forward] Xong: {summary.get('n_windows', 0)} windows")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[walk_forward] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
