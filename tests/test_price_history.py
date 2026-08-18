"""Unit tests cho fetch_price_history (luu lich su gia hang ngay).

Dung data mau, khong network. Kiem tra:
- Ghi moi khi file chua co / trading_date moi
- Thay the dong trung trading_date (so lieu moi nhat)
- Khong ghi khi fetch loi (error_count > 0) hoac prices rong
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fetcher.real_data_fetcher import append_price_history


def _report(prices=None, error_count=0, generated_at="2026-08-02T10:00:00"):
    return {
        "generated_at": generated_at,
        "error_count": error_count,
        "errors": [],
        "prices": prices if prices is not None else [
            {"symbol": "ACB", "price": "21,900", "change_percent": "(-2.01%)",
             "volume": "15,165,000", "trading_date": "31/07/2026 14:58"},
        ],
    }


def read_history(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_write_new(tmp_path):
    path = append_price_history(_report(), base_dir=tmp_path)
    assert path is not None
    recs = read_history(path)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["trading_date"] == "31/07/2026 14:58"
    assert rec["prices"][0]["symbol"] == "ACB"
    assert rec["prices"][0]["price"] == "21,900"


def test_append_new_date(tmp_path):
    append_price_history(_report(), base_dir=tmp_path)
    report2 = _report(generated_at="2026-08-03T10:00:00")
    report2["prices"][0]["trading_date"] = "01/08/2026 14:55"
    append_price_history(report2, base_dir=tmp_path)
    recs = read_history(tmp_path / "data" / "prices_history.jsonl")
    assert len(recs) == 2
    assert recs[0]["trading_date"] == "31/07/2026 14:58"
    assert recs[1]["trading_date"] == "01/08/2026 14:55"


def test_replace_same_date(tmp_path):
    append_price_history(_report(), base_dir=tmp_path)
    report2 = _report(generated_at="2026-08-02T15:00:00")
    report2["prices"][0]["price"] = "22,000"  # gia moi nhat cung ngay
    append_price_history(report2, base_dir=tmp_path)
    recs = read_history(tmp_path / "data" / "prices_history.jsonl")
    assert len(recs) == 1  # khong tang so dong
    assert recs[0]["prices"][0]["price"] == "22,000"


def test_no_write_on_error(tmp_path):
    result = append_price_history(_report(error_count=2), base_dir=tmp_path)
    assert result is None
    assert not (tmp_path / "data" / "prices_history.jsonl").exists()


def test_no_write_empty_prices(tmp_path):
    result = append_price_history(_report(prices=[]), base_dir=tmp_path)
    assert result is None
    assert not (tmp_path / "data" / "prices_history.jsonl").exists()
