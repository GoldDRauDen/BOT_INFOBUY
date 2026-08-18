"""Kiem tra tuong quan giua cac ma de xuat (top N) - canh bao rui ro tap trung nganh.

Chay:
    python -m src.risk.correlation_check

- Tinh ma tran tuong quan daily return (60 phien gan nhat) cua top N ma de xuat.
- Neu >50% top N cung 1 nganh -> canh bao "rui ro tap trung nganh".
- In + luu vao metadata cho report.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn  # noqa: E402

logger = logging.getLogger("correlation_check")

LOOKBACK = 60
CONCENTRATION_THRESHOLD = 0.5  # >50% top N cung 1 nganh


def load_top_symbols(conn, top_n: int = 10) -> List[str]:
    """Top N ma co signal moi nhat (theo score cao nhat moi ma)."""
    sql = """
        SELECT symbol, MAX(score) AS score FROM signals
        GROUP BY symbol
        ORDER BY score DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (top_n,)).fetchall()
    return [r["symbol"] for r in rows]


def daily_returns(conn, symbols: List[str], lookback: int = LOOKBACK) -> pd.DataFrame:
    """Ma tran daily return (%) 60 phien gan nhat cho tung symbol."""
    if not symbols:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in symbols)
    df = pd.read_sql_query(
        f"""
        SELECT symbol, trade_date, close FROM (
            SELECT symbol, trade_date, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM ohlcv_daily
            WHERE symbol IN ({placeholders})
        ) WHERE rn <= ?
        """,
        conn,
        params=symbols + [lookback],
    )
    if df.empty:
        return pd.DataFrame()
    pivot = df.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    returns = pivot.pct_change(fill_method=None) * 100.0
    return returns.dropna(how="all")


def sector_of(conn, symbols: List[str]) -> Dict[str, str]:
    """Sector cua tung symbol tu bang symbols."""
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, sector FROM symbols WHERE symbol IN ({placeholders})",
        symbols,
    ).fetchall()
    return {r["symbol"]: r["sector"] for r in rows}


def check_correlation(conn=None, top_n: int = 10) -> Dict[str, Any]:
    """Tinh tuong quan + kiem tra tap trung nganh. Tra ve metadata."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        symbols = load_top_symbols(conn, top_n)
        result: Dict[str, Any] = {
            "symbols": symbols,
            "correlation_matrix": None,
            "avg_correlation": None,
            "concentration_warning": None,
        }
        if len(symbols) < 2:
            logger.warning("It hon 2 ma de xuat - bo qua tinh tuong quan")
            print("[correlation_check] It hon 2 ma de xuat, skip")
            return result

        returns = daily_returns(conn, symbols)
        if returns.empty or len(returns) < 2:
            logger.warning("Khong du du lieu daily return - skip")
            return result

        corr = returns.corr()
        result["correlation_matrix"] = corr.round(4).to_dict()
        vals = corr.values[np.triu_indices_from(corr.values, k=1)]
        avg_corr = float(np.mean(vals)) if len(vals) else None
        result["avg_correlation"] = avg_corr

        # Kiem tra tap trung nganh: >50% top N cung 1 nganh
        sectors = sector_of(conn, symbols)
        known = [s for s in sectors.values() if s]
        if known:
            counts = pd.Series(known).value_counts()
            top_sector, n_same = counts.iloc[0], int(counts.iloc[0])
            if n_same > len(symbols) * CONCENTRATION_THRESHOLD:
                result["concentration_warning"] = (
                    f"RUI RO TAP TRUNG NGANH: {n_same}/{len(symbols)} ma de xuat "
                    f"cung nganh '{counts.index[0]}'"
                )
                logger.warning(result["concentration_warning"])

        print("\n=== CORRELATION CHECK (top %d ma) ===" % len(symbols))
        print("  Symbols:", ", ".join(symbols))
        print(f"  Avg correlation (60 phien): {avg_corr:.4f}" if avg_corr is not None else "  Avg correlation: N/A")
        if result["concentration_warning"]:
            print(f"  {result['concentration_warning']}")
        else:
            print("  Khong co canh bao tap trung nganh")
        return result
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        result = check_correlation()
        meta_path = Path(__file__).parent.parent.parent / "output" / "correlation_check.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[correlation_check] Da luu metadata: {meta_path}")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[correlation_check] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
