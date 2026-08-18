"""
Unit tests cho Master Report (Task 11).
Offline, deterministic, khong network.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reporters.master_report import MasterReport


def _make_validated(entries_count=3):
    return {
        "source": "hose",
        "capability": "stock_list",
        "schema_valid": True,
        "entries": [{"url": f"/{i}", "status": 200} for i in range(entries_count)],
    }


def _make_quality():
    return {
        "hose": {
            "stock_list": {"quality": "pass", "checked_entries": 3, "passed_entries": 3, "failed_entries": 0, "reason": None},
            "dividends": {"quality": "fail", "checked_entries": 2, "passed_entries": 0, "failed_entries": 2, "reason": "empty_body"},
        },
        "hnx": {},
        "generated_at": "2026-01-01T00:00:00",
    }


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class TestCountRecords:
    """Test dem records."""

    def test_count_entries(self):
        reporter = MasterReport()
        assert reporter._count_records(_make_validated(5)) == 5

    def test_empty(self):
        reporter = MasterReport()
        assert reporter._count_records({"entries": []}) == 0
        assert reporter._count_records({}) == 0
        assert reporter._count_records(None) == 0
        assert reporter._count_records({"entries": "not list"}) == 0


class TestCollectData:
    """Test gom data summary."""

    def test_only_passed_capabilities(self, tmp_path):
        reporter = MasterReport(base_dir=tmp_path)
        vdir = tmp_path / "output" / "validated_data"
        _write_json(vdir / "hose" / "stock_list.json", _make_validated(3))
        _write_json(vdir / "hose" / "dividends.json", _make_validated(2))

        data = reporter._collect_data(_make_quality(), vdir)
        # Chi stock_list pass -> co trong data
        assert "hose" in data
        assert "stock_list" in data["hose"]
        assert data["hose"]["stock_list"] == {"records": 3, "quality": "pass"}
        # dividends fail -> excluded
        assert "dividends" not in data["hose"]

    def test_no_full_dataset(self, tmp_path):
        """Chi {records, quality} - khong embed entries."""
        reporter = MasterReport(base_dir=tmp_path)
        vdir = tmp_path / "output" / "validated_data"
        _write_json(vdir / "hose" / "stock_list.json", _make_validated(3))

        data = reporter._collect_data(_make_quality(), vdir)
        entry = data["hose"]["stock_list"]
        assert set(entry.keys()) == {"records", "quality"}
        assert "entries" not in entry
        assert "body" not in entry

    def test_missing_quality(self, tmp_path):
        reporter = MasterReport(base_dir=tmp_path)
        assert reporter._collect_data(None, tmp_path) == {}

    def test_passed_no_validated_file(self, tmp_path):
        """Pass nhung thieu validated file -> records 0."""
        reporter = MasterReport(base_dir=tmp_path)
        data = reporter._collect_data(_make_quality(), tmp_path / "khong_co")
        assert data["hose"]["stock_list"]["records"] == 0


class TestRun:
    """Test run toan bo."""

    def test_run_preserves_reports(self, tmp_path):
        reporter = MasterReport(base_dir=tmp_path)
        out = tmp_path / "output"
        _write_json(out / "connectivity_report.json", {"hose": {"reachable": True}})
        _write_json(out / "discovery_report.json", {"hose": {"robots": {"found": True}}})
        _write_json(out / "quality_report.json", _make_quality())
        _write_json(out / "validated_data" / "hose" / "stock_list.json", _make_validated(2))

        report = reporter.run()
        # Giu nguyen report dung nhu cu
        assert report["connectivity"] == {"hose": {"reachable": True}}
        assert report["discovery"] == {"hose": {"robots": {"found": True}}}
        assert report["quality"]["hose"]["stock_list"]["quality"] == "pass"
        # Data summary
        assert report["data"]["hose"]["stock_list"] == {"records": 2, "quality": "pass"}
        # Pipeline summary
        assert report["pipeline"]["steps"]["connectivity"] == "ok"
        assert report["pipeline"]["steps"]["capability"] == "missing"

    def test_run_missing_reports_null(self, tmp_path):
        """Report thieu -> null, khong crash."""
        reporter = MasterReport(base_dir=tmp_path)
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)

        report = reporter.run()
        assert report["connectivity"] is None
        assert report["discovery"] is None
        assert report["capability"] is None
        assert report["index"] is None
        assert report["endpoint_plan"] is None
        assert report["quality"] is None
        assert report["data"] == {}
        assert report["pipeline"]["steps"]["connectivity"] == "missing"

    def test_run_deterministic(self, tmp_path):
        reporter = MasterReport(base_dir=tmp_path)
        out = tmp_path / "output"
        _write_json(out / "quality_report.json", _make_quality())
        _write_json(out / "validated_data" / "hose" / "stock_list.json", _make_validated(2))

        r1 = reporter.run()
        r2 = reporter.run()
        r1.pop("generated_at")
        r2.pop("generated_at")
        assert r1 == r2

    def test_run_output_schema_keys(self, tmp_path):
        """Kiem tra day du keys theo schema."""
        reporter = MasterReport(base_dir=tmp_path)
        out = tmp_path / "output"
        _write_json(out / "quality_report.json", _make_quality())

        report = reporter.run()
        for key in ["generated_at", "pipeline", "connectivity", "discovery",
                    "capability", "index", "endpoint_plan", "quality", "data"]:
            assert key in report


class TestSaveReport:
    """Test luu report."""

    def test_save_report(self, tmp_path):
        reporter = MasterReport(base_dir=tmp_path)
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        report = {"generated_at": "2026-01-01T00:00:00", "data": {}}
        saved = reporter.save_report(report, out / "final_report.json")
        assert Path(saved).exists()
        with open(saved, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["generated_at"] == "2026-01-01T00:00:00"
