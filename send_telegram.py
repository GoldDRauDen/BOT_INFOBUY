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
