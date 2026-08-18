"""
AI Analyst - Goi Gemini API de phan tich du lieu that.
PHA 2: Phan tich DUNG SO lieu duoc cung cap, khong them so lieu khac.
Loi API hoac khong co du lieu -> tra None (khong bia).

Security: API key tu env GEMINI_API_KEY (CI: GitHub secrets).
"""
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests

try:
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.config_loader import load_settings


GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")
DEFAULT_MODEL = "gemini-flash-latest"
# Model da chay thanh cong thuc te o du an khac
FALLBACK_MODEL = "gemini-flash-latest"

# Text phan tich toi thieu (ngan hon = bi cat hoac qua ngan)
MIN_ANALYSIS_LENGTH = 250


def build_prompt(prices_report: Dict[str, Any],
                 extra_instruction: str = "") -> str:
    """
    Tao prompt phan tich tu prices.json.
    Chi dung so lieu co trong report.
    """
    prices = prices_report.get("prices", [])
    if not prices:
        return ""

    lines = [
        "Phân tích dữ liệu cổ phiếu Việt Nam dưới đây (dữ liệu THẬT từ vietstock, "
        "ngày giao dịch {date}):".format(date=prices[0].get("trading_date", "?")),
        "",
    ]
    for p in prices:
        lines.append(
            f"- {p.get('symbol')}: giá {p.get('price')} VND, "
            f"thay đổi {p.get('change_percent')}, "
            f"khối lượng {p.get('volume')}, "
            f"mở cửa {p.get('open')}, cao {p.get('high')}, thấp {p.get('low')}"
        )

    num_symbols = len(prices)
    lines.append("")
    lines.append(
        f"Yêu cầu: Viết MỘT đoạn văn phân tích liền mạch (prose), KHÔNG dùng bullet, "
        f"KHÔNG dùng cấu trúc Sentence N hay đánh số câu. Tối thiểu 3 câu, tối đa 5 câu, "
        f"tiếng Việt. Nội dung: (a) tóm tắt phiên giao dịch của {num_symbols} mã, "
        f"(b) điểm nổi bật, (c) cảnh báo rủi ro. "
        f"CHỈ nhắc đến {num_symbols} mã trong danh sách, KHÔNG thêm bất kỳ thông tin nào khác "
        f"(số lượng cổ phiếu toàn thị trường, chỉ số, tin tức, con số ngoài danh sách)."
    )
    if extra_instruction:
        lines.append("")
        lines.append(extra_instruction)
    return "\n".join(lines)


def postprocess_text(text: str) -> str:
    """
    Xoa ky tu dau danh sach (*, **, #, -, 'Sentence N:') roi normalize khoang trang.
    """
    if not text:
        return text
    # Xoa ky tu bullet dau dong (ke ca *Sentence 4*: dang co dau sao)
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*\*{1,2}\s*", "", line)
        cleaned = re.sub(r"^\s*Sentence\s*\d+\s*[:.*]*\s*", "", cleaned)
        cleaned = re.sub(r"^\s*(?:#|-|•)\s*", "", cleaned)
        # Xoa ky tu bullet con sot giua chuoi
        cleaned = cleaned.replace("**", "").replace("*", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    return " ".join(lines)


WATCHLIST_PATTERN = re.compile(r"\b(ACB|VCB|BID|FPT|HPG|VNM|VIC|VHM|GAS|VPB)\b")
VIETNAMESE_DIACRITICS = set("ếấộệữịảă")
ECHO_PHRASES = ["CHỈ nhắc", "Strict", "unmentioned", "rule:", "VN-Index"]


def extract_text_from_parts(parts: list) -> str:
    """
    Doc response dung: gop text cua cac part, BO QUA part co thought=True
    hoac text rong (gemini-flash-latest tra ve nhieu parts, part dau co the
    la suy nghi).
    """
    chunks = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if part.get("thought"):
            continue
        text = part.get("text")
        if text and str(text).strip():
            chunks.append(str(text).strip())
    return "\n".join(chunks)


def validate_analysis(text: str, min_length: int = MIN_ANALYSIS_LENGTH) -> bool:
    """
    Validation manh:
    - text >= {MIN_ANALYSIS_LENGTH} ky tu (ngan hon = bi cat hoac qua ngan)
    - chua it nhat 1 ma watchlist (regex \b(ACB|VCB|BID|FPT|HPG|VNM|VIC|VHM|GAS|VPB)\b)
    - co it nhat 2 ky tu tieng Viet co dau (vd 'ếấộệữịảă')
    - KHONG chua cac cum echo prompt (CHỈ nhắc, Strict, unmentioned, rule:, VN-Index)
      va khong chua 'Sentence' hoac '**'
    Tra False neu vi pham.
    """.format(MIN_ANALYSIS_LENGTH=MIN_ANALYSIS_LENGTH)
    if not text or len(text) < min_length:
        return False
    if not WATCHLIST_PATTERN.search(text):
        return False
    diacritics = [c for c in text if c in VIETNAMESE_DIACRITICS]
    if len(diacritics) < 2:
        return False
    lower = text.lower()
    if "sentence" in lower or "**" in text:
        return False
    for phrase in ECHO_PHRASES:
        if phrase.lower() in lower:
            return False
    return True


class AiAnalyst:
    """Goi Gemini API phan tich."""

    def __init__(self, logger: logging.Logger = None, config: Dict[str, Any] = None):
        self.logger = logger or logging.getLogger("ai_analyst")
        if config is None:
            try:
                settings = load_settings()
                config = settings.get("ai", {})
            except Exception:
                config = {}
        self.config = config or {}
        self.model = self.config.get("model", DEFAULT_MODEL)
        self.timeout = int(self.config.get("timeout", 30))
        self.last_model = None  # Model thuc su thanh cong o lan goi gan nhat

    def _post_generate(self, model: str, api_key: str, prompt: str):
        """
        Goi Gemini generateContent.
        - Payload: maxOutputTokens 8192, thinkingConfig {'thinkingBudget': 0}
          (tat suy nghi - gemini-flash-latest tieu thu token budget cho thinking).
        - An toan: neu 400/error voi thinkingConfig -> thu lai KHONG co
          thinkingConfig (van giu maxOutputTokens 8192).
        - 429 (rate limit): log 'rate limited, waiting 60s...', time.sleep(60),
          thu lai 1 lan cung payload. Van 429 -> (None, 429).
        Tra ve (data, status_code) hoac (None, status).
        """
        url = GEMINI_URL.format(model=model)
        base_config = {
            "temperature": 0.4,
            "maxOutputTokens": 8192,
            "candidateCount": 1,
        }

        def _post(payload):
            """Gui request + retry 429 (60s) 1 lan. Tra (data, status)."""
            for attempt in range(2):
                try:
                    resp = requests.post(
                        url, params={"key": api_key}, json=payload,
                        timeout=self.timeout,
                        headers={"Content-Type": "application/json"},
                    )
                    if resp.status_code == 200:
                        return resp.json(), 200
                    if resp.status_code == 429:
                        self.logger.warning(
                            f"Gemini {model}: rate limited, waiting 60s..."
                            f" (lan {attempt + 1})"
                        )
                        if attempt == 0:
                            time.sleep(60)
                            continue
                        return None, 429
                    return None, resp.status_code
                except requests.RequestException as e:
                    self.logger.warning(f"Gemini {model} loi: {e}")
                    return None, 0
            return None, 429

        # Lan 1: co thinkingConfig
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": dict(base_config, thinkingConfig={"thinkingBudget": 0}),
        }
        data, status = _post(payload)
        if status == 200 or status != 400:
            # OK hoac loi khong phai 400 (404/429/5xx...) - khong thu thinkingConfig fallback
            return data, status
        self.logger.warning(
            f"Gemini {model}: 400 voi thinkingConfig - thu lai khong thinkingConfig"
        )

        # Lan 2: khong thinkingConfig (400 do model khong ho tro)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": base_config,
        }
        return _post(payload)

    def analyze(self, prices_report: Dict[str, Any],
                api_key: str = None) -> Optional[str]:
        """
        Phan tich prices_report qua Gemini.
        Tra text phan tich hoac None (loi API / khong du lieu / thieu key).
        """
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            self.logger.warning("SKIP: thieu GEMINI_API_KEY (env)")
            return None

        prompt = build_prompt(prices_report)
        if not prompt:
            self.logger.warning("SKIP: khong co du lieu gia de phan tich")
            return None

        models_to_try = [self.model]
        if FALLBACK_MODEL != self.model:
            models_to_try.append(FALLBACK_MODEL)

        # Retry 1 lan neu text khong dat validation
        for attempt in range(2):
            for model in models_to_try:
                try:
                    data, status = self._post_generate(model, api_key, prompt)
                    if status == 200:
                        candidate = (data.get("candidates") or [{}])[0]
                        parts = candidate.get("content", {}).get("parts", [])
                        finish = candidate.get("finishReason")
                        # Diagnostic: log day du response de tim nguyen nhan cat
                        self.logger.info(
                            f"Gemini {model}: finishReason={finish}, parts={len(parts)}"
                        )
                        for i, part in enumerate(parts):
                            if isinstance(part, dict):
                                ptxt = str(part.get("text", ""))[:200]
                                self.logger.info(
                                    f"  part[{i}] thought={part.get('thought', False)} "
                                    f"text={ptxt!r}"
                                )
                        if finish == "MAX_TOKENS":
                            self.logger.warning(
                                f"Gemini {model}: finishReason=MAX_TOKENS - text bi cat"
                            )
                        text = extract_text_from_parts(parts)
                        if text:
                            text = postprocess_text(text)
                            if validate_analysis(text):
                                self.logger.info(
                                    f"Gemini {model} phan tich OK ({len(text)} chars)"
                                )
                                self.last_model = model
                                return text
                            self.logger.warning(
                                f"Gemini {model}: khong dat validation (lan {attempt + 1})"
                            )
                        else:
                            self.logger.warning(f"Gemini {model}: response rong")
                    else:
                        self.logger.warning(f"Gemini {model} -> {status}")
                except Exception as e:
                    self.logger.warning(f"Gemini {model} loi: {e}")
                # Loi (404/429/5xx/timeout/rong) -> thu model tiep theo
            # Het models -> retry 1 lan voi prompt co them yeu cau sach
            if attempt == 0:
                prompt = build_prompt(
                    prices_report,
                    extra_instruction=(
                        "Lưu ý: phải viết liền mạch, không bullet, không đánh số câu, "
                        "chỉ dùng dữ liệu trong danh sách."
                    ),
                )
        return None

    def analyze_with_prompt(self, extra_instruction: str,
                            prices_report: Dict[str, Any],
                            api_key: str = None) -> Optional[str]:
        """
        Goi Gemini lai voi instruction bo sung (vi du: yeu cau chi tiet hon).
        Dung build_prompt voi extra_instruction. Tra None neu loi.
        """
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        prompt = build_prompt(prices_report, extra_instruction=extra_instruction)
        if not prompt:
            return None

        models_to_try = [self.model]
        if FALLBACK_MODEL != self.model:
            models_to_try.append(FALLBACK_MODEL)

        for model in models_to_try:
            try:
                data, status = self._post_generate(model, api_key, prompt)
                if status == 200:
                    candidate = (data.get("candidates") or [{}])[0]
                    parts = candidate.get("content", {}).get("parts", [])
                    finish = candidate.get("finishReason")
                    self.logger.info(
                        f"Gemini {model} (retry): finishReason={finish}, parts={len(parts)}"
                    )
                    if finish == "MAX_TOKENS":
                        self.logger.warning(
                            f"Gemini {model} (retry): finishReason=MAX_TOKENS - text bi cat"
                        )
                    text = extract_text_from_parts(parts)
                    if text:
                        text = postprocess_text(text)
                        self.logger.info(
                            f"Gemini {model} (retry) phan tich OK ({len(text)} chars)"
                        )
                        self.last_model = model
                        return text
                    self.logger.warning(f"Gemini {model} (retry): response rong")
                else:
                    self.logger.warning(f"Gemini {model} (retry) -> {status}")
            except Exception as e:
                self.logger.warning(f"Gemini {model} (retry) loi: {e}")
        return None


def run_ai_analysis(logger: logging.Logger = None) -> Optional[str]:
    """Doc prices.json + goi Gemini. Tra text hoac None."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    base_dir = Path(__file__).parent.parent.parent
    prices_path = base_dir / "output" / "real_data" / "prices.json"
    if not prices_path.exists():
        logger.warning("Khong co output/real_data/prices.json - bo qua AI phan tich")
        return None
    try:
        with open(prices_path, "r", encoding="utf-8") as f:
            prices_report = json.load(f)
    except Exception as e:
        logger.warning(f"Loi doc prices.json: {e}")
        return None

    analyst = AiAnalyst(logger=logger)
    text = analyst.analyze(prices_report)
    if text:
        # Luu ket qua
        out_path = base_dir / "output" / "real_data" / "ai_analysis.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "model": analyst.last_model or analyst.model,
                "analysis": text,
            }, f, indent=2, ensure_ascii=False)
        print(f"\n  AI phan tich OK ({len(text)} chars)")
        return text
    logger.warning("Khong co ket qua AI phan tich")
    return None
