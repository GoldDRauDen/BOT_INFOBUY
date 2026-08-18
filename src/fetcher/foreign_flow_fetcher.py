"""Fetch dong von ngoai (foreign flow) - OPTIONAL.

Chay:
    python -m src.fetcher.foreign_flow_fetcher

Hien tai KHONG co nguon free on dinh cho du lieu foreign flow.
- Neu khong co du lieu: log ro "foreign flow: khong co nguon on dinh, skip" + return 0.
- Quality gate KHONG fail pipeline.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_conn  # noqa: E402

logger = logging.getLogger("foreign_flow_fetcher")

# Cac URL da thu (recon truoc): khong nguon nao on dinh/free
CANDIDATE_SOURCES = [
    "https://ssi.com.vn/",           # yeu cau login
    "https://fiin.vn/",              # tra phi
]


def fetch_all(conn=None) -> int:
    """Thu fetch foreign flow; khong co nguon -> log + return 0 (khong fail)."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        for url in CANDIDATE_SOURCES:
            logger.info("Thu nguon foreign flow: %s", url)
        logger.warning("foreign flow: khong co nguon on dinh, skip")
        print("foreign flow: khong co nguon on dinh, skip")
        return 0
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        n = fetch_all()
        print(f"[foreign_flow_fetcher] Xong: {n} dong (0 = skip, khong co nguon)")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
