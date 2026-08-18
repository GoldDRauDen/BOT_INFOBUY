"""Rule engine: quet toan bo symbol co du lieu moi nhat, tinh score, ghi signals.

Chay:
    python -m src.screening.rule_engine

- Doc config/screening_rules.yaml: moi rule co name, weight, conditions (pandas-expression).
- Validator: condition phai parse duoc (pd.eval) truoc khi chay.
- Evaluate tren DataFrame ngay moi nhat cua tung symbol (chi bao ky thuat + bien phu).
- score = tong weight cac condition khop -> ghi signals (strategy_name = ten rule,
  metadata_json = danh sach dieu kien khop).
- Output console: top 20 ma theo score.
"""
import ast
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn, upsert  # noqa: E402
from src.indicators.fundamental_screening import (  # noqa: E402
    net_income_yoy_growth,
    sector_relative_pe_pb,
)

logger = logging.getLogger("rule_engine")

RULES_PATH = Path(__file__).parent.parent.parent / "config" / "screening_rules.yaml"
TOP_N = 20


def load_rules(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Doc + validate screening_rules.yaml."""
    path = path or RULES_PATH
    with open(path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    rules = content.get("rules", []) if content else []
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"Khong co rule nao trong {path}")
    for rule in rules:
        if "name" not in rule or "conditions" not in rule:
            raise ValueError(f"Rule thieu 'name' hoac 'conditions': {rule}")
        if not isinstance(rule["conditions"], list) or not rule["conditions"]:
            raise ValueError(f"Rule '{rule.get('name')}' can it nhat 1 condition")
        # Validator: moi condition phai la bieu thuc Python hop le (syntax check)
        for cond in rule["conditions"]:
            try:
                ast.parse(cond, mode="eval")
            except SyntaxError as e:
                raise ValueError(
                    f"Condition khong parse duoc trong rule '{rule.get('name')}': {cond} -> {e}"
                )
        rule.setdefault("weight", 1.0)
    return rules


def latest_snapshot(conn, symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """Tra ve {symbol: DataFrame 1 dong} voi cot chi bao moi nhat + bien phu."""
    out: Dict[str, pd.DataFrame] = {}
    if not symbols:
        return out
    placeholders = ",".join("?" for _ in symbols)

    df_ind = pd.read_sql_query(
        f"""
        SELECT t.symbol, t.trade_date, t.ma20, t.ma50, t.ma200, t.rsi14,
               t.macd, t.macd_signal, t.atr14, t.volume_ma20,
               o.close, o.volume
        FROM technical_indicators_daily t
        JOIN ohlcv_daily o
          ON o.symbol = t.symbol AND o.trade_date = t.trade_date
        JOIN (
            SELECT symbol, MAX(trade_date) AS d
            FROM technical_indicators_daily
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
        ) m ON t.symbol = m.symbol AND t.trade_date = m.d
        WHERE t.symbol IN ({placeholders})
        """,
        conn,
        params=symbols + symbols,
    )
    if df_ind.empty:
        return out

    rel = sector_relative_pe_pb(conn, symbols)
    yoy = net_income_yoy_growth(conn, symbols)

    for _, r in df_ind.iterrows():
        sym = r["symbol"]
        row = pd.DataFrame([{
            "trade_date": r["trade_date"],
            "close": r["close"],
            "volume": r["volume"],
            "ma20": r["ma20"],
            "ma50": r["ma50"],
            "ma200": r["ma200"],
            "rsi14": r["rsi14"],
            "macd": r["macd"],
            "macd_signal": r["macd_signal"],
            "atr14": r["atr14"],
            "volume_ma20": r["volume_ma20"],
            "pe_rel": rel.get(sym, {}).get("pe_rel"),
            "pb_rel": rel.get(sym, {}).get("pb_rel"),
            "yoy_growth": yoy.get(sym),
            "change_pct": None,
        }])
        out[sym] = row

    # % thay doi gia 1 phien (tinh tu OHLCV 2 ngay cuoi)
    df_price = pd.read_sql_query(
        f"""
        SELECT symbol, trade_date, close FROM (
            SELECT symbol, trade_date, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM ohlcv_daily WHERE symbol IN ({placeholders})
        ) WHERE rn <= 2
        """,
        conn,
        params=symbols,
    )
    for sym, grp in df_price.groupby("symbol"):
        if len(grp) >= 2 and sym in out:
            grp = grp.sort_values("trade_date")
            prev, cur = float(grp.iloc[0]["close"]), float(grp.iloc[1]["close"])
            if prev:
                out[sym]["change_pct"] = (cur - prev) / prev * 100.0
    return out


def evaluate_rule(row: pd.DataFrame, rule: Dict[str, Any]) -> tuple:
    """Evaluate 1 rule tren 1 dong. Tra ve (score, danh sach condition khop)."""
    matched = []
    for cond in rule["conditions"]:
        try:
            res = row.eval(cond, engine="python")
            if bool(res.iloc[0]) if hasattr(res, "iloc") else bool(res):
                matched.append(cond)
        except Exception as e:  # noqa: BLE001 - condition loi do thieu cot -> bo qua
            logger.warning("Condition loi (%s): %s", cond, e)
    score = rule["weight"] * len(matched)
    return score, matched


def run_screening(conn=None, top_n: int = TOP_N) -> List[Dict[str, Any]]:
    """Chay toan bo rule tren snapshot moi nhat. Ghi signals + tra ve top_n."""
    rules = load_rules()
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        rows_sym = conn.execute(
            "SELECT symbol FROM symbols WHERE is_active = 1 ORDER BY symbol"
        ).fetchall()
        symbols = [r["symbol"] for r in rows_sym]
        snapshots = latest_snapshot(conn, symbols)

        today = datetime.now().strftime("%Y-%m-%d")
        signal_rows = []
        results = []
        for sym, row in snapshots.items():
            total_score = 0.0
            matched_all = []
            for rule in rules:
                score, matched = evaluate_rule(row, rule)
                if score > 0:
                    total_score += score
                    matched_all.append({
                        "rule": rule["name"],
                        "conditions": matched,
                    })
                    signal_rows.append({
                        "symbol": sym,
                        "trade_date": row.iloc[0]["trade_date"],
                        "strategy_name": rule["name"],
                        "score": score,
                        "metadata_json": json.dumps({"conditions": matched}, ensure_ascii=False),
                    })
            if total_score > 0:
                results.append({
                    "symbol": sym,
                    "score": total_score,
                    "trade_date": row.iloc[0]["trade_date"],
                    "close": row.iloc[0]["close"],
                    "change_pct": row.iloc[0]["change_pct"],
                    "matched": matched_all,
                })

        if signal_rows:
            upsert(conn, "signals", signal_rows, conflict_cols=["symbol", "trade_date", "strategy_name"])
            logger.info("Signals: %d dong", len(signal_rows))

        results.sort(key=lambda x: x["score"], reverse=True)
        top = results[:top_n]
        print(f"\n=== TOP {len(top)} MA THEO SCORE ===")
        for i, r in enumerate(top, 1):
            print(
                f"{i:2d}. {r['symbol']:6s} score={r['score']:.1f} "
                f"gia={r['close']:,.0f} ({r['change_pct']:.2f}%) "
                f"rules={[m['rule'] for m in r['matched']]}"
            )
        if not top:
            print("  (khong co ma nao khop dieu kien)")
        return top
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        top = run_screening()
        print(f"\n[rule_engine] Xong: {len(top)} ma trong top")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        print(f"[rule_engine] LOI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
