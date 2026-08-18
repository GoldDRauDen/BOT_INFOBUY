"""Fetch OHLCV lich su cho toan bo ma HOSE + VNINDEX tu KBS, upsert vao ohlcv_daily.

Chay:
    python -m src.fetcher.historical_ohlcv_fetcher

- Incremental: moi symbol chi fetch tu MAX(trade_date)+1 den hom nay; chua co -> tu 2023-01-01.
- Normalize time (UTC) -> trade_date YYYY-MM-DD (gio VN UTC+7).
- QUYET DINH: luu gia DON VI VND (nhan 1000 tu nghin VND cua KBS) de nhat quan voi
  send_telegram va report. Volume don vi co phieu.
- Throttle 0.5s/ma + retry 2. Loi tung ma -> log skip, KHONG dung pipeline.
"""
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from vnstock.api.quote import Quote

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn, upsert  # noqa: E402
from src.utils.config_loader import load_settings  # noqa: E402

logger = logging.getLogger("historical_ohlcv_fetcher")

SOURCE = "KBS"
FALLBACK_SOURCE = "MSN"  # chi ~1 nam lich su - fallback khi KBS loi
START_DATE = "2023-01-01"


def _throttle_retry(settings: Dict) -> Dict:
    fetcher_cfg = settings.get("fetcher", {})
    return {
        "delay": float(fetcher_cfg.get("request_delay", 0.5)),
        "retries": int(fetcher_cfg.get("retries", 2)),
    }


def _fetch_history(quote: Quote, symbol: str, start: str, end: str, source: str) -> pd.DataFrame:
    """Goi Quote.history voi retry. Tra ve DataFrame rong neu loi."""
    last_err = None
    for attempt in range(3):
        try:
            df = quote.history(symbol=symbol, start=start, end=end, interval="1D")
            if df is None:
                return pd.DataFrame()
            return df
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 2:
                time.sleep(1.0)
    logger.warning("Loi fetch %s tu %s: %s", symbol, source, last_err)
    return pd.DataFrame()


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Chuan hoa DataFrame KBS -> cot (symbol, trade_date, open..close VND, volume).

    - time datetime UTC -> trade_date YYYY-MM-DD (gio VN = UTC+7, neu >23h tinh ngay sau).
    - Gia x1000: nghin VND -> VND.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # Cot co the la 'time' (KBS) hoac 'tradingDate' (MSN)
    time_col = "time" if "time" in out.columns else "tradingDate"
    if time_col not in out.columns:
        logger.warning("Thieu cot thoi gian (%s) - bo qua", time_col)
        return pd.DataFrame()
    t = pd.to_datetime(out[time_col])
    # UTC -> gio VN
    t = t + pd.Timedelta(hours=7)
    out["trade_date"] = t.dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce") * 1000.0
    if "volume" in out.columns:
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype("int64")
    keep = ["trade_date", "open", "high", "low", "close", "volume"]
    return out[[c for c in keep if c in out.columns]].dropna(subset=["close"])


def _last_date(conn, symbol: str) -> Optional[str]:
    """Ngay giao dich cuoi cung cua symbol trong DB (None neu chua co)."""
    row = conn.execute(
        "SELECT MAX(trade_date) AS d FROM ohlcv_daily WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["d"] if row and row["d"] else None


def fetch_symbol_ohlcv(conn, symbol: str, cfg: Dict, today: str) -> int:
    """Fetch + upsert OHLCV cho 1 symbol. Tra ve so dong ghi duoc."""
    last = _last_date(conn, symbol)
    if last:
        start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        if start > today:
            logger.info("%s: da cap nhat den %s, skip", symbol, last)
            return 0
    else:
        start = START_DATE

    try:
        quote = Quote(symbol=symbol, source=SOURCE)
    except Exception as e:  # noqa: BLE001 - symbol khong hop le (delisted)
        logger.warning("%s: symbol khong hop le tu %s (%s) - skip", symbol, SOURCE, e)
        return 0
    df = _fetch_history(quote, symbol, start, today, SOURCE)
    if df.empty:
        # Fallback MSN (chi ~1 nam) - ghi ro gioi han
        try:
            quote2 = Quote(symbol=symbol, source=FALLBACK_SOURCE)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s: symbol khong hop le tu %s (%s) - skip", symbol, FALLBACK_SOURCE, e)
            return 0
        df = _fetch_history(quote2, symbol, start, today, FALLBACK_SOURCE)
        if df.empty:
            logger.warning("%s: khong co du lieu tu %s/%s - skip", symbol, SOURCE, FALLBACK_SOURCE)
            return 0
        logger.info("%s: lay tu fallback %s (gioi han ~1 nam)", symbol, FALLBACK_SOURCE)
        src_used = FALLBACK_SOURCE
    else:
        src_used = SOURCE

    norm = normalize_ohlcv(df)
    if norm.empty:
        logger.warning("%s: du lieu rong sau normalize - skip", symbol)
        return 0

    rows = [
        {
            "symbol": symbol,
            "trade_date": r["trade_date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
            "source": src_used,
        }
        for _, r in norm.iterrows()
    ]
    written = upsert(conn, "ohlcv_daily", rows, conflict_cols=["symbol", "trade_date"])
    logger.info("%s: +%d dong (tu %s)", symbol, written, start)
    return written


def fetch_all(conn=None, symbols: Optional[List[str]] = None) -> int:
    """Fetch OHLCV cho danh sach symbol (mac dinh: toan bo HOSE + VNINDEX tu DB)."""
    settings = load_settings()
    cfg = _throttle_retry(settings)
    own_conn = conn is None
    conn = conn or get_conn()

    try:
        if symbols is None:
            rows = conn.execute(
                "SELECT symbol FROM symbols WHERE exchange = 'HOSE' AND is_active = 1"
            ).fetchall()
            symbols = [r["symbol"] for r in rows]
        # VNINDEX benchmark luon co trong DB de so sanh
        if "VNINDEX" not in symbols:
            symbols = symbols + ["VNINDEX"]

        today = date.today().strftime("%Y-%m-%d")
        total = 0
        for sym in symbols:
            try:
                total += fetch_symbol_ohlcv(conn, sym, cfg, today)
            except Exception as e:  # noqa: BLE001
                logger.error("%s: loi khong mong doi (khong dung pipeline): %s", sym, e)
            time.sleep(cfg["delay"])
        logger.info("Tong cong: %d dong OHLCV da ghi", total)
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
        n = fetch_all()
        print(f"[historical_ohlcv_fetcher] Xong: {n} dong da ghi")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[historical_ohlcv_fetcher] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
