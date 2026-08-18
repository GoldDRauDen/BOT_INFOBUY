"""
Unit tests cho Quality Gate (Task 10).
Offline, deterministic, khong network.
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from validators.quality import QualityGate


def _validated_file(schema_valid=True, entries=None):
    return {
        "source": "hose",
        "capability": "stock_list",
        "schema_valid": schema_valid,
        "validation_errors": [] if schema_valid else ["missing_top_level_capability"],
        "entries": entries if entries is not None else [],
    }


def _entry(status=200, size=100, body='{"symbol": "FPT"}'):
    return {
        "url": "/api",
        "status": status,
        "content_type": "application/json",
        "response_size_bytes": size,
        "fetched_at": "2026-01-01T00:00:00",
        "body": body,
    }


class TestEntryUsable:
    """Test mot entry co dung duoc khong."""

    def test_usable_entry(self):
        gate = QualityGate()
        assert gate._entry_is_usable(_entry()) is True

    def test_wrong_status(self):
        gate = QualityGate()
        assert gate._entry_is_usable(_entry(status=404)) is False
        assert gate._entry_is_usable(_entry(status=500)) is False

    def test_zero_size(self):
        gate = QualityGate()
        assert gate._entry_is_usable(_entry(size=0)) is False
        assert gate._entry_is_usable(_entry(size=-5)) is False

    def test_empty_body(self):
        gate = QualityGate()
        assert gate._entry_is_usable(_entry(body="")) is False
        assert gate._entry_is_usable(_entry(body="   ")) is False

    def test_null_body(self):
        gate = QualityGate()
        assert gate._entry_is_usable(_entry(body=None)) is False

    def test_not_dict(self):
        gate = QualityGate()
        assert gate._entry_is_usable("not dict") is False


class TestAssessFile:
    """Test danh gia mot file."""

    def test_pass_all_usable(self):
        gate = QualityGate()
        result = gate._assess_file(_validated_file(entries=[_entry(), _entry()]))
        assert result["quality"] == "pass"
        assert result["checked_entries"] == 2
        assert result["passed_entries"] == 2
        assert result["failed_entries"] == 0
        assert result["reason"] is None

    def test_pass_some_usable(self):
        gate = QualityGate()
        entries = [_entry(), _entry(status=404), _entry(body=None)]
        result = gate._assess_file(_validated_file(entries=entries))
        assert result["quality"] == "pass"
        assert result["checked_entries"] == 3
        assert result["passed_entries"] == 1
        assert result["failed_entries"] == 2

    def test_fail_schema_invalid(self):
        gate = QualityGate()
        result = gate._assess_file(_validated_file(schema_valid=False, entries=[_entry()]))
        assert result["quality"] == "fail"
        assert result["reason"] == "schema_invalid"
        assert result["checked_entries"] == 0

    def test_fail_empty_entries(self):
        gate = QualityGate()
        result = gate._assess_file(_validated_file(schema_valid=True, entries=[]))
        assert result["quality"] == "fail"
        assert result["reason"] == "empty_entries"

    def test_fail_no_successful_response(self):
        gate = QualityGate()
        entries = [_entry(status=404, body="nf"), _entry(status=500, body="err")]
        result = gate._assess_file(_validated_file(entries=entries))
        assert result["quality"] == "fail"
        assert result["reason"] == "no_successful_response"

    def test_fail_empty_body_reason(self):
        gate = QualityGate()
        entries = [_entry(status=200, body="")]
        result = gate._assess_file(_validated_file(entries=entries))
        assert result["quality"] == "fail"
        assert result["reason"] == "empty_body"

    def test_fail_null_body_reason(self):
        gate = QualityGate()
        entries = [_entry(status=200, body=None)]
        result = gate._assess_file(_validated_file(entries=entries))
        assert result["quality"] == "fail"
        assert result["reason"] == "empty_body"

    def test_fail_zero_size_reason(self):
        gate = QualityGate()
        entries = [_entry(status=200, size=0, body="x")]
        result = gate._assess_file(_validated_file(entries=entries))
        assert result["quality"] == "fail"
        assert result["reason"] == "zero_response_size"

    def test_never_modify_data(self):
        """Input khong bi modify."""
        gate = QualityGate()
        data = _validated_file(entries=[_entry(), _entry(status=404)])
        original = json.dumps(data, sort_keys=True)
        gate._assess_file(data)
        assert json.dumps(data, sort_keys=True) == original


class TestRun:
    """Test run toan bo."""

    def test_run_all_sources(self, tmp_path):
        gate = QualityGate()
        vdir = tmp_path / "validated_data"
        (vdir / "hose").mkdir(parents=True)
        (vdir / "hnx").mkdir()

        with open(vdir / "hose" / "stock_list.json", "w", encoding="utf-8") as f:
            json.dump(_validated_file(entries=[_entry(), _entry()]), f)
        with open(vdir / "hose" / "dividends.json", "w", encoding="utf-8") as f:
            json.dump(_validated_file(schema_valid=False, entries=[]), f)
        # hnx khong co files

        gate.validated_dir = vdir
        report = gate.run()
        assert report["hose"]["stock_list"]["quality"] == "pass"
        assert report["hose"]["dividends"]["quality"] == "fail"
        assert report["hose"]["dividends"]["reason"] == "schema_invalid"
        assert report["hnx"] == {}

    def test_run_missing_dir(self, tmp_path):
        gate = QualityGate()
        gate.validated_dir = tmp_path / "khong_ton_tai"
        report = gate.run()
        assert report == {"generated_at": report["generated_at"]}

    def test_run_malformed_file_continues(self, tmp_path):
        """File loi -> fail, van xu ly cac file khac."""
        gate = QualityGate()
        vdir = tmp_path / "validated_data"
        (vdir / "hose").mkdir(parents=True)
        (vdir / "hose" / "bad.json").write_text("{broken", encoding="utf-8")
        with open(vdir / "hose" / "good.json", "w", encoding="utf-8") as f:
            json.dump(_validated_file(entries=[_entry()]), f)

        gate.validated_dir = vdir
        report = gate.run()
        assert report["hose"]["bad"]["quality"] == "fail"
        assert report["hose"]["good"]["quality"] == "pass"

    def test_deterministic_order(self, tmp_path):
        gate = QualityGate()
        vdir = tmp_path / "validated_data"
        (vdir / "hose").mkdir(parents=True)
        for name in ["c.json", "a.json", "b.json"]:
            with open(vdir / "hose" / name, "w", encoding="utf-8") as f:
                json.dump(_validated_file(entries=[_entry()]), f)

        gate.validated_dir = vdir
        r1 = gate.run()
        r2 = gate.run()
        # Bo generated_at (timestamp khac nhau moi lan chay)
        r1.pop("generated_at")
        r2.pop("generated_at")
        # Order deterministic (sorted: a, b, c)
        assert list(r1["hose"].keys()) == ["a", "b", "c"]
        assert r1 == r2

    def test_save_report(self):
        gate = QualityGate()
        report = {"hose": {}, "generated_at": "2026-01-01T00:00:00"}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name
        try:
            saved = gate.save_report(report, temp_path)
            assert Path(saved).exists()
            with open(saved, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["generated_at"] == "2026-01-01T00:00:00"
        finally:
            Path(temp_path).unlink(missing_ok=True)
