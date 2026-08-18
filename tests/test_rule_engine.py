"""Unit tests cho rule engine (YAML parse + score).

Khong goi DB that - dung snapshot nhan tao qua evaluate_rule/load_rules.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.screening.rule_engine import load_rules, evaluate_rule  # noqa: E402

RULES_YAML = """
rules:
  - name: test_rule_1
    weight: 2.0
    conditions:
      - "close > ma20"
      - "rsi14 > 50"
  - name: test_rule_2
    weight: 1.0
    conditions:
      - "volume > volume_ma20 * 1.5"
"""


def _row(**overrides) -> pd.DataFrame:
    base = {
        "close": 12000.0,
        "ma20": 11000.0,
        "ma50": 10500.0,
        "rsi14": 60.0,
        "volume": 3_000_000.0,
        "volume_ma20": 1_000_000.0,
        "macd": 100.0,
        "macd_signal": 50.0,
    }
    base.update(overrides)
    return pd.DataFrame([base])


class TestLoadRules:
    def test_parse_valid_yaml(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text(RULES_YAML, encoding="utf-8")
        rules = load_rules(path)
        assert len(rules) == 2
        assert rules[0]["name"] == "test_rule_1"
        assert rules[0]["weight"] == 2.0
        assert len(rules[0]["conditions"]) == 2

    def test_default_weight_is_one(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text("rules:\n  - name: r\n    conditions: ['close > ma20']\n", encoding="utf-8")
        rules = load_rules(path)
        assert rules[0]["weight"] == 1.0

    def test_invalid_condition_raises(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text(
            "rules:\n  - name: r\n    conditions: ['close >>> bogus (']\n", encoding="utf-8"
        )
        with pytest.raises(ValueError):
            load_rules(path)

    def test_empty_rules_raises(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text("rules: []\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_rules(path)

    def test_missing_name_raises(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text("rules:\n  - conditions: ['close > ma20']\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_rules(path)


class TestEvaluateRule:
    def test_all_conditions_match(self):
        rule = {"name": "r", "weight": 2.0, "conditions": ["close > ma20", "rsi14 > 50"]}
        score, matched = evaluate_rule(_row(), rule)
        assert score == 4.0  # 2 condition x weight 2.0
        assert len(matched) == 2

    def test_partial_match(self):
        rule = {"name": "r", "weight": 1.0, "conditions": ["close > ma20", "rsi14 > 100"]}
        score, matched = evaluate_rule(_row(), rule)
        assert score == 1.0
        assert matched == ["close > ma20"]

    def test_no_match(self):
        rule = {"name": "r", "weight": 1.0, "conditions": ["close < ma20"]}
        score, matched = evaluate_rule(_row(), rule)
        assert score == 0.0
        assert matched == []

    def test_nan_condition_never_matches(self):
        """Nan trong du lieu -> condition khong khop (khong loi, khong bia)."""
        rule = {"name": "r", "weight": 1.0, "conditions": ["pb_rel is not null and pb_rel < 0.8"]}
        row = _row(pb_rel=float("nan"))
        score, matched = evaluate_rule(row, rule)
        assert score == 0.0
