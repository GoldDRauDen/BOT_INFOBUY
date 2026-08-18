"""
Data Extraction Engine - Dieu phoi trich xuat du lieu chung khoan.
Task 13: Chi xu ly capability quality == "pass" (tu quality_report.json).
Doc validated_data/, dispatch extractor per capability, luu extracted_data/.
Khong validate, khong quality check, khong HTTP, khong inference.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from .extractors import EXTRACTORS
except ImportError:
    from extractor.extractors import EXTRACTORS


class ExtractionEngine:
    """Orchestrator: doc quality + validated, dispatch extractor."""

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("extraction_engine")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"
        self.validated_dir = self.output_dir / "validated_data"
        self.extracted_dir = self.output_dir / "extracted_data"

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Any]:
        if not path.exists():
            self.logger.error(f"File khong ton tai: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Loi doc {path}: {e}")
            return None

    # ---------- Main flow ----------

    def run(self) -> Dict[str, Any]:
        """Trich xuat toan bo capability pass."""
        report: Dict[str, Any] = {"generated_at": datetime.now().isoformat(), "sources": {}}

        quality = self._read_json(self.output_dir / "quality_report.json")
        if not isinstance(quality, dict):
            self.logger.error("Thieu quality_report.json, khong the extraction")
            return report

        for source_key, source_q in quality.items():
            if source_key == "generated_at":
                continue
            if not isinstance(source_q, dict):
                continue
            source_result = {}
            for cap_name, cap_q in source_q.items():
                if not isinstance(cap_q, dict):
                    continue
                # Chi xu ly capability pass
                if cap_q.get("quality") != "pass":
                    continue
                extractor = EXTRACTORS.get(cap_name)
                if extractor is None:
                    self.logger.warning(f"Khong co extractor cho capability: {cap_name}")
                    continue
                # Doc validated file
                validated_file = self.validated_dir / source_key / f"{cap_name}.json"
                validated = self._read_json(validated_file)
                if validated is None:
                    continue
                result = extractor.extract(validated)
                source_result[cap_name] = result
            if source_result:
                report["sources"][source_key] = source_result

        return report

    def save_extracted(self, report: Dict[str, Any]) -> str:
        """Luu extracted data vao extracted_data/{source}/{capability}.json."""
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        for source_key, source_result in report.get("sources", {}).items():
            source_dir = self.extracted_dir / source_key
            source_dir.mkdir(parents=True, exist_ok=True)
            for cap_name, cap_data in source_result.items():
                path = source_dir / f"{cap_name}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cap_data, f, indent=2, ensure_ascii=False)
                self.logger.debug(f"  Da luu: {path}")
        return str(self.extracted_dir)


def run_extraction(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay extraction engine. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    engine = ExtractionEngine(logger=logger)

    logger.info("Trich xuat du lieu chung khoan (offline)...")
    report = engine.run()
    extracted_dir = engine.save_extracted(report)

    # In tom tat
    total_records = 0
    print(f"\n  Ket qua trich xuat du lieu:")
    for key, src in report.get("sources", {}).items():
        n = sum(len(c.get("records", [])) for c in src.values())
        total_records += n
        print(f"    - {key}: {len(src)} capabilities, {n} records")
    print(f"\n  Extracted data: {extracted_dir}")
    return report
