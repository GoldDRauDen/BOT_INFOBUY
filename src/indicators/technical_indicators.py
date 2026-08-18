"""Tinh chi bao ky thuat (MA/RSI/MACD/ATR/Volume MA) cho toan bo symbol, upsert bang technical_indicators_daily.

Chay:
    python -m src.indicators.technical_indicators

- Cong thuc TU VIET (pandas, vectorized) - khong dung ta-lib.
- MA20/MA50/MA200 (close), RSI14 (Wilder), MACD (12/26/9 EMA), ATR14 (Wilder), volume_MA20.
- Gia VND nhat quan (nhan 1000 tu OHLCV da luu).
- Chay SAU fetch OHLCV.
"""
import logging
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn, upsert  # noqa: E402

logger = logging.getLogger("technical_indicators")


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Tinh chi bao ky thuat cho 1 symbol (df co cot trade_date, open, high, low, close, volume).

    Tra ve DataFrame da sap xep theo trade_date kem cac cot chi bao.
    """
    out = df.sort_values("trade_date").copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    volume = out["volume"].astype(float)

    # MA
    out["ma20"] = close.rolling(20, min_periods=20).mean()
    out["ma50"] = close.rolling(50, min_periods=50).mean()
    out["ma200"] = close.rolling(200, min_periods=200).mean()

    # RSI14 (Wilder)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out["rsi14"] = 100 - (100 / (1 + rs))
    # Truong hop loss = 0 (RS vo cung) -> RSI = 100
    out.loc[avg_loss == 0, "rsi14"] = 100.0

    # MACD (12/26/9 EMA)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()

    # ATR14 (Wilder)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

    # Volume MA20
    out["volume_ma20"] = volume.rolling(20, min_periods=20).mean()

    return out


def compute_all(conn=None, symbols: Optional[List[str]] = None) -> int:
    """Tinh chi bao cho toan bo symbol co du lieu OHLCV."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        if symbols is None:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM ohlcv_daily ORDER BY symbol"
            ).fetchall()
            symbols = [r["symbol"] for r in rows]

        total = 0
        for sym in symbols:
            df = pd.read_sql_query(
                "SELECT trade_date, open, high, low, close, volume "
                "FROM ohlcv_daily WHERE symbol = ? ORDER BY trade_date",
                conn,
                params=(sym,),
            )
            if df.empty:
                logger.warning("%s: khong co OHLCV - skip", sym)
                continue
            ind = compute_indicators(df)
            rows_out = []
            for _, r in ind.iterrows():
                if pd.isna(r["ma20"]):  # thieu du lieu de tinh -> khong ghi
                    continue
                rows_out.append({
                    "symbol": sym,
                    "trade_date": r["trade_date"],
                    "ma20": r["ma20"],
                    "ma50": r["ma50"] if not pd.isna(r["ma50"]) else None,
                    "ma200": r["ma200"] if not pd.isna(r["ma200"]) else None,
                    "rsi14": r["rsi14"],
                    "macd": r["macd"],
                    "macd_signal": r["macd_signal"],
                    "atr14": r["atr14"],
                    "volume_ma20": r["volume_ma20"],
                })
            if rows_out:
                total += upsert(
                    conn, "technical_indicators_daily", rows_out,
                    conflict_cols=["symbol", "trade_date"],
                )
            logger.info("%s: %d ngay chi bao", sym, len(rows_out))
        logger.info("Tong cong: %d dong chi bao ky thuat", total)
        return total
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        n = compute_all()
        print(f"[technical_indicators] Xong: {n} dong chi bao")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[technical_indicators] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
