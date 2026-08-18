"""Test chong look-ahead bias: fundamentals join theo reported_date.

Dam bao: KHONG co du lieu co reported_date > ngay lo mau (current_date)
lo vao tap du lieu cua ngay do.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.db.database import get_conn  # noqa: E402
from src.indicators.fundamental_screening import fundamentals_as_of  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = get_conn(tmp_path / "la.db")
    # Seed fundamentals: 2 ky - quy 1 (reported 2023-04-20) + quy 2 (reported 2023-07-20)
    c.executemany(
        "INSERT OR REPLACE INTO fundamentals_quarterly "
        "(symbol, quarter_end, reported_date, is_estimated, eps, pe_ratio, pb_ratio, "
        "roe, roa, bvps, revenue, net_income, dividend_yield) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("AAA", "2023-03-31", "2023-04-20", 1, 1000.0, 10.0, 1.5,
             15.0, 2.0, 20000.0, 1e9, 5e8, 0.02),
            ("AAA", "2023-06-30", "2023-07-20", 1, 1100.0, 9.0, 1.4,
             16.0, 2.1, 21000.0, 1.1e9, 5.5e8, 0.02),
        ],
    )
    c.commit()
    yield c
    c.close()


class TestLookAheadBias:
    def test_as_of_before_first_report_is_empty(self, conn):
        """Truoc 2023-04-20: chua co ky nao duoc cong bo."""
        df = fundamentals_as_of(conn, "AAA", "2023-04-19")
        assert df.empty

    def test_as_of_after_q1_includes_only_q1(self, conn):
        """Sau 2023-04-20 truoc 2023-07-20: chi co quy 1."""
        df = fundamentals_as_of(conn, "AAA", "2023-06-01")
        assert list(df["quarter_end"]) == ["2023-03-31"]

    def test_as_of_after_q2_includes_both(self, conn):
        df = fundamentals_as_of(conn, "AAA", "2023-07-21")
        assert len(df) == 2

    def test_no_future_row_leaks(self, conn):
        """Khong co dong nao co reported_date > as_of_date trong ket qua."""
        for as_of in ["2023-04-19", "2023-06-01", "2023-07-20", "2023-07-21"]:
            df = fundamentals_as_of(conn, "AAA", as_of)
            assert (df["reported_date"] <= as_of).all()

    def test_quarter_end_ordering(self, conn):
        df = fundamentals_as_of(conn, "AAA", "2023-12-31")
        assert df["quarter_end"].is_monotonic_increasing
