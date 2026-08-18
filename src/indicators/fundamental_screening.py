"""Bien phu de rule engine: PE/PB tuong doi theo nganh + tang truong loi nhuan YoY.

- PE/PB tuong doi: so sanh voi TRUNG BINH NGANH (sector tu bang symbols).
  Chi tinh neu sector co >= 3 ma, nguoc lai None (khong bia).
- Tang truong loi nhuan YoY: tu fundamentals_quarterly (cung quy nam truoc).
- Khong co du lieu -> None, khong bia.
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

def fundamentals_as_of(conn, symbol: str, as_of_date: str) -> pd.DataFrame:
    """Fundamentals cua symbol ma reported_date <= as_of_date.

    Dam bao KHONG co du lieu tuong lai (reported_date > as_of_date) lo vao
    mau ngay do - chong look-ahead bias.
    """
    return pd.read_sql_query(
        """
        SELECT symbol, quarter_end, reported_date, is_estimated, eps, pe_ratio,
               pb_ratio, roe, roa, bvps, revenue, net_income, dividend_yield
        FROM fundamentals_quarterly
        WHERE symbol = ? AND reported_date <= ?
        ORDER BY quarter_end
        """,
        conn,
        params=(symbol, as_of_date),
    )


logger = logging.getLogger("fundamental_screening")


def sector_relative_pe_pb(conn, symbols: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """PE/PB tuong doi (pe_ratio / trung binh nganh, pb_ratio / trung binh nganh).

    Tra ve {symbol: {"pe_rel": float|None, "pb_rel": float|None}}.
    """
    if not symbols:
        return {}
    sym_placeholders = ",".join("?" for _ in symbols)

    # Lay sector + chi so moi nhat cua tung symbol
    df_fund = pd.read_sql_query(
        f"""
        SELECT f.symbol, f.quarter_end, f.pe_ratio, f.pb_ratio
        FROM fundamentals_quarterly f
        JOIN (
            SELECT symbol, MAX(quarter_end) AS qe
            FROM fundamentals_quarterly
            WHERE symbol IN ({sym_placeholders})
            GROUP BY symbol
        ) m ON f.symbol = m.symbol AND f.quarter_end = m.qe
        WHERE f.symbol IN ({sym_placeholders})
        """,
        conn,
        params=symbols + symbols,
    )
    if df_fund.empty:
        return {s: {"pe_rel": None, "pb_rel": None} for s in symbols}

    df_sym = pd.read_sql_query(
        f"SELECT symbol, sector FROM symbols WHERE symbol IN ({sym_placeholders})",
        conn,
        params=symbols,
    )
    df = df_fund.merge(df_sym, on="symbol", how="left")

    result: Dict[str, Dict[str, Optional[float]]] = {}
    for symbol in symbols:
        result[symbol] = {"pe_rel": None, "pb_rel": None}

    # Trung binh theo sector (chi sector co >= 3 ma co du lieu)
    sector_stats: Dict[str, Dict[str, float]] = {}
    for sector, grp in df.groupby("sector"):
        if sector is None or pd.isna(sector):
            continue
        pe_vals = grp["pe_ratio"].dropna()
        pb_vals = grp["pb_ratio"].dropna()
        if len(grp) < 3:
            continue
        sector_stats[sector] = {
            "pe_avg": float(pe_vals.mean()) if not pe_vals.empty else float("nan"),
            "pb_avg": float(pb_vals.mean()) if not pb_vals.empty else float("nan"),
        }

    for _, r in df.iterrows():
        sector = r["sector"]
        sym = r["symbol"]
        if sector not in sector_stats:
            continue
        stats = sector_stats[sector]
        if not pd.isna(stats["pe_avg"]) and not pd.isna(r["pe_ratio"]) and stats["pe_avg"] > 0:
            result[sym]["pe_rel"] = float(r["pe_ratio"]) / stats["pe_avg"]
        if not pd.isna(stats["pb_avg"]) and not pd.isna(r["pb_ratio"]) and stats["pb_avg"] > 0:
            result[sym]["pb_rel"] = float(r["pb_ratio"]) / stats["pb_avg"]

    return result


def net_income_yoy_growth(conn, symbols: List[str]) -> Dict[str, Optional[float]]:
    """Tang truong loi nhuan sau thue YoY (cung quy nam truoc, ky moi nhat).

    Can it nhat 5 ky (cung quy 2 nam) - nguoc lai None.
    Tra ve {symbol: growth_pct|None}.
    """
    if not symbols:
        return {}
    sym_placeholders = ",".join("?" for _ in symbols)
    df = pd.read_sql_query(
        f"""
        SELECT symbol, quarter_end, net_income
        FROM fundamentals_quarterly
        WHERE symbol IN ({sym_placeholders}) AND net_income IS NOT NULL
        ORDER BY symbol, quarter_end
        """,
        conn,
        params=symbols,
    )
    result: Dict[str, Optional[float]] = {s: None for s in symbols}
    if df.empty:
        return result

    # quarter_end dang YYYY-MM-DD -> tach nam/quy de so sanh cung quy nam truoc
    df["year"] = df["quarter_end"].str[:4].astype(int)
    df["quarter"] = df["quarter_end"].str[5:7]

    for symbol, grp in df.groupby("symbol"):
        # Chon ky moi nhat co du lieu nam truoc
        latest = grp.sort_values("quarter_end").iloc[-1]
        prev = grp[
            (grp["year"] == latest["year"] - 1)
            & (grp["quarter"] == latest["quarter"])
        ]
        if prev.empty:
            continue
        cur, past = float(latest["net_income"]), float(prev.iloc[0]["net_income"])
        if past == 0:
            continue
        result[symbol] = (cur - past) / abs(past) * 100.0

    return result
