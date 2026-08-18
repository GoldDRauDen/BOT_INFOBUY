"""Fetch chi so co ban + bao cao thu nhap (quarterly) tu KBS, upsert fundamentals_quarterly.

Chay:
    python -m src.fetcher.fundamental_fetcher

- quarter_end = ten cot ky ('2026-Q2') -> ngay cuoi quy (YYYY-MM-DD).
- KHONG CO reported_date tu nguon (community edition) -> uoc luong:
  reported_date = quarter_end + 20 ngay, is_estimated = 1 (quy tac spec).
- net_income lay tu income_statement (item_id='net_profit', don vi VND).
- GIOI HAN: community edition chi 8 ky (2 nam).
"""
import logging
import re
import sys
import time
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from vnstock.api.financial import Finance

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn, upsert  # noqa: E402
from src.utils.config_loader import load_settings  # noqa: E402

logger = logging.getLogger("fundamental_fetcher")

SOURCE = "KBS"
RATIO_ITEMS = {
    "eps": "trailing_eps",
    "pe_ratio": "pe_ratio",
    "pb_ratio": "pb_ratio",
    "roe": "roe",
    "roa": "roa",
    "bvps": "book_value_per_share_bvps",
    "dividend_yield": "dividend_yield",
}


def quarter_key_to_date(quarter_key: str) -> Optional[str]:
    """Chuyen '2026-Q2' -> '2026-06-30' (ngay cuoi quy). None neu sai format."""
    m = re.match(r"^(\d{4})-Q([1-4])$", str(quarter_key).strip())
    if not m:
        return None
    year = int(m.group(1))
    q = int(m.group(2))
    month = q * 3
    return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


def _extract_series(df: pd.DataFrame, item_id: str) -> Dict[str, float]:
    """Lay chuoi gia tri cua 1 item theo ky tu DataFrame dang item/item_id/<ky>.

    Xu ly quirk ky trung lap (vd '2025-Q4_1'): uu tien cot khong co suffix.
    """
    if df is None or df.empty or "item_id" not in df.columns:
        return {}
    match = df[df["item_id"] == item_id]
    if match.empty:
        return {}
    row = match.iloc[0]
    out: Dict[str, float] = {}
    for col in df.columns:
        if col in ("item", "item_id"):
            continue
        key = str(col)
        # Bo suffix '_1' cua ky trung lap
        base_key = re.sub(r"_\d+$", "", key)
        val = row.get(col)
        if val is None or pd.isna(val):
            continue
        try:
            out[base_key] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def fetch_symbol_fundamentals(conn, symbol: str) -> int:
    """Fetch + upsert fundamentals cho 1 symbol. Tra ve so ky ghi duoc."""
    fin = Finance(symbol=symbol, source=SOURCE)
    ratio_df = fin.ratio(period="quarterly", lang="vi")
    if ratio_df is None or ratio_df.empty or "item_id" not in ratio_df.columns:
        logger.warning("%s: ratio tra ve rong - skip", symbol)
        return 0

    inc_df = None
    try:
        inc_df = fin.income_statement(period="quarterly", lang="vi")
    except Exception as e:  # noqa: BLE001
        logger.warning("%s: loi income_statement (khong fail): %s", symbol, e)

    net_income_map = _extract_series(inc_df, "net_profit") if inc_df is not None else {}
    revenue_map = {}
    if inc_df is not None and "item_id" in inc_df.columns:
        rev_row = inc_df[inc_df["item_id"] == "total_revenue"]
        if rev_row.empty:
            # Mot so doanh nghiep dung 'total_revenue_from_sales_and_service_provision'
            rev_row = inc_df[inc_df["item_id"] == "total_revenue_from_sales_and_service_provision"]
        if not rev_row.empty:
            revenue_map = _extract_series(inc_df, rev_row.iloc[0]["item_id"])

    maps = {field: _extract_series(ratio_df, item_id) for field, item_id in RATIO_ITEMS.items()}

    # Tap hop cac ky xuat hien o bat ky map nao
    quarter_keys: set = set()
    for m in maps.values():
        quarter_keys.update(m.keys())
    quarter_keys.update(net_income_map.keys())

    rows = []
    for qk in sorted(quarter_keys):
        qe = quarter_key_to_date(qk)
        if qe is None:
            logger.warning("%s: ky khong hop le '%s' - bo qua", symbol, qk)
            continue
        reported = (date.fromisoformat(qe) + timedelta(days=20)).isoformat()
        rows.append({
            "symbol": symbol,
            "quarter_end": qe,
            "reported_date": reported,
            "is_estimated": 1,
            "eps": maps["eps"].get(qk),
            "pe_ratio": maps["pe_ratio"].get(qk),
            "pb_ratio": maps["pb_ratio"].get(qk),
            "roe": maps["roe"].get(qk),
            "roa": maps["roa"].get(qk),
            "bvps": maps["bvps"].get(qk),
            "revenue": revenue_map.get(qk),
            "net_income": net_income_map.get(qk),
            "dividend_yield": maps["dividend_yield"].get(qk),
        })
        time.sleep(0.1)  # tranh spam trong 1 symbol

    written = upsert(conn, "fundamentals_quarterly", rows, conflict_cols=["symbol", "quarter_end"])
    logger.info("%s: %d ky da ghi", symbol, written)
    return written


def fetch_all(conn=None, symbols: Optional[List[str]] = None) -> int:
    """Fetch fundamentals cho danh sach symbol (mac dinh: toan bo ma active trong DB)."""
    settings = load_settings()
    own_conn = conn is None
    conn = conn or get_conn()

    try:
        if symbols is None:
            # Chi fetch cho symbol CO OHLCV (co the screening/backtest) - ghi ro gioi han
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM ohlcv_daily WHERE symbol != 'VNINDEX' "
                "ORDER BY symbol"
            ).fetchall()
            symbols = [r["symbol"] for r in rows]
            logger.info(
                "Pham vi fundamentals: %d symbol co du lieu OHLCV "
                "(symbol khong co gia khong the screening - bo qua)", len(symbols)
            )

        total = 0
        for sym in symbols:
            try:
                total += fetch_symbol_fundamentals(conn, sym)
            except Exception as e:  # noqa: BLE001
                logger.error("%s: loi khong mong doi (khong dung pipeline): %s", sym, e)
            time.sleep(0.5)
        logger.info("Tong cong: %d ky fundamentals da ghi", total)
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
        print(f"[fundamental_fetcher] Xong: {n} ky da ghi")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[fundamental_fetcher] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
