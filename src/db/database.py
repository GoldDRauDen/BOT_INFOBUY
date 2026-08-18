"""Lop DB truy xuat du lieu BOT_INFOBUY (SQLite, khong ORM).

Ham dung chung:
- connect(db_path): mo ket noi.
- init_db(conn): tao bang theo data/schema.sql.
- upsert(conn, table, rows, conflict_cols): ghi/cap nhat theo PRIMARY KEY, chay lai khong trung lap.
- get_conn(): ket noi mac dinh toi data/bot_buy.db (tu dong init).
"""
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Thu muc goc du an (2 cap tu src/db/)
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bot_buy.db"
SCHEMA_PATH = PROJECT_ROOT / "data" / "schema.sql"

logger = logging.getLogger(__name__)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Mo ket noi SQLite, bat FOREIGN KEY."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Tao toan bo bang tu data/schema.sql (idempotent)."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


def get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Mo ket noi + dam bao schema da duoc tao."""
    conn = connect(db_path)
    init_db(conn)
    return conn


def _clean_value(value: Any) -> Any:
    """Chuyen NaN/None ve None de khong loi SQLite."""
    if value is None:
        return None
    # pandas float NaN
    try:
        if value != value:  # NaN != NaN
            return None
    except Exception:
        pass
    return value


def upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[Dict[str, Any]],
    conflict_cols: List[str],
) -> int:
    """Upsert danh sach row dict vao bang.

    Dung ON CONFLICT(<conflict_cols>) DO UPDATE de chay lai khong trung lap.
    Tra ve so dong da ghi.
    """
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    quoted = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    conflict = ", ".join(f'"{c}"' for c in conflict_cols)
    updates = ", ".join(f'"{c}"=excluded."{c}"' for c in cols if c not in conflict_cols)
    sql = (
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) '
        f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}'
    )
    values = [
        tuple(_clean_value(row.get(c)) for c in cols)
        for row in rows
    ]
    with conn:
        conn.executemany(sql, values)
    return len(values)
