"""Fetch danh sach ma chung khoan + nganh (ICB) tu KBS, upsert vao bang symbols.

Chay:
    python -m src.fetcher.symbol_fetcher

- Listing.symbols_by_exchange() -> loc type=='stock'.
- Listing.symbols_by_industries() -> join sector/industry_code.
- Ma khong co nganh -> sector rong (khong bia so lieu).
"""
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List

from vnstock.api.listing import Listing

# Them thu muc goc vao sys.path de import src.* (khi chay truc tiep bang python -m)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn, upsert  # noqa: E402
from src.utils.config_loader import load_settings  # noqa: E402

logger = logging.getLogger("symbol_fetcher")

SOURCE = "KBS"


def _throttle_retry(settings: Dict) -> Dict:
    """Lay cau hinh throttle + retry tu config/settings.yaml (muc fetcher)."""
    fetcher_cfg = settings.get("fetcher", {})
    return {
        "delay": float(fetcher_cfg.get("request_delay", 0.5)),
        "retries": int(fetcher_cfg.get("retries", 2)),
    }


def _call_with_retry(fn, retries: int, *args, **kwargs):
    """Goi ham vnstock voi retry; loi cuoi cung -> raise."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - bat moi loi mang/parse
            last_err = e
            if attempt < retries:
                time.sleep(1.0)
    raise last_err


def fetch_symbols(conn=None, settings: Dict | None = None) -> int:
    """Fetch danh sach ma + nganh, upsert vao bang symbols.

    Tra ve so ma da ghi.
    """
    settings = settings or load_settings()
    cfg = _throttle_retry(settings)
    own_conn = conn is None
    conn = conn or get_conn()

    try:
        listing = Listing(source=SOURCE)
        # 1. Danh sach ma + san
        df_ex = _call_with_retry(
            listing.symbols_by_exchange, cfg["retries"]
        )
        if df_ex is None or df_ex.empty or "symbol" not in df_ex.columns:
            logger.error("symbols_by_exchange tra ve rong hoac sai schema - skip")
            return 0
        df_stock = df_ex[df_ex["type"] == "stock"] if "type" in df_ex.columns else df_ex
        logger.info("Listing: %d ma stock (HOSE/HNX/UPCOM)", len(df_stock))

        # 2. Nganh ICB
        sector_map: Dict[str, Dict[str, str]] = {}
        try:
            df_ind = _call_with_retry(
                listing.symbols_by_industries, cfg["retries"]
            )
            if df_ind is not None and not df_ind.empty and "symbol" in df_ind.columns:
                for _, row in df_ind.iterrows():
                    sector_map[row["symbol"]] = {
                        "sector": row.get("industry_name"),
                        "industry_code": row.get("industry_code"),
                    }
                logger.info("Nganh ICB: %d ma", len(sector_map))
            else:
                logger.warning("symbols_by_industries tra ve rong - sector de trong")
        except Exception as e:  # noqa: BLE001
            logger.warning("Loi lay nganh ICB (khong fail): %s", e)

        # 3. Upsert
        rows = []
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for _, r in df_stock.iterrows():
            sym = str(r["symbol"]).strip().upper()
            if not sym:
                continue
            ind = sector_map.get(sym, {})
            rows.append({
                "symbol": sym,
                "organ_name": r.get("organ_name"),
                "en_organ_name": r.get("en_organ_name"),
                "exchange": r.get("exchange"),
                "sector": ind.get("sector"),
                "industry_code": ind.get("industry_code"),
                "is_active": 1,
                "updated_at": now,
            })

        written = upsert(conn, "symbols", rows, conflict_cols=["symbol"])
        logger.info("Upsert symbols: %d ma", written)
        return written
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        n = fetch_symbols()
        print(f"[symbol_fetcher] Xong: {n} ma da upsert")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[symbol_fetcher] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
