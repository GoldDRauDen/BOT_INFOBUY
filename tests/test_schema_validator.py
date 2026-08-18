"""
Unit tests cho Schema Validator (Task 9).
Offline, deterministic, khong network.
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from validators.schema import SchemaValidator


def _make_entry(url="/api", status=200, content_type="application/json", body='{"symbol": "FPT"}'):
    return {
        "url": url,
        "status": status,
        "content_type": content_type,
        "response_size_bytes": len(body.encode()),
        "fetched_at": "2026-01-01T00:00:00",
        "body": body,
    }


def _make_file(source="hose", capability="stock_list", entries=None):
    return {
        "source": source,
        "capability": capability,
        "entries": entries if entries is not None else [_make_entry()],
    }


class TestContentType:
    """Test kiem tra content type."""

    def test_is_json(self):
        v = SchemaValidator()
        assert v._is_json_content_type("application/json") is True
        assert v._is_json_content_type("application/json; charset=utf-8") is True
        assert v._is_json_content_type("text/html") is False

    def test_is_xml(self):
        v = SchemaValidator()
        assert v._is_xml_content_type("application/xml") is True
        assert v._is_xml_content_type("text/xml") is True
        assert v._is_xml_content_type("application/json") is False


class TestValidateBody:
    """Test validate body theo content type."""

    def test_valid_json(self):
        v = SchemaValidator()
        assert v._validate_body('{"symbol": "FPT"}', "application/json") == []

    def test_invalid_json(self):
        v = SchemaValidator()
        assert v._validate_body("{not json", "application/json") == ["body_invalid_json"]

    def test_json_body_not_string(self):
        v = SchemaValidator()
        assert v._validate_body({"symbol": "FPT"}, "application/json") == ["body_not_string_for_json"]

    def test_valid_xml(self):
        v = SchemaValidator()
        assert v._validate_body("<root><a>1</a></root>", "application/xml") == []

    def test_invalid_xml(self):
        v = SchemaValidator()
        assert v._validate_body("<root><a></root>", "application/xml") == ["body_invalid_xml"]

    def test_other_content_type_string_ok(self):
        v = SchemaValidator()
        assert v._validate_body("<html>plain</html>", "text/html") == []

    def test_other_content_type_not_string(self):
        v = SchemaValidator()
        assert v._validate_body({"a": 1}, "text/html") == ["body_not_string"]


class TestValidateEntry:
    """Test validate metadata fields."""

    def test_valid_entry(self):
        v = SchemaValidator()
        assert v._validate_entry(_make_entry()) == []

    def test_missing_fields(self):
        v = SchemaValidator()
        entry = {"url": "/api"}  # thieu status, content_type, ...
        errors = v._validate_entry(entry)
        assert "missing_field_status" in errors
        assert "missing_field_body" in errors

    def test_entry_not_dict(self):
        v = SchemaValidator()
        assert v._validate_entry("not a dict") == ["entry_not_dict"]


class TestValidateFile:
    """Test validate mot file."""

    def test_valid_file(self):
        v = SchemaValidator()
        result = v._validate_file_from_data(_make_file())
        assert result["schema_valid"] is True
        assert result["validation_errors"] == []
        # Giu nguyen data goc
        assert result["source"] == "hose"
        assert result["capability"] == "stock_list"
        assert len(result["entries"]) == 1

    def test_missing_top_level(self):
        v = SchemaValidator()
        result = v._validate_file_from_data({"entries": []})
        assert result["schema_valid"] is False
        assert "missing_top_level_source" in result["validation_errors"]
        assert "missing_top_level_capability" in result["validation_errors"]

    def test_entries_not_list(self):
        v = SchemaValidator()
        result = v._validate_file_from_data(_make_file(entries="not list"))
        assert result["schema_valid"] is False
        assert "entries_not_list" in result["validation_errors"]

    def test_invalid_entry_metadata(self):
        v = SchemaValidator()
        result = v._validate_file_from_data(_make_file(entries=[{"url": "/x"}]))
        assert result["schema_valid"] is False
        assert any("missing_field_status" in e for e in result["validation_errors"])

    def test_invalid_json_body(self):
        v = SchemaValidator()
        result = v._validate_file_from_data(
            _make_file(entries=[_make_entry(body="{bad json")])
        )
        assert result["schema_valid"] is False
        assert any("body_invalid_json" in e for e in result["validation_errors"])

    def test_malformed_json_file(self):
        v = SchemaValidator()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("{broken")
            temp_path = Path(f.name)
        try:
            result = v._validate_file(temp_path)
            assert result["schema_valid"] is False
            assert any("invalid_json" in e for e in result["validation_errors"])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_never_drop_entries(self):
        """Entries giu nguyen, khong loai bo."""
        v = SchemaValidator()
        entries = [_make_entry(), _make_entry(body="{bad"), _make_entry()]
        result = v._validate_file_from_data(_make_file(entries=entries))
        assert len(result["entries"]) == 3  # khong drop entry nao
        assert result["schema_valid"] is False


class TestRun:
    """Test run toan bo."""

    def test_run_validates_all(self, tmp_path):
        v = SchemaValidator()
        raw = tmp_path / "raw_data" / "hose"
        raw.mkdir(parents=True)
        with open(raw / "stock_list.json", "w", encoding="utf-8") as f:
            json.dump(_make_file(), f)
        with open(raw / "bad.json", "w", encoding="utf-8") as f:
            f.write("{broken")

        v.raw_dir = tmp_path / "raw_data"
        report = v.run()
        assert "hose" in report["sources"]
        caps = report["sources"]["hose"]["capabilities"]
        assert caps["stock_list"]["schema_valid"] is True
        assert caps["bad"]["schema_valid"] is False

    def test_run_missing_raw_dir(self):
        v = SchemaValidator()
        report = v.run()
        assert report["sources"] == {}

    def test_save_validated(self, tmp_path):
        v = SchemaValidator()
        report = {
            "sources": {
                "hose": {"capabilities": {
                    "stock_list": {"source": "hose", "schema_valid": True, "entries": []},
                }},
            }
        }
        v.validated_dir = tmp_path / "validated_data"
        saved = v.save_validated(report)
        assert (Path(saved) / "hose" / "stock_list.json").exists()
        with open(Path(saved) / "hose" / "stock_list.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["schema_valid"] is True
