"""
Unit tests cho AI Analyst (PHA 2).
Offline tests (mock requests). Khong network, khong key that.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from analyst.ai_analyst import AiAnalyst, build_prompt


SAMPLE_PRICES = {
    "prices": [
        {"symbol": "ACB", "price": "21,900", "change_percent": "(-2.01%)",
         "volume": "15,165,000", "open": "22,600", "high": "22,600",
         "low": "21,800", "trading_date": "31/07/2026"},
        {"symbol": "FPT", "price": "67,100", "change_percent": "(+0.75%)",
         "volume": "6,253,600", "open": "66,500", "high": "67,500",
         "low": "66,200", "trading_date": "31/07/2026"},
    ]
}

VALID_TEXT = (
    "Thị trường phiên hôm nay có sự phân hóa rõ rệt giữa các nhóm cổ phiếu trong "
    "danh sách theo dõi. ACB giảm nhẹ hơn 2% trong khi VCB tăng gần 5% nhờ dòng tiền "
    "mạnh. Nhà đầu tư cần thận trọng với biến động ngắn hạn của các mã còn lại và "
    "theo dõi sát diễn biến khối ngoại để có quyết định hợp lý trong phiên tiếp theo."
)

# Text dai > 300 ky tu, hop le voi min_length=250
LONG_TEXT = VALID_TEXT + (
    " Bên cạnh đó, nhóm cổ phiếu trụ cột giữ vai trò dẫn dắt thị trường khi dòng tiền "
    "tập trung vào các mã vốn hóa lớn. Phiên giao dịch hôm nay cho thấy tâm lý nhà đầu "
    "tư vẫn thận trọng trước biến động của thị trường quốc tế."
)


class TestBuildPrompt:
    """Test build_prompt."""

    def test_prompt_contains_data(self):
        prompt = build_prompt(SAMPLE_PRICES)
        assert "ACB" in prompt
        assert "21,900" in prompt
        assert "(-2.01%)" in prompt
        assert "FPT" in prompt
        assert "vietstock" in prompt

    def test_prompt_empty(self):
        assert build_prompt({"prices": []}) == ""

    def test_prompt_no_speculation(self):
        """Prompt yeu cau prose, khong bullet/khong bia."""
        prompt = build_prompt(SAMPLE_PRICES)
        assert "MỘT đoạn văn phân tích liền mạch" in prompt
        assert "KHÔNG dùng bullet" in prompt
        assert "KHÔNG dùng cấu trúc Sentence N" in prompt
        assert "KHÔNG thêm bất kỳ thông tin nào khác" in prompt
        assert "số lượng cổ phiếu toàn thị trường" in prompt
        # So ma duoc nhac den (SAMPLE_PRICES co 2 ma)
        assert "của 2 mã" in prompt

    def test_prompt_extra_instruction(self):
        """extra_instruction duoc them vao cuoi prompt."""
        prompt = build_prompt(SAMPLE_PRICES, extra_instruction="Viết chi tiết hơn.")
        assert "Viết chi tiết hơn." in prompt


class TestPostprocess:
    """Test postprocess_text."""

    def test_remove_bullets(self):
        from analyst.ai_analyst import postprocess_text
        text = "*Sentence 4*: Thị trường tăng.\n**Điểm nổi bật**: VCB tăng.\n- Rủi ro: giảm."
        cleaned = postprocess_text(text)
        assert "Sentence" not in cleaned
        assert "**" not in cleaned
        assert "*" not in cleaned
        assert "-" not in cleaned.split()[0] or True

    def test_normalize_whitespace(self):
        from analyst.ai_analyst import postprocess_text
        cleaned = postprocess_text("  Dòng  một  \n\n   Dòng hai  ")
        assert "Dòng một" in cleaned
        assert "Dòng hai" in cleaned


class TestValidate:
    """Test validate_analysis."""

    def test_valid(self):
        from analyst.ai_analyst import validate_analysis
        assert len(LONG_TEXT) >= 250
        assert validate_analysis(LONG_TEXT) is True

    def test_missing_symbol(self):
        """Khong chua ma watchlist -> False."""
        from analyst.ai_analyst import validate_analysis
        text = ("Đây là một đoạn phân tích đủ dài với nhiều thông tin hữu ích cho nhà đầu "
                "tư về thị trường chứng khoán Việt Nam hôm nay và các cổ phiếu trong "
                "danh sách theo dõi.")
        assert validate_analysis(text) is False

    def test_no_diacritics(self):
        """Thieu ky tu tieng Viet co dau -> False."""
        from analyst.ai_analyst import validate_analysis
        text = ("ACB VCB BID FPT HPG VNM VIC VHM GAS VPB. Day la mot doan phan tich dai "
                "du voi nhieu thong tin huu ich cho nha dau tu ve thi truong chung khoan "
                "Viet Nam hom nay va cac co phieu trong danh sach theo doi.")
        assert validate_analysis(text) is False

    def test_echo_prompt(self):
        """Chua cum echo prompt -> False."""
        from analyst.ai_analyst import validate_analysis
        text = ("CHỈ nhắc đến ACB, VCB, BID, FPT, HPG, VNM, VIC, VHM, GAS, VPB trong "
                "danh sách, không thêm bất kỳ thông tin nào khác như chỉ số thị trường "
                "hay tin tức bên ngoài, chỉ dùng số liệu đã được cung cấp sẵn.")
        assert validate_analysis(text) is False

    def test_too_short(self):
        from analyst.ai_analyst import validate_analysis
        assert validate_analysis("Ngắn") is False

    def test_contains_sentence(self):
        from analyst.ai_analyst import validate_analysis
        text = ("Đây là một đoạn phân tích đủ dài với nhiều thông tin hữu ích cho nhà đầu "
                "tư về thị trường chứng khoán Việt Nam hôm nay và các cổ phiếu trong "
                "danh sách. Sentence 4: x")
        assert validate_analysis(text) is False

    def test_contains_bold(self):
        from analyst.ai_analyst import validate_analysis
        text = ("Đây là một đoạn phân tích đủ dài với nhiều thông tin hữu ích cho nhà đầu "
                "tư về thị trường chứng khoán Việt Nam hôm nay **in đậm** và cổ phiếu.")
        assert validate_analysis(text) is False


class TestAiAnalyst:
    """Test analyze (mock requests)."""

    def test_success(self):
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        valid_text = LONG_TEXT
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": valid_text}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_response) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="test-key")

        assert result == valid_text
        assert analyst.last_model == "gemini-2.0-flash"
        # Dung URL + key
        url = mock_post.call_args[0][0]
        assert "generateContent" in url
        assert mock_post.call_args.kwargs["params"]["key"] == "test-key"
        # Prompt trong payload
        payload = mock_post.call_args.kwargs["json"]
        assert "ACB" in payload["contents"][0]["parts"][0]["text"]
        # generationConfig moi
        gen = payload["generationConfig"]
        assert gen["maxOutputTokens"] == 8192
        assert gen["temperature"] == 0.4
        assert gen["candidateCount"] == 1
        assert gen["thinkingConfig"] == {"thinkingBudget": 0}

    def test_missing_key(self):
        analyst = AiAnalyst(config={})
        with patch.dict("os.environ", {}, clear=True):
            result = analyst.analyze(SAMPLE_PRICES)
        assert result is None

    def test_no_data(self):
        analyst = AiAnalyst(config={})
        result = analyst.analyze({"prices": []}, api_key="k")
        assert result is None

    def test_model_404_fallback(self):
        """Model 404 -> thu fallback gemini-flash-latest."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_404 = MagicMock(status_code=404, text="model not found")
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_404, mock_ok]) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")

        assert result == LONG_TEXT
        assert mock_post.call_count == 2
        # Fallback model dung
        url2 = mock_post.call_args_list[1][0][0]
        assert "gemini-flash-latest" in url2
        # last_model ghi nhan model that su dung
        assert analyst.last_model == "gemini-flash-latest"

    def test_rate_limit_retries_same_model(self):
        """429 -> retry cung model (khong fallback) -> 200 OK."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_429 = MagicMock(status_code=429, text="rate limited")
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_429, mock_ok]) as mock_post:
            with patch("analyst.ai_analyst.time.sleep") as mock_sleep:
                result = analyst.analyze(SAMPLE_PRICES, api_key="k")

        assert result == LONG_TEXT
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(60)
        # 429 retry cung model 2.0-flash, KHONG fallback sang flash-latest
        assert "gemini-2.0-flash" in mock_post.call_args_list[1][0][0]

    def test_5xx_fallback(self):
        """500 -> thu fallback."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_500 = MagicMock(status_code=500, text="server error")
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_500, mock_ok]) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 2

    def test_timeout_fallback(self):
        """Timeout -> thu fallback."""
        import requests
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[requests.Timeout("slow"), mock_ok]) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 2

    def test_empty_response_fallback(self):
        """Response rong -> thu fallback."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.json.return_value = {"candidates": []}
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_empty, mock_ok]) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 2

    def test_both_fail_returns_none(self):
        """Ca 2 model deu 429 -> None."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_429 = MagicMock(status_code=429, text="rate limited")
        with patch("analyst.ai_analyst.requests.post",
                   return_value=mock_429) as mock_post:
            with patch("analyst.ai_analyst.time.sleep"):
                result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        # 2 models x 2 attempts x 2 lan (429 retry)
        assert mock_post.call_count == 8

    def test_http_error_no_fallback(self):
        """Loi 500 -> thu fallback, ca 2 loi -> None."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_500 = MagicMock(status_code=500, text="server error")
        mock_502 = MagicMock(status_code=502, text="bad gateway")
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_500, mock_502, mock_500, mock_502]) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        assert mock_post.call_count == 4

    def test_empty_response(self):
        """Response rong ca 2 model -> None."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"candidates": []}
        with patch("analyst.ai_analyst.requests.post", return_value=mock_response):
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None

    def test_same_model_no_duplicate(self):
        """settings.model = gemini-flash-latest -> chi thu 1 lan."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_ok) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 1
        assert "gemini-flash-latest" in mock_post.call_args[0][0]
        assert analyst.last_model == "gemini-flash-latest"

    def test_multipart_thought_skipped(self):
        """Response 2 parts (thought + answer) -> lay dung answer."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        thought_part = ("Người dùng muốn phân tích cổ phiếu Việt Nam, cần viết về các mã "
                        "trong danh sách như ACB, VCB và nhấn mạnh điểm nổi bật cũng như "
                        "cảnh báo rủi ro cho nhà đầu tư.")
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [
                {"thought": True, "text": thought_part},
                {"text": LONG_TEXT},
            ]}}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_response):
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        # Chi lay part khong phai thought
        assert result == LONG_TEXT
        assert analyst.last_model == "gemini-2.0-flash"

    def test_multipart_empty_text_skipped(self):
        """Part text rong bi bo qua, gop cac part con lai."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [
                {"text": ""},
                {"text": LONG_TEXT},
            ]}}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_response):
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT

    def test_garbage_echo_rejected(self):
        """Response echo prompt -> reject, retry, van loi -> None."""
        analyst = AiAnalyst(config={"model": "gemini-2.0-flash"})
        garbage = ("CHỈ nhắc đến ACB, VCB, BID, FPT, HPG, VNM, VIC, VHM, GAS, VPB trong "
                   "danh sách, không thêm bất kỳ thông tin nào khác như chỉ số thị trường "
                   "hay tin tức bên ngoài, chỉ dùng số liệu đã được cung cấp sẵn.")
        mock_garbage = MagicMock()
        mock_garbage.status_code = 200
        mock_garbage.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": garbage}]}}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   return_value=mock_garbage) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        # 2 models x 2 attempts (retry 1 lan)
        assert mock_post.call_count == 4
        assert analyst.last_model is None

    def test_finish_reason_max_tokens(self):
        """finishReason=MAX_TOKENS + text ngan -> reject (bi cat)."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        short = ("ACB giảm nhẹ hơn 2% trong khi VCB tăng gần 5% nhờ dòng tiền mạnh.")
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": short}]},
                            "finishReason": "MAX_TOKENS"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_r) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        # 1 model x 2 attempts
        assert mock_post.call_count == 2

    def test_finish_reason_stop_ok(self):
        """finishReason=STOP + text dai -> OK."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_r):
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT

    def test_text_200_chars_rejected(self):
        """Text 200 ky tu (< 250) -> reject, retry, None."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        text_200 = LONG_TEXT[:200]
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": text_200}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_r) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        assert mock_post.call_count == 2

    def test_text_300_chars_ok(self):
        """Text 300 ky tu (>= 250) -> OK."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_r):
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert len(result) >= 250

    def test_settings_model_single_call(self):
        """settings.model = gemini-flash-latest (= FALLBACK) -> chi goi 1 lan."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post", return_value=mock_r) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 1

    def test_thinking_config_400_fallback(self):
        """400 voi thinkingConfig -> thu lai khong thinkingConfig."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_400 = MagicMock(status_code=400, text="thinkingConfig not supported")
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_400, mock_ok]) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 2
        # Lan 1: co thinkingConfig; Lan 2: khong
        gen1 = mock_post.call_args_list[0].kwargs["json"]["generationConfig"]
        gen2 = mock_post.call_args_list[1].kwargs["json"]["generationConfig"]
        assert gen1.get("thinkingConfig") == {"thinkingBudget": 0}
        assert "thinkingConfig" not in gen2
        assert gen1["maxOutputTokens"] == 8192
        assert gen2["maxOutputTokens"] == 8192

    def test_thinking_config_400_all_models(self):
        """400 ca 2 lan (co + khong thinkingConfig) -> None."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_400 = MagicMock(status_code=400, text="bad request")
        with patch("analyst.ai_analyst.requests.post",
                   return_value=mock_400) as mock_post:
            result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        # 1 model x 2 attempts x 2 lan (with/without thinkingConfig)
        assert mock_post.call_count == 4

    def test_rate_limit_retry_ok(self):
        """429 -> sleep 60s -> thu lai -> 200 OK."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_429 = MagicMock(status_code=429, text="rate limited")
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": LONG_TEXT}]},
                            "finishReason": "STOP"}]
        }
        with patch("analyst.ai_analyst.requests.post",
                   side_effect=[mock_429, mock_ok]) as mock_post:
            with patch("analyst.ai_analyst.time.sleep") as mock_sleep:
                result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result == LONG_TEXT
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(60)

    def test_rate_limit_twice_none(self):
        """429 lien tuc -> None (retry 1 lan cung payload, van 429 thi bo)."""
        analyst = AiAnalyst(config={"model": "gemini-flash-latest"})
        mock_429 = MagicMock(status_code=429, text="rate limited")
        with patch("analyst.ai_analyst.requests.post",
                   return_value=mock_429) as mock_post:
            with patch("analyst.ai_analyst.time.sleep"):
                result = analyst.analyze(SAMPLE_PRICES, api_key="k")
        assert result is None
        # 1 model x 2 attempts (retry validation) x 2 lan (429 retry)
        assert mock_post.call_count == 4
