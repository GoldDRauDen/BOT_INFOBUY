"""
Quality Gate - Danh gia du lieu da validate co "dung duoc" khong.
Task 10: OFFLINE. Chi danh gia quality.
Khong fetch, khong modify, khong normalize, khong repair, khong infer capability,
khong validate schema, khong transform records, khong aggregate reports.

Quality rules (per validated file):
1. Neu schema_valid == false -> quality = "fail" (reason: schema_invalid)
2. Nguoc lai danh gia:
   - entries khong rong
   - it nhat 1 entry co status == 200
   - response_size_bytes > 0
   - body khong rong
   - body khong null
3. Produce: pass | fail

Khong co quality level nao khac. Mot capability = mot quality result.
Khong bao gio modify validated_data, khong remove entries.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional


class QualityGate:
    """Danh gia chat luong du lieu da validate."""

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("quality_gate")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"
        self.validated_dir = self.output_dir / "validated_data"

    # ---------- Quality assessment ----------

    def _entry_is_usable(self, entry: Any) -> bool:
        """
        Mot entry co dung duoc khong:
        - status == 200
        - response_size_bytes > 0
        - body khong rong
        - body khong null
        """
        if not isinstance(entry, dict):
            return False
        if entry.get("status") != 200:
            return False
        size = entry.get("response_size_bytes")
        if not isinstance(size, int) or size <= 0:
            return False
        body = entry.get("body")
        if body is None:
            return False
        if isinstance(body, str) and not body.strip():
            return False
        return True

    def _assess_file(self, data: Any) -> Dict[str, Any]:
        """
        Danh gia quality cua mot validated file.
        Tra ve {quality, checked_entries, passed_entries, failed_entries, reason}.
        """
        # 1. schema_valid == false -> fail
        if not isinstance(data, dict) or data.get("schema_valid") is False:
            return {
                "quality": "fail",
                "checked_entries": 0,
                "passed_entries": 0,
                "failed_entries": 0,
                "reason": "schema_invalid",
            }

        entries = data.get("entries")
        # 2a. entries khong rong
        if not isinstance(entries, list) or not entries:
            return {
                "quality": "fail",
                "checked_entries": 0,
                "passed_entries": 0,
                "failed_entries": 0,
                "reason": "empty_entries",
            }

        # Dem entries
        checked = len(entries)
        passed = sum(1 for e in entries if self._entry_is_usable(e))
        failed = checked - passed

        # 2b. it nhat 1 entry status == 200
        if passed == 0:
            # Xac dinh reason cu the
            reason = self._determine_fail_reason(entries)
            return {
                "quality": "fail",
                "checked_entries": checked,
                "passed_entries": 0,
                "failed_entries": failed,
                "reason": reason,
            }

        # 2c-e. Neu co entry pass -> quality pass
        return {
            "quality": "pass",
            "checked_entries": checked,
            "passed_entries": passed,
            "failed_entries": failed,
            "reason": None,
        }

    def _determine_fail_reason(self, entries: List[Any]) -> str:
        """
        Xac dinh ly do fail khi khong co entry nao usable.
        Uu tien: empty_body > zero_response_size > no_successful_response.
        """
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            body = entry.get("body")
            if body is None:
                return "empty_body"
            if isinstance(body, str) and not body.strip():
                return "empty_body"
            size = entry.get("response_size_bytes")
            if isinstance(size, int) and size <= 0:
                return "zero_response_size"
        return "no_successful_response"

    # ---------- Main flow ----------

    def run(self) -> Dict[str, Any]:
        """Danh gia toan bo validated data."""
        report: Dict[str, Any] = {"generated_at": datetime.now().isoformat()}

        if not self.validated_dir.exists():
            self.logger.error(f"Thieu thu muc validated_data: {self.validated_dir}")
            return report

        for source_dir in sorted(self.validated_dir.iterdir()):
            if not source_dir.is_dir():
                continue
            source_key = source_dir.name
            source_result = {}
            for cap_file in sorted(source_dir.glob("*.json")):
                cap_name = cap_file.stem
                # Continue neu file loi - graceful handling
                try:
                    with open(cap_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    source_result[cap_name] = self._assess_file(data)
                except Exception as e:
                    self.logger.warning(f"Loi doc {cap_file}: {e}")
                    source_result[cap_name] = {
                        "quality": "fail",
                        "checked_entries": 0,
                        "passed_entries": 0,
                        "failed_entries": 0,
                        "reason": "schema_invalid",
                    }
            report[source_key] = source_result

        return report

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        if output_path is None:
            output_path = self.output_dir / "quality_report.json"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_quality_gate(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay quality gate tren validated data. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    gate = QualityGate(logger=logger)

    logger.info("Danh gia chat luong du lieu (offline)...")
    report = gate.run()
    report_path = gate.save_report(report)

    # In tom tat
    print(f"\n  Ket qua quality gate:")
    for key, caps in report.items():
        if key == "generated_at":
            continue
        passed = sum(1 for c in caps.values() if c.get("quality") == "pass")
        print(f"    - {key}: {passed}/{len(caps)} capabilities pass")

    print(f"\n  Bao cao: output/quality_report.json")
    return report
