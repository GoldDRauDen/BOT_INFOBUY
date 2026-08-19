"""Quan ly danh muc mau (paper portfolio) - de xuat an toan cho giam doc.

Chay:
    python -m src.portfolio.manager

Cau hinh an toan (da chot) - khong doi neu khong co ly do:
  - VON_DAU_TU = 100,000,000 VND (von gia dinh de mo phong)
  - TOI_DA_MA  = 10 ma
  - TRONG_SO   = 10% von/ma (an toan hon muc toi da 15% da chot)
  - CAT_LO     = 8% (gia giam 8% so gia vao -> ban ngay hom do)
  - TAI CAN BANG: thu 2 hang tuan (giat ma khong con trong top, mua ma moi)

Logic hang ngay (chay trong job screening-and-report):
  1. Doc gia moi nhat (close) cua vi the + top signal.
  2. Kiem tra cat lo: close <= stop_price -> ban toan bo (reason=stop_loss).
  3. Tien hanh tai can bang theo top N moi nhat tu signals.
  4. Ghi portfolio_positions + portfolio_trades + output/portfolio_report.json.
  Khong dung tien that - chi mo phong de do luong hieu qua truoc khi tin tuong.

Ponytail: dung co phieu le (shares that), khong lam tron theo board lot 100 - paper
trading khong can; khi chuyen sang lenh that, them buoc round ve boi so lot + phi.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn  # noqa: E402
from src.risk.correlation_check import load_top_symbols  # noqa: E402

logger = logging.getLogger("portfolio_manager")

VON_DAU_TU = 100_000_000.0          # 100 trieu VND von gia dinh
TOI_DA_MA = 10                      # toi da 10 vi the
TRONG_SO = 10.0                     # % von cho moi ma (toi da 15% da chot)
CAT_LO_PCT = 8.0                    # cat lo 8% so gia vao
REBALANCE_DOW = 0                   # thu 2 hang tuan
OUTPUT_PATH = Path(__file__).parent.parent.parent / "output" / "portfolio_report.json"

def _latest_closes(conn, symbols) -> dict:
    """{symbol: (trade_date, close)} tu OHLCV moi nhat cua cac symbol."""
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    df = pd.read_sql_query(
        f"""
        SELECT symbol, trade_date, close FROM (
            SELECT symbol, trade_date, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM ohlcv_daily WHERE symbol IN ({placeholders})
        ) WHERE rn = 1
        """,
        conn,
        params=symbols,
    )
    return {r["symbol"]: (r["trade_date"], float(r["close"])) for _, r in df.iterrows()}

def _cash_balance(conn) -> float:
    """Tien mat con lai = VON_DAU_TU - tong mua + tong ban (tu portfolio_trades)."""
    buy = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM portfolio_trades WHERE action = 'buy'"
    ).fetchone()[0]
    sell = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM portfolio_trades WHERE action = 'sell'"
    ).fetchone()[0]
    return round(VON_DAU_TU - buy + sell, 2)

def _active_positions(conn) -> list:
    return [
        dict(r) for r in conn.execute(
            "SELECT * FROM portfolio_positions WHERE status = 'active'"
        ).fetchall()
    ]

def _sell(conn, symbol, trade_date, price, reason):
    """Ban toan bo vi the symbol. Ghi trade + cap nhat status."""
    pos = conn.execute(
        "SELECT * FROM portfolio_positions WHERE symbol = ? AND status = 'active'",
        (symbol,),
    ).fetchone()
    if not pos:
        return
    amount = pos["shares"] * price
    conn.execute(
        "INSERT INTO portfolio_trades (symbol, action, trade_date, price, shares, amount, reason) "
        "VALUES (?, 'sell', ?, ?, ?, ?, ?)",
        (symbol, trade_date, price, pos["shares"], amount, reason),
    )
    conn.execute(
        "UPDATE portfolio_positions SET status = 'closed', updated_at = ? WHERE symbol = ?",
        (datetime.now().isoformat(timespec="seconds"), symbol),
    )
    logger.info("BAN %s (%s): gia=%.0f so luong=%.0f (%s)", symbol, reason, price, pos["shares"], reason)

def _buy(conn, symbol, trade_date, price, reason):
    """Mua vi the moi voi so tien = min(tien mat, TRONG_SO% von dau tu)."""
    cash = _cash_balance(conn)
    amount = min(cash, VON_DAU_TU * TRONG_SO / 100.0)
    if amount <= 0:
        logger.warning("Mua %s: khong du tien mat - skip", symbol)
        return
    shares = amount / price
    amount_real = shares * price
    stop_price = round(price * (1 - CAT_LO_PCT / 100.0), 0)
    conn.execute(
        "INSERT INTO portfolio_trades (symbol, action, trade_date, price, shares, amount, reason) "
        "VALUES (?, 'buy', ?, ?, ?, ?, ?)",
        (symbol, trade_date, price, shares, amount_real, reason),
    )
    conn.execute(
        "INSERT INTO portfolio_positions "
        "(symbol, entry_date, entry_price, shares, stop_price, weight_pct, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?) "
        "ON CONFLICT(symbol) DO UPDATE SET shares = excluded.shares, "
        "entry_price = excluded.entry_price, stop_price = excluded.stop_price, "
        "weight_pct = excluded.weight_pct, status = 'active', updated_at = excluded.updated_at",
        (symbol, trade_date, price, shares, stop_price, TRONG_SO, datetime.now().isoformat(timespec="seconds")),
    )
    logger.info("MUA %s (%s): gia=%.0f so luong=%.0f tien=%.0f", symbol, reason, price, shares, amount_real)

def run_daily(conn) -> dict:
    """Chay 1 ngay: cat lo + tai can bang top N. Tra ve summary report."""
    # Top N moi nhat tu signals
    top = load_top_symbols(conn, top_n=TOI_DA_MA)
    closes = _latest_closes(conn, top + [p["symbol"] for p in _active_positions(conn)])
    if not closes:
        logger.warning("Khong co gia moi nhat - skip portfolio")
        return {}

    trade_date = max(d for d, _ in closes.values())
    positions = _active_positions(conn)
    active_syms = {p["symbol"] for p in positions}
    top_set = set(top[:TOI_DA_MA])
    today = datetime.now()
    is_rebalance_day = today.weekday() == REBALANCE_DOW or len(positions) == 0

    # 1. Cat lo (chay hang ngay)
    stopped_today = set()
    for p in positions:
        c = closes.get(p["symbol"])
        if c and c[1] <= p["stop_price"]:
            _sell(conn, p["symbol"], trade_date, c[1], "stop_loss")
            active_syms.discard(p["symbol"])
            stopped_today.add(p["symbol"])

    # 2. Tai can bang (chi thu 2 hoac lan dau)
    if is_rebalance_day:
        # Ban vi the khong con trong top
        for sym in list(active_syms):
            if sym not in top_set:
                c = closes.get(sym)
                if c:
                    _sell(conn, sym, trade_date, c[1], "not_in_top")
                    active_syms.discard(sym)
        # Mua ma moi trong top chua co vi the (khong mua lai ma vua cat lo hom nay)
        for sym in top:
            if sym not in active_syms and sym not in stopped_today:
                c = closes.get(sym)
                if c:
                    _buy(conn, sym, trade_date, c[1], "new_entry" if positions else "init_entry")
                    active_syms.add(sym)
                if len(active_syms) >= TOI_DA_MA:
                    break
    conn.commit()

    # 3. Report
    positions = _active_positions(conn)
    cash = _cash_balance(conn)
    total_value = cash
    pos_rows = []
    for p in positions:
        c = closes.get(p["symbol"])
        close = c[1] if c else p["entry_price"]
        value = p["shares"] * close
        total_value += value
        pos_rows.append({
            "symbol": p["symbol"],
            "entry_price": p["entry_price"],
            "close": close,
            "shares": round(p["shares"], 2),
            "weight_pct": p["weight_pct"],
            "stop_price": p["stop_price"],
            "gain_pct": round((close / p["entry_price"] - 1) * 100, 2) if p["entry_price"] else None,
            "status": p["status"],
        })
    pnl = total_value - VON_DAU_TU
    summary = {
        "trade_date": trade_date,
        "capital": VON_DAU_TU,
        "cash": round(cash, 0),
        "total_value": round(total_value, 0),
        "pnl": round(pnl, 0),
        "pnl_pct": round(pnl / VON_DAU_TU * 100, 2),
        "positions": pos_rows,
        "top_signals": top,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[portfolio_manager] {trade_date}: PnL={summary['pnl']:,.0f} "
          f"({summary['pnl_pct']}%) - {len(positions)} vi the")
    return summary

def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    conn = get_conn()
    try:
        run_daily(conn)
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
