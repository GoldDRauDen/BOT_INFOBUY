"""
Unit tests cho Telegram report sender (format nha dau tu).
Offline tests: build_summary voi report mau, send_telegram monkeypatch requests,
thieu credential -> skip. Khong co network.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reporters.telegram_sender import (
    build_summary, send_telegram, get_telegram_config,
)


def make_real_prices():
    """Du lieu gia that mau."""
    return {
        "prices": [
            {"symbol": "ACB", "price": "21,900", "change_percent": "(-2.01%)",
             "volume": "15,165,000", "trading_date": "31/07/2026 14:58",
             "source_url": "https://finance.vietstock.vn/ACB/thong-ke-giao-dich.htm"},
            {"symbol": "VCB", "price": "59,300", "change_percent": "(+4.96%)",
             "volume": "18,314,600", "trading_date": "31/07/2026 14:58",
             "source_url": "https://finance.vietstock.vn/VCB/thong-ke-giao-dich.htm"},
            {"symbol": "FPT", "price": "67,100", "change_percent": "(+0.15%)",
             "volume": "6,253,600", "trading_date": "31/07/2026 14:58",
             "source_url": "https://finance.vietstock.vn/FPT/thong-ke-giao-dich.htm"},
        ]
    }


class TestBuildSummary:
    """Test build_summary format nha dau tu."""

    def _write_reports(self, tmp_path, final=None):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        default_final = {
            "generated_at": "2026-08-02T10:00:00.000000",
            "pipeline": {"steps": {"connectivity": "ok", "discovery": "ok"}},
            "connectivity": {"total_sources": 2, "reachable": 2, "unreachable": 0,
                             "results": {}},
            "discovery": {},
        }
        (out / "final_report.json").write_text(
            json.dumps(final if final is not None else default_final),
            encoding="utf-8")
        (out / "quality_report.json").write_text(
            json.dumps({"generated_at": "2026-08-02T10:00:00"}), encoding="utf-8")

    def test_header(self, tmp_path):
        """Header moi + gio Viet Nam (UTC+7, khong dung generated_at)."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        assert "BÁO CÁO CHỨNG KHOÁN" in text
        assert "giờ Việt Nam, UTC+7" in text
        assert "Asia/Bangkok" not in text
        # generated_at 10:00 KHONG duoc dung - gio = now UTC + 7
        assert "Dữ liệu phiên: 31/07/2026" in text

    def test_header_timezone_utc_mock(self, tmp_path, monkeypatch):
        """Mock runner UTC -> hien thi dung +7."""
        import datetime as dt_mod
        self._write_reports(tmp_path)
        fixed_utc = dt_mod.datetime(2026, 8, 2, 10, 30, tzinfo=dt_mod.timezone.utc)
        monkeypatch.setattr("reporters.telegram_sender.datetime", dt_mod)
        # Patch datetime.now co dinh
        class FakeDT(dt_mod.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_utc if tz else fixed_utc
        monkeypatch.setattr("reporters.telegram_sender.datetime", FakeDT)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        # UTC 10:30 -> VN 17:30
        assert "17:30" in text
        assert "10:30" not in text

    def test_header_timezone_no_tzdata(self, tmp_path, monkeypatch):
        """Thieu tzdata -> fallback UTC+7 co dinh, van dung gio."""
        import builtins
        import datetime as dt_mod
        self._write_reports(tmp_path)

        class FakeDT(dt_mod.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt_mod.datetime(2026, 8, 2, 10, 30, tzinfo=dt_mod.timezone.utc)

        monkeypatch.setattr("reporters.telegram_sender.datetime", FakeDT)
        # ZoneInfo import fail (thieu tzdata)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "zoneinfo":
                raise ImportError("No module named 'zoneinfo'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        # UTC 10:30 + 7 = 17:30
        assert "17:30" in text

    def test_market_section(self, tmp_path):
        """THI TRUONG: so ma tang/giam/dung + tong KL."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        assert "THỊ TRƯỜNG" in text
        assert "Tăng: 2" in text
        assert "Giảm: 1" in text
        assert "Đứng giá: 0" in text
        # Tong KL: 15,165,000 + 18,314,600 + 6,253,600 = 39,733,200 -> 39.7 triệu
        assert "Tổng khối lượng" in text
        assert "39.7 triệu cổ phiếu" in text

    def test_watchlist_section(self, tmp_path):
        """WATCHLIST: KL don vi trieu (tr)."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        assert "WATCHLIST" in text
        assert "📌 ACB: 21,900 VND (-2.01%) | KL 15.2 tr" in text
        assert "📌 VCB: 59,300 VND (+4.96%) | KL 18.3 tr" in text
        # Khong con ngoac kep va khong con KL that
        assert "((-2.01%))" not in text
        assert "KL 15,165,000" not in text

    def test_highlights_section(self, tmp_path):
        """DIEM NHAN: top tang + top giam."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        assert "ĐIỂM NHẤN" in text
        assert "Tăng mạnh nhất: VCB (+4.96%)" in text
        assert "Giảm mạnh nhất: ACB (-2.01%)" in text

    def test_ai_section_long(self, tmp_path):
        """AI text >= 120 ky tu -> hien thi."""
        self._write_reports(tmp_path)
        long_analysis = ("Thị trường có xu hướng phân hóa rõ rệt trong phiên giao dịch. "
                         "Nhóm ngân hàng tăng mạnh nhờ dòng tiền lớn, trong khi cổ phiếu "
                         "vốn hóa lớn khác điều chỉnh nhẹ. ACB giảm hơn 2% còn VCB tăng "
                         "gần 5%, cho thấy dòng tiền đang tập trung vào nhóm ngân hàng. "
                         "Nhà đầu tư cần theo dõi sát thanh khoản và diễn biến khối ngoại "
                         "để đưa ra quyết định hợp lý, đồng thời thận trọng với biến động "
                         "ngắn hạn của các cổ phiếu còn lại trong danh sách.")
        text = build_summary(str(tmp_path), real_prices=make_real_prices(),
                             ai_analysis=long_analysis)
        assert "PHÂN TÍCH AI" in text
        assert "phân hóa" in text

    def test_ai_section_short_no_retry(self, tmp_path):
        """AI text ngan + khong co analyst -> bao 'chua dat'."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices(),
                             ai_analysis="Ngắn quá.")
        assert "PHÂN TÍCH AI" in text
        assert "chưa đạt (quá ngắn)" in text

    def test_ai_section_short_with_retry(self, tmp_path):
        """AI text ngan + co analyst retry -> dung text dai hon."""
        self._write_reports(tmp_path)
        mock_analyst = MagicMock()
        long_retry = ("Phân tích chi tiết: thị trường chứng khoán Việt Nam ghi nhận "
                      "phiên giao dịch tích cực với sự dẫn dắt của nhóm cổ phiếu ngân hàng. "
                      "VCB tăng mạnh nhất với gần 5%, phản ánh kỳ vọng tích cực về kết quả "
                      "kinh doanh. ACB điều chỉnh nhẹ hơn 2% do áp lực chốt lời ngắn hạn. "
                      "Các cổ phiếu khác có sự phân hóa, cần thận trọng khi giải ngân trong "
                      "ngắn hạn. Nhà đầu tư nên quan sát thêm diễn biến thanh khoản trước "
                      "khi đưa ra quyết định mua bán trong những phiên tới.")
        mock_analyst.analyze_with_prompt.return_value = long_retry
        text = build_summary(str(tmp_path), real_prices=make_real_prices(),
                             ai_analysis="Ngắn.", ai_analyst=mock_analyst)
        assert "PHÂN TÍCH AI" in text
        assert "Phân tích chi tiết" in text
        assert "chưa đạt" not in text
        mock_analyst.analyze_with_prompt.assert_called_once()

    def test_ai_short_retry_still_short(self, tmp_path):
        """Retry van ngan -> bao 'chua dat'."""
        self._write_reports(tmp_path)
        mock_analyst = MagicMock()
        mock_analyst.analyze_with_prompt.return_value = "Vẫn ngắn."
        text = build_summary(str(tmp_path), real_prices=make_real_prices(),
                             ai_analysis="Ngắn.", ai_analyst=mock_analyst)
        assert "chưa đạt (quá ngắn)" in text

    def test_disclaimer(self, tmp_path):
        """Disclaimer + nguon + thoi diem."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        assert "KHÔNG phải khuyến nghị đầu tư" in text
        assert "vietstock" in text
        assert "Thời điểm lấy dữ liệu" in text

    def test_no_pipeline_sections(self, tmp_path):
        """Khong con muc pipeline cu."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices())
        for old in ["KẾT NỐI", "DISCOVERY", "API PROFILES", "CAPABILITY",
                    "GHI CHÚ", "PIPELINE", "endpoint", "profiles", "capability"]:
            assert old not in text

    def test_no_prices(self, tmp_path):
        """Khong co gia -> bao khong co du lieu."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path))
        assert "Không có dữ liệu giá" in text

    def test_length_limit(self, tmp_path):
        """Text khong vuot 4000 ky tu."""
        self._write_reports(tmp_path)
        text = build_summary(str(tmp_path), real_prices=make_real_prices(),
                             ai_analysis="Phân tích dài. " * 100)
        assert len(text) <= 4000

    def test_ai_kept_whole_over_limit(self, tmp_path):
        """AI text dai + watchlist day du -> PHAN TICH AI khong bi cat,
        cat bot WATCHLIST truoc, len <= 4000."""
        self._write_reports(tmp_path)
        ai_tail = "Đây là phần kết thúc quan trọng của phân tích không được cắt bỏ."
        long_analysis = ("Thị trường chứng khoán Việt Nam phiên hôm nay ghi nhận "
                         "sự phân hóa rõ rệt giữa các nhóm cổ phiếu ngân hàng và "
                         "bất động sản. Dòng tiền tập trung vào nhóm cổ phiếu trụ "
                         "cột, giúp chỉ số giữ vững trong biên độ hẹp. ACB giảm "
                         "hơn 2% do áp lực chốt lời, trong khi VCB tăng gần 5% "
                         "nhờ kết quả kinh doanh tích cực. Nhà đầu tư cần theo "
                         "dõi sát diễn biến khối ngoại và thanh khoản để có quyết "
                         "định hợp lý. " * 8) + ai_tail
        assert len(long_analysis) > 2840  # Du de vuot gioi han khi + watchlist
        text = build_summary(str(tmp_path), real_prices=make_real_prices(),
                             ai_analysis=long_analysis)
        assert len(text) <= 4000
        # PHAN TICH AI tron ven - khong cat giua cau
        assert ai_tail in text
        # WATCHLIST bi cat bot (khong con day du 10 ma)
        assert "WATCHLIST" in text
        watch_count = text.count("📌")
        assert 0 <= watch_count < 10

    def test_html_escaped(self, tmp_path):
        """Ky tu HTML duoc escape."""
        self._write_reports(tmp_path)
        prices = make_real_prices()
        prices["prices"][0]["symbol"] = "A&B"
        text = build_summary(str(tmp_path), real_prices=prices)
        assert "&amp;" in text


class TestGetTelegramConfig:
    """Test doc cau hinh telegram."""

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        cfg = get_telegram_config({})
        assert cfg["enabled"] is True
        assert cfg["token"] == "secret-token"
        assert cfg["chat_id"] == "12345"

    def test_from_settings_config(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg = get_telegram_config({"token": "cfg-token", "chat_id": "cfg-chat"})
        assert cfg["enabled"] is True
        assert cfg["token"] == "cfg-token"

    def test_missing_credentials(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg = get_telegram_config({})
        assert cfg["enabled"] is False


class TestSendTelegram:
    """Test send_telegram (monkeypatch requests, khong network)."""

    def test_success(self, monkeypatch):
        """Gui thanh cong -> True."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_post = MagicMock(return_value=mock_response)

        with patch("reporters.telegram_sender.requests.post", mock_post):
            result = send_telegram("test", token="tok", chat_id="123")

        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.telegram.org/bottok/sendMessage"
        payload = call_args.kwargs["data"]
        assert payload["chat_id"] == "123"
        assert payload["parse_mode"] == "HTML"

    def test_retry_on_error(self, monkeypatch):
        """Loi mang -> retry -> False."""
        import requests
        mock_post = MagicMock(side_effect=requests.Timeout("timeout"))

        with patch("reporters.telegram_sender.requests.post", mock_post):
            with patch("reporters.telegram_sender.time.sleep"):
                result = send_telegram("test", token="tok", chat_id="123", retries=2)

        assert result is False
        assert mock_post.call_count == 3  # 1 + 2 retries

    def test_missing_credentials(self, monkeypatch):
        """Thieu credential -> False, khong goi network."""
        mock_post = MagicMock()
        with patch("reporters.telegram_sender.requests.post", mock_post):
            with patch.dict("os.environ", {}, clear=True):
                result = send_telegram("test")

        assert result is False
        mock_post.assert_not_called()

    def test_http_error(self, monkeypatch):
        """API tra ve non-200 -> False."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "unauthorized"
        mock_post = MagicMock(return_value=mock_response)

        with patch("reporters.telegram_sender.requests.post", mock_post):
            with patch("reporters.telegram_sender.time.sleep"):
                result = send_telegram("test", token="tok", chat_id="123")

        assert result is False
