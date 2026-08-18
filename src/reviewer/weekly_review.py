"""
Weekly/Monthly Review - Tong ket tuan/thang tu lich su gia.

Doc data/prices_history.jsonl, tinh toan % ca ky, KL trung binh, gia cao/thap,
so phien tang/giam, tao bao cao HTML tieng Viet co dau va gui Telegram.

Su dung:
  python -m src.reviewer.weekly_review --period weekly   # 5 phien gan nhat
  python -m src.reviewer.weekly_review --period monthly  # 21 phien gan nhat

Credentials Telegram tu env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) nhu
send_telegram.py. Thieu credentials -> canh bao SKIP, exit 0 (khong fail CI).
AI loi -> bo qua phan AI (khong fail).
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Them src vao sys.path (nhu main.py / send_telegram.py)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

try:
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.config_loader import load_settings

from reporters.telegram_sender import (
    _html_escape, _format_volume, _num, _pct_value,
    send_telegram, get_telegram_config,
)
from analyst.ai_analyst import AiAnalyst

MAX_TEXT_LENGTH = 4000
PERIOD_SESSIONS = {"weekly": 5, "monthly": 21}


def _parse_trading_date(value: Any) -> Optional[datetime]:
    """Parse '31/07/2026 14:58' -> datetime. Tra None neu loi."""
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _load_history(base_dir: Path = None) -> List[Dict[str, Any]]:
    """
    Doc data/prices_history.jsonl, sap xep theo trading_date tang dan.
    Tra list record. Bo qua dong loi JSON.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent.parent
    history_path = Path(base_dir) / "data" / "prices_history.jsonl"
    if not history_path.exists():
        return []
    records = []
    with open(history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("prices"):
                records.append(rec)
    # Sap xep tang dan theo trading_date
    records.sort(key=lambda r: _parse_trading_date(r.get("trading_date")) or datetime.min)
    return records


def _symbols(records: List[Dict[str, Any]]) -> List[str]:
    """Danh sach ma (giu thu tu watchlist neu co, bo ma thieu o phien nao do)."""
    if not records:
        return []
    # Uu tien thu tu watchlist trong settings
    try:
        settings = load_settings()
        watchlist = settings.get("ai", {}).get("watchlist", []) or []
    except Exception:
        watchlist = []
    seen = set()
    ordered = []
    for sym in watchlist:
        seen.add(sym)
        ordered.append(sym)
    for p in records[-1].get("prices", []):
        sym = p.get("symbol")
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered


def _price_of(session: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Tim price entry cua symbol trong 1 phien."""
    for p in session.get("prices", []):
        if p.get("symbol") == symbol:
            return p
    return None


def _compute_stats(records: List[Dict[str, Any]],
                   symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Tinh thong ke moi ma trong ky:
      start_price, end_price, change_pct (ca ky), avg_volume, total_volume,
      high, low, up_days, down_days, flat_days, sessions.
    Chi tinh ma co du lieu ca dau ky va cuoi ky.
    """
    stats = {}
    for symbol in symbols:
        prices = []
        for session in records:
            p = _price_of(session, symbol)
            if p:
                prices.append(p)
        if len(prices) < 2:
            continue
        start = _num(prices[0].get("price"))
        end = _num(prices[-1].get("price"))
        if start <= 0 or end <= 0:
            continue
        vols = [_num(p.get("volume")) for p in prices]
        highs = [_num(p.get("high")) for p in prices]
        lows = [_num(p.get("low")) for p in prices]
        change_pct = (end - start) / start * 100.0
        up = sum(1 for p in prices if _pct_value(p.get("change_percent")) > 0)
        down = sum(1 for p in prices if _pct_value(p.get("change_percent")) < 0)
        flat = sum(1 for p in prices if _pct_value(p.get("change_percent")) == 0)
        stats[symbol] = {
            "start_price": start,
            "end_price": end,
            "change_pct": change_pct,
            "avg_volume": sum(vols) / len(vols),
            "total_volume": sum(vols),
            "high": max(highs) if highs else 0.0,
            "low": min([v for v in lows if v > 0]) if any(v > 0 for v in lows) else 0.0,
            "up_days": up,
            "down_days": down,
            "flat_days": flat,
            "sessions": len(prices),
        }
    return stats


def _fmt_price(v: float) -> str:
    """Format gia: '21,900'."""
    if v <= 0:
        return "0"
    return f"{v:,.0f}".replace(",", ".")


def _fmt_pct(v: float) -> str:
    """Format % ca ky: '(+3.45%)' / '(-2.01%)'."""
    sign = "+" if v >= 0 else ""
    return f"({sign}{v:.2f}%)"


def _fmt_volume(v: float) -> str:
    """KL trung binh/tong: '15.2 tr'."""
    return _format_volume(v)


def build_review_report(records: List[Dict[str, Any]], period: str = "weekly",
                        ai_text: Optional[str] = None) -> str:
    """
    Tao bao cao HTML tieng Viet co dau gioi han MAX_TEXT_LENGTH (4000 ky tu).
    Uu tien giu PHAN TICH AI tron ven: neu vuot, cat bot cac dong WATCHLIST
    (tu cuoi len) truoc; chi cat duoi khi bo het van con vuot (truong hop hiem).
    """
    n_max = PERIOD_SESSIONS.get(period, 5)
    sessions = records[-n_max:] if records else []
    period_label = "TUẦN" if period == "weekly" else "THÁNG"

    lines = []
    if not sessions:
        lines.append(f"📊 <b>BÁO CÁO CHỨNG KHOÁN {period_label}</b>")
        lines.append("⚠️ Chưa có dữ liệu lịch sử giá (data/prices_history.jsonl)")
        return "\n".join(lines)

    start_dt = _parse_trading_date(sessions[0].get("trading_date"))
    end_dt = _parse_trading_date(sessions[-1].get("trading_date"))
    start_s = start_dt.strftime("%d/%m/%Y") if start_dt else str(sessions[0].get("trading_date"))
    end_s = end_dt.strftime("%d/%m/%Y") if end_dt else str(sessions[-1].get("trading_date"))

    lines.append(f"📊 <b>BÁO CÁO CHỨNG KHOÁN {period_label}</b>")
    lines.append(f"🗓 Kỳ: {start_s} → {end_s} ({len(sessions)} phiên)")

    symbols = _symbols(sessions)
    stats = _compute_stats(sessions, symbols)

    # ---------- TỔNG QUAN ----------
    lines.append("")
    lines.append("📈 <b>TỔNG QUAN</b>")
    if stats:
        up_total = sum(1 for s in stats.values() if s["change_pct"] > 0)
        down_total = sum(1 for s in stats.values() if s["change_pct"] < 0)
        flat_total = sum(1 for s in stats.values() if s["change_pct"] == 0)
        lines.append(f"Tăng: {up_total} | Giảm: {down_total} | Đứng: {flat_total} (trong watchlist)")
        total_vol = sum(s["total_volume"] for s in stats.values())
        lines.append(f"🔢 Tổng KL cả kỳ: {_fmt_volume(total_vol)}")
    else:
        lines.append("⚠️ Không đủ dữ liệu để tính")

    # ---------- WATCHLIST ----------
    lines.append("")
    lines.append("📋 <b>WATCHLIST</b>")
    watch_idx = []
    for symbol, s in sorted(stats.items(),
                            key=lambda kv: kv[1]["change_pct"], reverse=True):
        watch_idx.append(len(lines))
        lines.append(
            f"📌 {_html_escape(symbol)}: {_fmt_price(s['start_price'])} → "
            f"{_fmt_price(s['end_price'])} {_fmt_pct(s['change_pct'])} | "
            f"KL TB {_fmt_volume(s['avg_volume'])} | "
            f"Cao {_fmt_price(s['high'])} / Thấp {_fmt_price(s['low'])}"
        )

    # ---------- ĐIỂM NHẤN ----------
    if stats:
        lines.append("")
        lines.append("⭐ <b>ĐIỂM NHẤN</b>")
        top_up = max(stats.items(), key=lambda kv: kv[1]["change_pct"])
        top_down = min(stats.items(), key=lambda kv: kv[1]["change_pct"])
        lines.append(
            f"🚀 Tăng mạnh nhất: {_html_escape(top_up[0])} "
            f"{_fmt_pct(top_up[1]['change_pct'])}"
        )
        lines.append(
            f"🔻 Giảm mạnh nhất: {_html_escape(top_down[0])} "
            f"{_fmt_pct(top_down[1]['change_pct'])}"
        )
        lines.append("")

    # ---------- PHÂN TÍCH AI ----------
    if ai_text:
        lines.append("🤖 <b>PHÂN TÍCH AI</b>")
        for para in _html_escape(ai_text).split("\n"):
            if para.strip():
                lines.append(para.strip())
        lines.append("")

    # ---------- DISCLAIMER ----------
    lines.append("—" * 20)
    lines.append(
        "⚠️ Báo cáo tự động từ dữ liệu thật (vietstock), chỉ mang tính tham khảo, "
        "KHÔNG phải khuyến nghị đầu tư."
    )

    text = "\n".join(lines)
    if len(text) > MAX_TEXT_LENGTH:
        # Uu tien giu AI tron ven: cat bot dan dong WATCHLIST tu cuoi len
        for idx in reversed(watch_idx):
            lines.pop(idx)
            if len("\n".join(lines)) <= MAX_TEXT_LENGTH:
                break
        text = "\n".join(lines)
        if len(text) > MAX_TEXT_LENGTH:
            text = text[: MAX_TEXT_LENGTH - 3] + "..."
    return text


def _ai_prices_report(sessions: List[Dict[str, Any]],
                      stats: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Tao prices_report cho AI: moi ma 1 entry voi gia cuoi ky + % ca ky + KL TB."""
    if not sessions or not stats:
        return None
    start_dt = _parse_trading_date(sessions[0].get("trading_date"))
    prices = []
    for symbol, s in stats.items():
        prices.append({
            "symbol": symbol,
            "price": _fmt_price(s["end_price"]),
            "change_percent": _fmt_pct(s["change_pct"]),
            "volume": _fmt_volume(s["avg_volume"]),
            "open": _fmt_price(s["start_price"]),
            "high": _fmt_price(s["high"]),
            "low": _fmt_price(s["low"]),
            "trading_date": start_dt.strftime("%d/%m/%Y") if start_dt else "",
        })
    return {"prices": prices}


def run_review(period: str = "weekly", logger: logging.Logger = None,
               base_dir: Path = None) -> int:
    """
    Chay review: doc history, tinh stats, goi AI, build report, gui Telegram.
    Tra exit code (0 luon - khong fail CI).
    """
    if logger is None:
        logger = logging.getLogger("weekly_review")
    records = _load_history(base_dir)
    n_max = PERIOD_SESSIONS.get(period, 5)
    sessions = records[-n_max:] if records else []
    period_label = "TUẦN" if period == "weekly" else "THÁNG"
    print(f"\n  Review {period_label}: {len(sessions)} phiên gần nhất")

    # AI phan tich (loi -> bo qua, khong fail)
    ai_text = None
    try:
        analyst = AiAnalyst(logger=logger)
        prices_report = _ai_prices_report(sessions, _compute_stats(
            sessions, _symbols(sessions)))
        if prices_report:
            ai_text = analyst.analyze_with_prompt(
                "Đây là dữ liệu tổng kết cả kỳ (giá đầu kỳ → cuối kỳ, % thay đổi "
                "cả kỳ, khối lượng trung bình, giá cao/thấp) của các mã trong "
                "watchlist. Hãy viết tổng kết xu hướng cả kỳ: (a) diễn biến chính "
                "của các mã, (b) điểm nổi bật, (c) cảnh báo rủi ro. Chỉ dùng số "
                "liệu được cung cấp, không thêm thông tin khác.",
                prices_report,
            )
            if ai_text:
                logger.info(f"AI review OK ({len(ai_text)} chars, model {analyst.last_model})")
    except Exception as e:
        logger.warning(f"Loi AI review (bo qua): {e}")

    text = build_review_report(records, period=period, ai_text=ai_text)
    print(f"\n  Báo cáo ({len(text)} ký tự):")
    for line in text.splitlines():
        print(f"    {line}")

    print("\n  Gửi qua Telegram...")
    success = send_telegram(text, logger=logger)
    if success:
        print("\n  ✅ Đã gửi báo cáo review qua Telegram")
        return 0
    print("\n  ⚠️ Không gửi được (thiếu credential hoặc lỗi mạng)")
    print("  SKIP - exit 0 (không fail CI)")
    return 0


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly/Monthly stock review")
    parser.add_argument("--period", choices=["weekly", "monthly"], default="weekly",
                        help="weekly: 5 phien gan nhat, monthly: 21 phien gan nhat")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("weekly_review")
    return run_review(period=args.period, logger=logger)


if __name__ == "__main__":
    sys.exit(main())
