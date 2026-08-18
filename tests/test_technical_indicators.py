"""Unit tests cho technical_indicators (cong thuc tay, sai so < 0.01%).

- RSI14 (Wilder), MACD (12/26/9 EMA), MA20/50, ATR14 (Wilder), volume_ma20.
- So sanh voi tinh toan tay bang pandas thuong truc.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.indicators.technical_indicators import compute_indicators  # noqa: E402


def _make_df(n=260, seed=42):
    """Sinh du lieu gia nhan tao deterministic."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.5, n))
    close = np.maximum(close, 5.0)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    volume = rng.integers(100_000, 5_000_000, n).astype(float)
    dates = pd.date_range("2023-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "trade_date": dates,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def _hand_indicators(df):
    """Tinh tay theo dung cong thuc spec (dung cho doi chieu)."""
    out = df.copy().sort_values("trade_date")
    close = out["close"].astype(float)
    # MA
    ma20 = close.rolling(20, min_periods=20).mean()
    ma50 = close.rolling(50, min_periods=50).mean()
    # RSI Wilder
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    # ATR Wilder
    prev_close = close.shift(1)
    tr = pd.concat(
        [(out["high"] - out["low"]),
         (out["high"] - prev_close).abs(),
         (out["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    # Volume MA20
    vma20 = out["volume"].rolling(20, min_periods=20).mean()
    return ma20, ma50, rsi, macd, macd_signal, atr, vma20


class TestIndicators:
    def test_ma_close_to_hand(self):
        df = _make_df()
        out = compute_indicators(df)
        ma20, ma50, *_ = _hand_indicators(df)
        # So sanh phan co du lieu
        valid = out["ma20"].notna()
        assert np.allclose(out["ma20"][valid], ma20[valid], rtol=1e-4)
        valid50 = out["ma50"].notna()
        assert np.allclose(out["ma50"][valid50], ma50[valid50], rtol=1e-4)

    def test_rsi_close_to_hand(self):
        df = _make_df()
        out = compute_indicators(df)
        _, _, rsi, *_ = _hand_indicators(df)
        valid = out["rsi14"].notna() & rsi.notna()
        assert np.allclose(out["rsi14"][valid], rsi[valid], rtol=1e-4)

    def test_rsi_extremes(self):
        """Gia tang lien tuc -> RSI = 100; giam lien tuc -> RSI ~ 0."""
        up = pd.DataFrame({
            "trade_date": pd.date_range("2023-01-02", periods=60, freq="B").strftime("%Y-%m-%d"),
            "open": np.linspace(100, 200, 60),
            "high": np.linspace(101, 201, 60),
            "low": np.linspace(99, 199, 60),
            "close": np.linspace(100, 200, 60),
            "volume": np.full(60, 1_000_000.0),
        })
        out = compute_indicators(up)
        assert out["rsi14"].dropna().iloc[-1] == pytest.approx(100.0, abs=1e-6)

        down = up.copy()
        down["open"] = np.linspace(200, 100, 60)
        down["high"] = np.linspace(201, 101, 60)
        down["low"] = np.linspace(199, 99, 60)
        down["close"] = np.linspace(200, 100, 60)
        out2 = compute_indicators(down)
        assert out2["rsi14"].dropna().iloc[-1] < 0.01

    def test_macd_close_to_hand(self):
        df = _make_df()
        out = compute_indicators(df)
        _, _, _, macd, macd_signal, _, _ = _hand_indicators(df)
        valid = out["macd"].notna()
        assert np.allclose(out["macd"][valid], macd[valid], rtol=1e-4)
        assert np.allclose(out["macd_signal"][valid], macd_signal[valid], rtol=1e-4)

    def test_atr_close_to_hand(self):
        df = _make_df()
        out = compute_indicators(df)
        _, _, _, _, _, atr, _ = _hand_indicators(df)
        valid = out["atr14"].notna()
        assert np.allclose(out["atr14"][valid], atr[valid], rtol=1e-4)

    def test_volume_ma20_close_to_hand(self):
        df = _make_df()
        out = compute_indicators(df)
        vma20 = _hand_indicators(df)[-1]
        valid = out["volume_ma20"].notna()
        assert np.allclose(out["volume_ma20"][valid], vma20[valid], rtol=1e-4)

    def test_ma200_nan_before_200_rows(self):
        df = _make_df(n=120)
        out = compute_indicators(df)
        assert out["ma200"].isna().all()  # < 200 dong -> chua co MA200

    def test_no_indicators_first_19_rows(self):
        """Truoc ngay thu 20: ma20 NaN (min_periods)."""
        df = _make_df()
        out = compute_indicators(df)
        assert out["ma20"].iloc[:19].isna().all()
        assert out["ma20"].iloc[19] == pytest.approx(df["close"].iloc[:20].mean(), rel=1e-6)
