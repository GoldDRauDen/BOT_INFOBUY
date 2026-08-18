"""
Telegram Report Sender - Gui bao cao chung khoan cho nha dau tu.
Format: BÁO CÁO CHỨNG KHOÁN, tieng Viet co dau, HTML-escape, max 4000 ky tu.

Security:
- Token bot la SECRET - chi doc tu env var TELEGRAM_BOT_TOKEN (hoac settings).
- KHONG commit token vao repo.
- CI dung GitHub secrets, local dung env var.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.config_loader import load_settings


try:
    from ..analyst.ai_analyst import MIN_ANALYSIS_LENGTH
except ImportError:
    MIN_ANALYSIS_LENGTH = 250


TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_TEXT_LENGTH = 4000
VIETSTOCK_URL = "https://finance.vietstock.vn/{symbol}/thong-ke-giao-dich.htm"


def _read_json(path: Path) -> Optional[Any]:
    """Doc JSON file, tra None neu thieu/loi."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _html_escape(text: Any) -> str:
    """Escape ky tu HTML de an toan voi parse_mode=HTML."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_time(generated_at: Any = None) -> str:
    """
    Hien thi thoi gian gio Viet Nam (UTC+7).
    Lay datetime.now(timezone.utc) (khong dung generated_at - GitHub runner la UTC,
    nhung generated_at local co the khac) roi chuyen sang Asia/Ho_Chi_Minh.
    """
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(timezone.utc)
        vn = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
        return vn.strftime("%d/%m/%Y %H:%M")
    except Exception:
        # Fallback: VN co dinh UTC+7 (khong DST), dung neu thieu tzdata
        return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y %H:%M")


def _num(value: Any) -> float:
    """Chuyen chuoi so Viet Nam (co dau phay) thanh float. Tra 0.0 neu loi."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(".", "").replace("%", "").strip()
    s = re.sub(r"[^\d\-+.]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _pct_value(pct_str: Any) -> float:
    """Lay gia tri % tu chuoi nhu '(-2.01%)' hoac '-2.01%'. Tra 0.0 neu loi."""
    if pct_str is None:
        return 0.0
    s = str(pct_str)
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1))
    return 0.0


def _pct_display(pct_str: Any) -> str:
    """
    Format % hien thi: luon 1 cap ngoac '(-2.01%)'.
    Strip ngoac san co tu nguon (tranh ((-2.01%))).
    """
    if pct_str is None:
        return ""
    s = str(pct_str).strip()
    s = s.strip("()")
    if s:
        return f"({s})"
    return ""


def _format_volume(value: Any) -> str:
    """KL tung ma: doi sang trieu (tr), 1 chu so thap phan. VD: 15,165,000 -> '15.2 tr'."""
    num = _num(value)
    if num <= 0:
        return "0"
    return f"{num / 1_000_000:.1f} tr"


def _format_total_volume(value: Any) -> str:
    """Tong KL: trieu co phieu, 1 chu so thap phan. VD: 101,147,200 -> '101.1 triệu cổ phiếu'."""
    num = _num(value)
    if num <= 0:
        return "0"
    return f"{num / 1_000_000:.1f} triệu cổ phiếu"


def _summarize_ai(text: str, ai_analyst=None, prices_report: Dict = None) -> Optional[str]:
    """
    Chi giu AI text neu >= 250 ky tu (ngan hon = bi cat/qua ngan).
    Neu ngan, thu lai 1 lan voi prompt 'phan tich chi tiet hon, it nhat 3 cau'.
    Van ngan -> tra None.
    """
    if not text:
        return None
    if len(text) >= MIN_ANALYSIS_LENGTH:
        return text
    # Thu lai 1 lan voi prompt chi tiet hon
    try:
        if ai_analyst is not None and prices_report:
            retry = ai_analyst.analyze_with_prompt(
                "Phân tích chi tiết hơn, ít nhất 3 câu, dựa trên dữ liệu đã cung cấp.",
                prices_report,
            )
            if retry and len(retry) >= MIN_ANALYSIS_LENGTH:
                return retry
    except Exception:
        pass
    return None


def build_summary(base_dir: str = None, config: Dict = None,
                  real_prices: Dict = None, ai_analysis: str = None,
                  ai_analyst=None) -> str:
    """
    Tao bao cao chung khoan cho nha dau tu (tieng Viet co dau, HTML-escape,
    max 4000 ky tu).

    Cau truc:
    1. Header: BÁO CÁO CHỨNG KHOÁN + gio Viet Nam (UTC+7) + ngay phien
    2. THỊ TRƯỜNG: so ma tang/giam/dung gia, tong KL (VN-INDEX neu lay duoc,
       khong lay duoc thi bo qua phan index, khong bia)
    3. WATCHLIST: moi ma 1 dong gon
    4. ĐIỂM NHẤN: top tang + top giam
    5. PHÂN TÍCH AI: chi hien thi neu text >= 120 ky tu
    6. Disclaimer + nguon + thoi diem lay du lieu
    """
    if base_dir is None:
        base_dir = str(Path(__file__).parent.parent.parent)
    output_dir = Path(base_dir) / "output"

    final = _read_json(output_dir / "final_report.json") or {}
    quality = _read_json(output_dir / "quality_report.json") or {}

    generated_at = final.get("generated_at") or quality.get("generated_at") or datetime.now().isoformat()
    time_str = _format_time(generated_at)

    prices = (real_prices or {}).get("prices") or []
    lines = []

    # ---------- 1. HEADER ----------
    lines.append("📊 <b>BÁO CÁO CHỨNG KHOÁN</b>")
    lines.append(f"🕐 {_html_escape(time_str)} (giờ Việt Nam, UTC+7)")
    if prices and prices[0].get("trading_date"):
        lines.append(f"📅 Dữ liệu phiên: {_html_escape(prices[0]['trading_date'])}")
    lines.append("")

    # ---------- 2. THỊ TRƯỜNG ----------
    lines.append("🏛 <b>THỊ TRƯỜNG</b>")
    if prices:
        up = sum(1 for p in prices if _pct_value(p.get("change_percent")) > 0)
        down = sum(1 for p in prices if _pct_value(p.get("change_percent")) < 0)
        flat = len(prices) - up - down
        total_vol = sum(_num(p.get("volume")) for p in prices)
        lines.append(f"📈 Tăng: {up} | 📉 Giảm: {down} | ➖ Đứng giá: {flat}")
        lines.append(f"🔢 Tổng khối lượng (watchlist {len(prices)} mã): {_format_total_volume(total_vol)}")
    else:
        lines.append("⚠️ Không có dữ liệu giá")
    lines.append("")

    # ---------- 3. WATCHLIST ----------
    lines.append("📋 <b>WATCHLIST</b>")
    watchlist_line_indices = []
    if prices:
        for p in prices:
            symbol = _html_escape(p.get("symbol", "?"))
            price = _html_escape(p.get("price", "?"))
            pct = _html_escape(_pct_display(p.get("change_percent")))
            vol = _html_escape(_format_volume(p.get("volume")))
            watchlist_line_indices.append(len(lines))
            lines.append(f"📌 {symbol}: {price} VND {pct} | KL {vol}")
    else:
        lines.append("⚠️ Chưa có dữ liệu giá thật")
    lines.append("")

    # ---------- 4. ĐIỂM NHẤN ----------
    if prices:
        lines.append("⭐ <b>ĐIỂM NHẤN</b>")
        with_pct = [(p, _pct_value(p.get("change_percent"))) for p in prices]
        valid = [x for x in with_pct if x[1] != 0.0]
        if valid:
            top_up = max(valid, key=lambda x: x[1])
            top_down = min(valid, key=lambda x: x[1])
            lines.append(
                f"🚀 Tăng mạnh nhất: {_html_escape(top_up[0].get('symbol', '?'))} "
                f"{_html_escape(_pct_display(top_up[0].get('change_percent')))}"
            )
            lines.append(
                f"🔻 Giảm mạnh nhất: {_html_escape(top_down[0].get('symbol', '?'))} "
                f"{_html_escape(_pct_display(top_down[0].get('change_percent')))}"
            )
        else:
            lines.append("➖ Không có mã tăng/giảm đáng kể")
        lines.append("")

    # ---------- 5. PHÂN TÍCH AI ----------
    ai_text = _summarize_ai(ai_analysis, ai_analyst=ai_analyst,
                            prices_report=real_prices) if ai_analysis else None
    if ai_text:
        lines.append("🤖 <b>PHÂN TÍCH AI</b>")
        for para in _html_escape(ai_text).split("\n"):
            if para.strip():
                lines.append(para.strip())
        lines.append("")
    elif ai_analysis is not None:
        # Co AI text nhung qua ngan va khong the lam dai hon
        lines.append("🤖 <b>PHÂN TÍCH AI</b>")
        lines.append("⚠️ AI phân tích chưa đạt (quá ngắn)")
        lines.append("")

    # ---------- 6. DISCLAIMER ----------
    lines.append("—" * 20)
    lines.append(
        "⚠️ Báo cáo tự động từ dữ liệu thật (vietstock), chỉ mang tính tham khảo, "
        "KHÔNG phải khuyến nghị đầu tư."
    )
    if prices and prices[0].get("source_url"):
        lines.append(f"🔗 Nguồn: {_html_escape(prices[0]['source_url'])}")
    lines.append(f"⏱ Thời điểm lấy dữ liệu: {_html_escape(time_str)}")

    text = "\n".join(lines)
    if len(text) > MAX_TEXT_LENGTH:
        # Uu tien giu PHAN TICH AI tron ven: cat bot dan cac dong WATCHLIST
        # (tu cuoi len) cho den khi vua gioi han. PHAN TICH AI + DIEM NHAN
        # + THI TRUONG + DISCLAIMER van day du.
        for idx in reversed(watchlist_line_indices):
            lines.pop(idx)
            if len("\n".join(lines)) <= MAX_TEXT_LENGTH:
                break
        text = "\n".join(lines)
        if len(text) > MAX_TEXT_LENGTH:
            # Truong hop cuc ky hiem: van vuot sau khi bo het WATCHLIST
            text = text[: MAX_TEXT_LENGTH - 3] + "..."
    return text


def get_telegram_config(config: Dict = None) -> Dict[str, Any]:
    """
    Lay cau hinh telegram: token/chat_id tu env var (uu tien) hoac settings.
    Tra ve {"enabled", "token", "chat_id"}. Khong bao gio tra token rong.
    """
    result = {"enabled": False, "token": None, "chat_id": None}

    if config is None:
        try:
            settings = load_settings()
            config = settings.get("telegram", {})
        except Exception:
            config = {}
    config = config or {}

    # Env var uu tien (CI: GitHub secrets)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.get("token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or config.get("chat_id")

    if token and chat_id:
        result["enabled"] = True
        result["token"] = token
        result["chat_id"] = chat_id
    return result


def send_telegram(text: str, token: str = None, chat_id: str = None,
                  logger: logging.Logger = None, timeout: int = 15,
                  retries: int = 2) -> bool:
    """
    Gui text qua Telegram bot API (POST).
    Tra ve True neu gui thanh cong, False neu that bai.
    """
    if logger is None:
        logger = logging.getLogger("telegram_sender")

    if token is None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if chat_id is None:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("SKIP: thieu TELEGRAM_BOT_TOKEN hoac TELEGRAM_CHAT_ID (env/settings)")
        print("⚠️ SKIP: thiếu Telegram credentials (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    for attempt in range(retries + 1):
        try:
            response = requests.post(url, data=payload, timeout=timeout)
            if response.status_code == 200:
                logger.info("Da gui bao cao Telegram thanh cong")
                return True
            logger.warning(
                f"Telegram API tra ve {response.status_code} (lan {attempt + 1}): "
                f"{response.text[:200]}"
            )
        except requests.RequestException as e:
            logger.warning(f"Loi gui Telegram (lan {attempt + 1}): {e}")
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    return False
