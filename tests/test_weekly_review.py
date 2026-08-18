"""Unit tests cho weekly/monthly review (src/reviewer/weekly_review.py).

Khong network: _load_history doc file tam trong tmp_path, build_review_report
thuan tuan (khong goi AI), AI chi mo phong qua mock trong mot test.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reviewer.weekly_review import (
    _load_history, _compute_stats, _fmt_pct, build_review_report,
    PERIOD_SESSIONS, _ai_prices_report,
)


def _session(trading_date, prices):
    return {"trading_date": trading_date, "generated_at": "2026-08-01T10:00:00",
            "prices": prices}


def _price(symbol, price, pct, vol, high, low):
    return {"symbol": symbol, "price": price, "change_percent": pct,
            "volume": vol, "high": high, "low": low}


def _history():
    """ACB tang dan, VCB giam dan, 6 phien."""
    sessions = []
    for i in range(6):
        d = f"{i+1:02d}/08/2026 14:58"
        acb = _price("ACB", f"{20 + i}000", f"(+1.0{i}%)", "1000000", f"{20+i}500", f"{19+i}500")
        vcb = _price("VCB", f"{30 - i}000", f"(-0.5{i}%)", "2000000", f"{31 - i}000", f"{29 - i}000")
        sessions.append(_session(d, [acb, vcb]))
    return sessions


def test_period_sessions_counts():
    assert PERIOD_SESSIONS["weekly"] == 5
    assert PERIOD_SESSIONS["monthly"] == 21


def test_load_history_sorted(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    path = data_dir / "prices_history.jsonl"
    # Ghi 2 dong khong theo thu tu ngay
    rec_moi = _session("02/08/2026 14:58", [_price("ACB", "20000", "(+1.0%)", "1000000", "20500", "19500")])
    rec_cu = _session("01/08/2026 14:58", [_price("ACB", "19000", "(+0.5%)", "900000", "19500", "18500")])
    path.write_text(
        json.dumps(rec_moi, ensure_ascii=False) + "\n" +
        json.dumps(rec_cu, ensure_ascii=False) + "\n",
        encoding="utf-8")
    records = _load_history(tmp_path)
    assert [r["trading_date"] for r in records] == ["01/08/2026 14:58", "02/08/2026 14:58"]


def test_compute_stats_change_pct():
    stats = _compute_stats(_history()[:3], ["ACB", "VCB"])
    # ACB: dau 20000, cuoi 22000 -> +10%
    assert abs(stats["ACB"]["change_pct"] - 10.0) < 0.01
    assert stats["ACB"]["start_price"] == 20000
    assert stats["ACB"]["end_price"] == 22000
    # VCB: dau 30000, cuoi 28000 -> -6.67%
    assert abs(stats["VCB"]["change_pct"] + 6.6667) < 0.01
    assert stats["VCB"]["high"] == 31000
    assert stats["VCB"]["low"] == 27000


def test_weekly_selects_last_5():
    records = _history()  # 6 phien
    weekly = records[-PERIOD_SESSIONS["weekly"]:]
    assert len(weekly) == 5
    assert weekly[0]["trading_date"] == "02/08/2026 14:58"  # phien thu 2
    assert weekly[-1]["trading_date"] == "06/08/2026 14:58"


def test_build_report_with_ai():
    records = _history()
    ai = "Thị trường tuần qua ghi nhận ACB tăng đều và VCB điều chỉnh giảm. " * 6
    text = build_review_report(records, period="weekly", ai_text=ai)
    assert "BÁO CÁO CHỨNG KHOÁN TUẦN" in text
    assert "5 phiên" in text
    assert "PHÂN TÍCH AI" in text
    assert "khuyến nghị đầu tư" in text  # disclaimer
    assert len(text) <= 4000


def test_build_report_without_ai():
    records = _history()
    text = build_review_report(records, period="monthly", ai_text=None)
    assert "BÁO CÁO CHỨNG KHOÁN THÁNG" in text
    assert "PHÂN TÍCH AI" not in text
    assert "Tăng mạnh nhất" in text


def test_build_report_empty():
    text = build_review_report([], period="weekly")
    assert "Chưa có dữ liệu lịch sử giá" in text


def test_fmt_pct():
    assert _fmt_pct(3.456) == "(+3.46%)"
    assert _fmt_pct(-2.01) == "(-2.01%)"
