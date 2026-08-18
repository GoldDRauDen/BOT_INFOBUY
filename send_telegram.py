"""
Gui bao cao pipeline qua Telegram.
Doc tom tat tu output/, gui qua bot Telegram.

Su dung:
  python send_telegram.py

Credentials:
  - env var: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (uu tien)
  - hoac config/settings.yaml: telegram.token, telegram.chat_id
Thieu credentials -> in canh bao SKIP, exit 0 (khong fail CI).

Phase 1-4+6: doc signals + backtest metrics moi nhat tu DB (data/bot_buy.db),
so sanh VNINDEX, canh bao tuong quan. Neu khong co metric backtest -> ghi
"chua co backtest" chu khong hien signal tran.
"""
import json
import logging
import sqlite3
import sys
from pathlib import Path

# Them src vao sys.path (nhu main.py)
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Windows console cp1252 khong in duoc emoji -> force UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from reporters.telegram_sender import build_summary, send_telegram
from fetcher.real_data_fetcher import run_real_data_fetch
from analyst.ai_analyst import run_ai_analysis

DB_PATH = Path(__file__).parent / "data" / "bot_buy.db"


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def build_quant_report(logger: logging.Logger) -> str:
    """Doc DB -> bao cao dinh luong: top 10 signals, backtest WF, VNINDEX, tuong quan."""
    if not DB_PATH.exists():
        return "  - Chua co du lieu phan tich dinh luong (data/bot_buy.db chua duoc tao)."
    try:
        conn = _db_conn()
        lines: list = []

        # 1. Top 10 ma theo score (signal moi nhat moi ma)
        try:
            rows = conn.execute(
                """
                SELECT s.symbol, s.strategy_name, s.score, s.metadata_json,
                       o.close AS price
                FROM signals s
                LEFT JOIN ohlcv_daily o
                  ON o.symbol = s.symbol
                 AND o.trade_date = (SELECT MAX(trade_date) FROM ohlcv_daily WHERE symbol = s.symbol)
                WHERE s.trade_date = (SELECT MAX(trade_date) FROM signals)
                ORDER BY s.score DESC LIMIT 10
                """
            ).fetchall()
            if rows:
                lines.append("  TOP 10 MA (theo score):")
                for r in rows:
                    meta = ""
                    try:
                        md = json.loads(r["metadata_json"] or "{}")
                        meta = ", ".join(md.get("conditions", []))[:80]
                    except json.JSONDecodeError:
                        pass
                    price = f"{r['price']:,.0f} VND" if r["price"] else "N/A"
                    lines.append(
                        f"    - {r['symbol']} [{r['strategy_name']}] score={r['score']:.1f} "
                        f"gia={price} | {meta}"
                    )
            else:
                lines.append("  - Chua co signal nao tu rule engine.")
        except sqlite3.Error as e:
            logger.warning(f"Loi doc signals: {e}")

        # 2. Backtest metrics (walk-forward moi nhat)
        try:
            run = conn.execute(
                "SELECT metrics_json FROM backtest_runs "
                "WHERE strategy_name = 'wf_rule_based' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if run:
                metrics = json.loads(run["metrics_json"])
                lines.append("  WALK-FORWARD (moi nhat):")
                lines.append(
                    f"    - CAGR mean={metrics.get('cagr_mean')}, std={metrics.get('cagr_std')}"
                )
                lines.append(
                    f"    - Sharpe mean={metrics.get('sharpe_mean')}, "
                    f"MaxDD mean={metrics.get('max_drawdown_mean')}"
                )
                if metrics.get("warning"):
                    lines.append(f"    - CANH BAO: {metrics['warning']}")
            else:
                lines.append("  - Backtest: chua co backtest (walk-forward chua chay).")
        except sqlite3.Error as e:
            logger.warning(f"Loi doc backtest: {e}")

        # 3. So sanh VNINDEX (benchmark tu backtest_runs moi nhat)
        try:
            bm = conn.execute(
                "SELECT benchmark_json FROM backtest_runs "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if bm:
                lines.append("  - VNINDEX benchmark: xem window trong backtest_runs (id cuoi).")
        except sqlite3.Error as e:
            logger.warning(f"Loi doc benchmark: {e}")

        # 4. Canh bao tuong quan
        corr_path = Path(__file__).parent / "output" / "correlation_check.json"
        if corr_path.exists():
            try:
                data = json.loads(corr_path.read_text(encoding="utf-8"))
                if data.get("concentration_warning"):
                    lines.append(f"  - {data['concentration_warning']}")
                if data.get("avg_correlation") is not None:
                    lines.append(
                        f"  - Avg correlation (60 phien): {data['avg_correlation']:.4f}"
                    )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Loi doc correlation_check.json: {e}")
        else:
            lines.append("  - Chua co du lieu tuong quan (chay src.risk.correlation_check).")

        conn.close()
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Loi build quant report (khong fail): {e}")
        return "  - Loi doc du lieu phan tich dinh luong."


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("send_telegram")

    print("=" * 50)
    print("  Gui bao cao Telegram")
    print("=" * 50)

    # PHA 2: fetch du lieu that (vietstock) + AI phan tich
    print("\n  [1/3] Fetch du lieu that (vietstock)...")
    prices_report = {}
    try:
        prices_report = run_real_data_fetch(logger)
        # Luu lich su gia (chi khi fetch OK: error_count == 0 va co prices)
        try:
            from fetcher.real_data_fetcher import append_price_history
            history_path = append_price_history(prices_report, logger=logger)
            if history_path:
                logger.info(f"Da luu lich su gia: {history_path}")
        except Exception as e:
            logger.warning(f"Loi ghi lich su gia (khong fail): {e}")
    except Exception as e:
        logger.error(f"Loi fetch du lieu that: {e}")
        print(f"  [LOI] {e}")

    print("\n  [2/3] AI phan tich (Gemini)...")
    analysis = None
    try:
        analysis = run_ai_analysis(logger)
    except Exception as e:
        logger.error(f"Loi AI phan tich: {e}")
        print(f"  [LOI] {e}")

    # Build summary (gom du lieu that + AI)
    print("\n  [3/3] Build summary + gui...")
    from analyst.ai_analyst import AiAnalyst
    analyst = AiAnalyst(logger=logger)
    text = build_summary(real_prices=prices_report, ai_analysis=analysis,
                         ai_analyst=analyst)

    # Them bao cao dinh luong tu DB (phase 1-4+6)
    quant = build_quant_report(logger)
    if quant:
        text = text.rstrip() + "\n\n=== PHAN TICH DINH LUONG (BOT_INFOBUY) ===\n" + quant

    print(f"\n  Tom tat bao cao ({len(text)} ky tu):")
    for line in text.splitlines():
        print(f"    {line}")

    # Gui
    print("\n  Gui qua Telegram...")
    success = send_telegram(text, logger=logger)

    if success:
        print("\n  ✅ Da gui bao cao Telegram thanh cong")
        return 0

    # Thieu credential hoac gui loi -> khong fail CI (theo yeu cau)
    print("\n  ⚠️ Khong gui duoc (thieu credential hoac loi mang)")
    print("  SKIP - exit 0 (khong fail CI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
