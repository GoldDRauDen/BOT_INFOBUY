"""Kiem tra trang thai thi truong (regime) qua VNINDEX vs MA200.

Chay:
    python -m src.risk.market_regime

- VNINDEX > MA200 -> BULLISH (thi truong khoe, co the de xuat mua).
- VNINDEX < MA200 -> BEARISH (thi truong yeu - canh bao, giam muc goi y).
- Luu output/market_regime.json de send_telegram.py doc.
"""
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn  # noqa: E402

logger = logging.getLogger("market_regime")

MA_WINDOW = 200
OUTPUT_PATH = Path(__file__).parent.parent.parent / "output" / "market_regime.json"

def detect_regime(conn) -> dict:
    """Tinh regime hien tai tu VNINDEX (OHLCV moi nhat)."""
    df = pd.read_sql_query(
        """
        SELECT trade_date, close FROM ohlcv_daily
        WHERE symbol = 'VNINDEX'
        ORDER BY trade_date
        """,
        conn,
    )
    if df.empty:
        return {"trade_date": None, "vnindex_close": None, "ma200": None, "regime": "unknown"}
    df["ma200"] = df["close"].rolling(MA_WINDOW, min_periods=MA_WINDOW).mean()
    last = df.iloc[-1]
    close = float(last["close"])
    ma200 = float(last["ma200"]) if not np.isnan(last["ma200"]) else None
    if ma200 is None:
        return {"trade_date": last["trade_date"], "vnindex_close": close, "ma200": None,
                "regime": "unknown"}
    regime = "BULLISH" if close > ma200 else "BEARISH"
    return {
        "trade_date": last["trade_date"],
        "vnindex_close": round(close, 2),
        "ma200": round(ma200, 2),
        "regime": regime,
    }

def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    conn = get_conn()
    try:
        info = detect_regime(conn)
        print(f"[market_regime] VNINDEX={info['vnindex_close']} "
              f"MA200={info['ma200']} regime={info['regime']}")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
