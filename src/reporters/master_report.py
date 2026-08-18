"""
Master Report - Tong hop toan bo pipeline thanh bao cao cuoi.
Task 11: OFFLINE. Gom cac output cua pipeline.
Khong fetch, khong modify report, khong infer capability, khong validate schema,
khong evaluate quality, khong transform validated data, khong remove records,
khong recalculate ket qua task truoc.

Aggregation rules:
1. Giu nguyen moi input report dung nhu cu.
2. Include pipeline execution summary.
3. Include quality results dung nhu cu.
4. Include validated data summary chi cho capability PASSED quality gate.
5. Khong embed full datasets - chi {records, quality}.
6. Failed capabilities excluded khoi data section.

Missing report -> null + warning. Khong bao gio modify input file.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class MasterReport:
    """Gom cac report thanh final_report.json."""

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("master_report")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Any]:
        """Doc report, tra ve None neu thieu + warning."""
        if not path.exists():
            self.logger.warning(f"Report thieu: {path.name}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.warning(f"Loi doc {path.name}: {e}")
            return None

    # ---------- Data summary ----------

    def _count_records(self, validated_data: Any) -> int:
        """
        Dem records trong validated file.
        records = tong so entry (bao gom ca entry fail).
        """
        if not isinstance(validated_data, dict):
            return 0
        entries = validated_data.get("entries")
        if not isinstance(entries, list):
            return 0
        return len(entries)

    def _collect_data(self, quality: Any, validated_dir: Path) -> Dict[str, Any]:
        """
        Gom data summary chi cho capability PASSED quality gate.
        Chi {records, quality}. Khong embed full dataset.
        """
        data: Dict[str, Any] = {}
        if not isinstance(quality, dict):
            return data

        for source_key, source_q in quality.items():
            if source_key == "generated_at":
                continue
            if not isinstance(source_q, dict):
                continue
            source_data = {}
            for cap_name, cap_q in source_q.items():
                if not isinstance(cap_q, dict):
                    continue
                if cap_q.get("quality") != "pass":
                    # Failed capabilities excluded
                    continue
                # Doc validated file de dem records
                cap_file = validated_dir / source_key / f"{cap_name}.json"
                validated = self._read_json(cap_file)
                source_data[cap_name] = {
                    "records": self._count_records(validated),
                    "quality": "pass",
                }
            if source_data:
                data[source_key] = source_data

        return data

    def _pipeline_summary(self, reports: Dict[str, Any]) -> Dict[str, Any]:
        """Tong ket pipeline execution."""
        summary = {"steps": {}}
        for name, report in reports.items():
            summary["steps"][name] = "ok" if report is not None else "missing"
        return summary

    # ---------- Build ----------

    def run(self) -> Dict[str, Any]:
        """Gom toan bo report."""
        reports = {
            "connectivity": self._read_json(self.output_dir / "connectivity_report.json"),
            "discovery": self._read_json(self.output_dir / "discovery_report.json"),
            "capability": self._read_json(self.output_dir / "capability_report.json"),
            "index": self._read_json(self.output_dir / "index_pages.json"),
            "endpoint_plan": self._read_json(self.output_dir / "endpoint_plan.json"),
            "quality": self._read_json(self.output_dir / "quality_report.json"),
        }

        quality = reports.get("quality")
        data = self._collect_data(quality, self.output_dir / "validated_data")

        report: Dict[str, Any] = {
            "generated_at": datetime.now().isoformat(),
            "pipeline": self._pipeline_summary(reports),
        }
        # Giu nguyen moi report dung nhu cu
        report.update(reports)
        report["data"] = data

        return report

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        if output_path is None:
            output_path = self.output_dir / "final_report.json"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_master_report(logger: logging.Logger = None) -> Dict[str, Any]:
    """Tao bao cao cuoi. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    reporter = MasterReport(logger=logger)

    logger.info("Tao bao cao tong hop (offline)...")
    report = reporter.run()
    report_path = reporter.save_report(report)

    # In tom tat
    print(f"\n  Bao cao tong hop: {report_path}")
    return report
