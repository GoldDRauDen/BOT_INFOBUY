"""
Gui bao cao pipeline qua Telegram.
Doc tom tat tu output/, gui qua bot Telegram.

Su dung:
  python send_telegram.py

Credentials:
  - env var: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (uu tien)
  - hoac config/settings.yaml: telegram.token, telegram.chat_id
Thieu credentials -> in canh bao SKIP, exit 0 (khong fail CI).
"""
import logging
import sys
import json
import sqlite3
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


def _fmt_vnd(x) -> str:
    """Format so tien VND co dau phay. Tra 'N/A' neu None."""
    if x is None:
        return "N/A"
    return f"{float(x):,.0f}"


def _fmt_pct(x, digits: int = 2) -> str:
    """Format phan tram gan gon. Tra 'N/A' neu None."""
    if x is None:
        return "N/A"
    return f"{float(x):.{digits}f}%"


def build_quant_report(logger: logging.Logger) -> str:
    """Doc DB -> bao cao dinh luong gon cho giam doc (HTML-safe)."""
    if not DB_PATH.exists():
        return ""
    try:
        conn = _db_conn()
        lines: list = []

        regime_path = Path(__file__).parent / "output" / "market_regime.json"
        if regime_path.exists():
            try:
                data = json.loads(regime_path.read_text(encoding="utf-8"))
                regime = data.get("regime")
                if regime and regime != "unknown":
                    vn = _fmt_vnd(data.get("vnindex_close"))
                    ma = _fmt_vnd(data.get("ma200"))
                    emoji = "📈" if regime == "BULLISH" else "📉"
                    lines.append(f"<b>🏛 THỈ TRƯỜNG:</b> {emoji} {regime}")
                    lines.append(f"VNINDEX {vn} | MA200 {ma}")
                    if regime == "BEARISH":
                        lines.append("⚠️ Thị trường yếu – chỉ xem xét, thận trọng, hạn chế mua mới.")
                    lines.append("")
            except (json.JSONDecodeError, OSError):
                pass

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
                lines.append("<b>📋 TOP 10 AN TOÀN (rule-based):</b>")
                for rank, r in enumerate(rows, 1):
                    price = _fmt_vnd(r["price"])
                    lines.append(f"{rank}. {r['symbol']}  ▸ {price} VND")
                lines.append("Bộ lọc: xu hướng dài hạn + thanh khoản + định giá hợp lý.")
                lines.append("")
            else:
                lines.append("Chưa có tín hiệu từ rule engine.")
        except sqlite3.Error as e:
            logger.warning(f"Loi doc signals: {e}")

        pf_path = Path(__file__).parent / "output" / "portfolio_report.json"
        if pf_path.exists():
            try:
                data = json.loads(pf_path.read_text(encoding="utf-8"))
                if data.get("positions") is not None:
                    lines.append(f"<b>📂 DANH MỤC MẤU</b> (10 mã, 10% vốn/mã, cắt lỗ 8%):")
                    lines.append(
                        f"Tổng {_fmt_vnd(data.get('total_value'))} VND | "
                        f"PnL {_fmt_vnd(data.get('pnl'))} ({_fmt_pct(data.get('pnl_pct'))})"
                    )
                    for pos in data["positions"][:10]:
                        gain = pos.get("gain_pct")
                        gs = f"{gain:+.2f}%" if gain is not None else "N/A"
                        lines.append(
                            f"   ▸ {pos['symbol']} {_fmt_vnd(pos['close'])} VND ({gs})"
                        )
                    lines.append("")
                else:
                    lines.append("Danh mục mẫu: chưa có dữ liệu.")
            except (json.JSONDecodeError, OSError, TypeError) as e:
                logger.warning(f"Loi doc portfolio_report.json: {e}")

        try:
            run = conn.execute(
                "SELECT metrics_json FROM backtest_runs "
                "WHERE strategy_name = 'wf_rule_based' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if run:
                m = json.loads(run["metrics_json"])
                cagr = _fmt_pct(m.get("cagr_mean") * 100 if m.get("cagr_mean") is not None else None)
                sharpe = f"{m['sharpe_mean']:.2f}" if m.get("sharpe_mean") is not None else "N/A"
                maxdd = _fmt_pct(m.get("max_drawdown_mean") * 100 if m.get("max_drawdown_mean") is not None else None)
                wr = _fmt_pct(m.get("win_rate_mean") * 100 if m.get("win_rate_mean") is not None else None)
                lines.append(f"<b>✅ KIỂM CHỨNG (walk-forward {m.get('n_windows')}  kỳ):</b>")
                lines.append(f"CAGR {cagr} | Sharpe {sharpe} | MaxDD {maxdd} | Win {wr}")
                lines.append("Phải thắng benchmark ở nhiều kỳ mới tin kết quả này đang theo dõi, KHÔNG phải khuyến nghị mua.")
                lines.append("")
        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.warning(f"Loi doc backtest: {e}")

        corr_path = Path(__file__).parent / "output" / "correlation_check.json"
        if corr_path.exists():
            try:
                cdata = json.loads(corr_path.read_text(encoding="utf-8"))
                if cdata.get("concentration_warning"):
                    lines.append(f"⚠️ {cdata['concentration_warning']}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Loi doc correlation_check.json: {e}")

        conn.close()
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Loi build quant report (khong fail): {e}")
        return ""


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("send_telegram")

    print("=" * 50)
    print("  Gui bao cao Telegram")
    print("=" * 50)

    print("\n  [1/3] Fetch du lieu that (vietstock)...")
    prices_report = {}
    try:
        prices_report = run_real_data_fetch(logger)
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

    print("\n  [3/3] Build summary + gui...")
    from analyst.ai_analyst import AiAnalyst
    analyst = AiAnalyst(logger=logger)
    text = build_summary(real_prices=prices_report, ai_analysis=analysis,
                         ai_analyst=analyst)
    quant = build_quant_report(logger)
    print(f"\n  Tom tat bao cao ({len(text)} ky tu):")
    for line in text.splitlines():
        print(f"    {line}")

    sent = []
    if quant:
        quant_text = "📊 <b>PHÂN TÍCH ĐỆNH LƯỜNG BOT_INFOBUY</b>\n" + quant
        if len(quant_text) > 4000:
            quant_text = quant_text[:4000]
        print(f"\n  Gui tin 1 (dinh luong, {len(quant_text)} ky tu)...")
        sent.append(send_telegram(quant_text, logger=logger))
    print(f"\n  Gui tin 2 (bao cao chung, {len(text)} ky tu)...")
    sent.append(send_telegram(text, logger=logger))

    success = any(sent)
    if success:
        print("\n  ✅ Đã gửi báo cáo Telegram thành công")
        return 0

    print("\n  ⚠️ Khong gui duoc (thieu credential hoac loi mang)")
    print("  SKIP - exit 0 (khong fail CI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())