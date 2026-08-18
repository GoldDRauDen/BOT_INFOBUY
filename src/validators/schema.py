"""
Schema Validator - Kiem tra cau truc raw data.
Task 9: OFFLINE. Chi validate STRUCTURE.
Khong fetch, khong modify, khong normalize, khong repair, khong infer capability,
khong evaluate quality, khong remove/aggregate records.

Validation chi kiem tra:
- File readable
- JSON structure valid
- Required top-level fields: source, capability, entries
- entries la list
- Moi entry co required metadata: url, status, content_type,
  response_size_bytes, fetched_at, body
- content_type JSON -> body phai la valid JSON
- content_type XML -> body phai la valid XML
- Khac -> body chi can la string

KHONG kiem tra symbol/price/stock data correct/reasonable.
Giu nguyen data goc, them validation metadata.
"""
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional


class SchemaValidator:
    """Validate cau truc raw data."""

    # Required top-level fields
    TOP_LEVEL_FIELDS = ["source", "capability", "entries"]

    # Required metadata fields per entry
    ENTRY_FIELDS = [
        "url", "status", "content_type",
        "response_size_bytes", "fetched_at", "body",
    ]

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("schema_validator")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"
        self.raw_dir = self.output_dir / "raw_data"
        self.validated_dir = self.output_dir / "validated_data"

    # ---------- Validation ----------

    def _is_json_content_type(self, content_type: str) -> bool:
        """Kiem tra content_type co phai JSON khong."""
        return "json" in (content_type or "").lower()

    def _is_xml_content_type(self, content_type: str) -> bool:
        """Kiem tra content_type co phai XML khong."""
        ct = (content_type or "").lower()
        return "xml" in ct

    def _validate_body(self, body: Any, content_type: str) -> List[str]:
        """
        Validate body theo content_type.
        Tra ve list loi (rong = hop le).
        """
        errors = []
        if self._is_json_content_type(content_type):
            # Body phai la valid JSON
            if not isinstance(body, str):
                errors.append("body_not_string_for_json")
                return errors
            try:
                json.loads(body)
            except (json.JSONDecodeError, ValueError):
                errors.append("body_invalid_json")
        elif self._is_xml_content_type(content_type):
            # Body phai la valid XML
            if not isinstance(body, str):
                errors.append("body_not_string_for_xml")
                return errors
            try:
                ET.fromstring(body)
            except ET.ParseError:
                errors.append("body_invalid_xml")
        else:
            # Khac: body chi can la string
            if not isinstance(body, str):
                errors.append("body_not_string")
        return errors

    def _validate_entry(self, entry: Any) -> List[str]:
        """Validate metadata fields cua mot entry. Tra ve list loi."""
        errors = []
        if not isinstance(entry, dict):
            return ["entry_not_dict"]

        for field in self.ENTRY_FIELDS:
            if field not in entry:
                errors.append(f"missing_field_{field}")

        # Neu thieu content_type/body thi khong the validate body
        if "content_type" in entry and "body" in entry:
            body_errors = self._validate_body(entry.get("body"), entry.get("content_type"))
            errors.extend(body_errors)

        return errors

    def _validate_file_from_data(self, data: Any) -> Dict[str, Any]:
        """Validate data dang dict (da load). Dung chung cho _validate_file."""
        # 2-3. Top-level fields
        if not isinstance(data, dict):
            return {
                "file": None,
                "schema_valid": False,
                "validation_errors": ["not_dict"],
            }

        errors = []
        for field in self.TOP_LEVEL_FIELDS:
            if field not in data:
                errors.append(f"missing_top_level_{field}")

        # 4. entries la list
        entries = data.get("entries")
        if "entries" in data and not isinstance(entries, list):
            errors.append("entries_not_list")

        # 5-8. Validate tung entry (neu entries la list)
        entry_errors = []
        if isinstance(entries, list):
            for i, entry in enumerate(entries):
                for err in self._validate_entry(entry):
                    entry_errors.append(f"entry_{i}_{err}")

        all_errors = errors + entry_errors

        # Output: giu nguyen data goc + them metadata
        result = dict(data)
        result["schema_valid"] = not all_errors
        result["validation_errors"] = all_errors
        return result

    def _validate_file(self, path: Path) -> Dict[str, Any]:
        """
        Validate mot raw_data file.
        Giu nguyen data goc, them validation metadata.
        """
        # 1. File readable
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return {
                "file": path.name,
                "schema_valid": False,
                "validation_errors": [f"invalid_json: {e}"],
            }
        except OSError as e:
            return {
                "file": path.name,
                "schema_valid": False,
                "validation_errors": [f"unreadable: {e}"],
            }
        except Exception as e:
            return {
                "file": path.name,
                "schema_valid": False,
                "validation_errors": [f"read_error: {e}"],
            }

        result = self._validate_file_from_data(data)
        result["file"] = path.name
        return result

    # ---------- Main flow ----------

    def run(self) -> Dict[str, Any]:
        """Validate toan bo raw_data."""
        report: Dict[str, Any] = {"generated_at": datetime.now().isoformat(), "sources": {}}

        if not self.raw_dir.exists():
            self.logger.error(f"Thieu thu muc raw_data: {self.raw_dir}")
            return report

        for source_dir in sorted(self.raw_dir.iterdir()):
            if not source_dir.is_dir():
                continue
            source_key = source_dir.name
            source_result = {"validated_at": datetime.now().isoformat(), "capabilities": {}}
            for cap_file in sorted(source_dir.glob("*.json")):
                cap_name = cap_file.stem
                source_result["capabilities"][cap_name] = self._validate_file(cap_file)
            report["sources"][source_key] = source_result

        return report

    def save_validated(self, report: Dict[str, Any]) -> str:
        """Luu ket qua validate vao validated_data/{source}/{capability}.json."""
        self.validated_dir.mkdir(parents=True, exist_ok=True)
        for source_key, source_result in report.get("sources", {}).items():
            source_dir = self.validated_dir / source_key
            source_dir.mkdir(parents=True, exist_ok=True)
            for cap_name, cap_data in source_result.get("capabilities", {}).items():
                path = source_dir / f"{cap_name}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cap_data, f, indent=2, ensure_ascii=False)
        return str(self.validated_dir)


def run_schema_validator(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay schema validator tren raw data. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    validator = SchemaValidator(logger=logger)

    logger.info("Validate schema raw data (offline)...")
    report = validator.run()
    validated_dir = validator.save_validated(report)

    # In tom tat
    print(f"\n  Ket qua validate schema:")
    for key, src in report.get("sources", {}).items():
        valid_count = sum(
            1 for c in src.get("capabilities", {}).values() if c.get("schema_valid")
        )
        total = len(src.get("capabilities", {}))
        print(f"    - {key}: {valid_count}/{total} files schema valid")

    print(f"\n  Validated data: {validated_dir}")
    return report
