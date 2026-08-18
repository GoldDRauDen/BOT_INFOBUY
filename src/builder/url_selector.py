"""
URL Selector - Chon URL cho viec fetch sau nay.
Task 7: OFFLINE. Deterministic. Khong HTTP. Khong tao URL, khong infer capability,
khong keyword matching, khong score URL, khong inspect page content.

Selection rules (per source x capability):
1. Neu capability.status == "supported":
   - Neu capability.evidence.url ton tai: dung URL do.
2. Nguoc lai: chon URL DAU TIEN tu index_pages (preserve crawl order).
3. Neu khong co URL: khong tao plan entry.

Khong bao gio tao/synthesize URL. Khong reorder tru khi dedup deterministic.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class UrlSelector:
    """Chon URL cho tung capability duoc ho tro."""

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("url_selector")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"

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

    # ---------- Selection ----------

    def _get_evidence_url(self, cap_info: Any) -> Optional[str]:
        """Lay evidence.url tu capability (chi khi status supported)."""
        if not isinstance(cap_info, dict):
            return None
        if cap_info.get("status") != "supported":
            return None
        evidence = cap_info.get("evidence")
        if isinstance(evidence, dict):
            url = evidence.get("url")
            return url if isinstance(url, str) and url else None
        return None

    def _select_for_capability(
        self, cap_name: str, cap_info: Any, index_urls: list
    ) -> Optional[Dict[str, Any]]:
        """
        Chon URL cho mot capability.
        CHI tao entry khi status == "supported".
        Tra ve dict {status, url, reason} hoac None.
        """
        # Gate: chi supported moi co plan entry
        if not isinstance(cap_info, dict) or cap_info.get("status") != "supported":
            return None

        # Rule 1: evidence.url tu capability_report
        evidence_url = self._get_evidence_url(cap_info)
        if evidence_url is not None:
            return {
                "status": "planned",
                "url": evidence_url,
                "reason": "capability_evidence",
            }

        # Rule 2: URL dau tien tu index_pages (preserve crawl order)
        if index_urls:
            first = index_urls[0]
            return {
                "status": "planned",
                "url": first,
                "reason": "first_available_index_page",
            }

        # Rule 3: khong co URL -> khong tao plan entry
        return None

    def _dedup_index_urls(self, urls: list) -> list:
        """Dedup deterministic, preserve crawl order."""
        seen = set()
        result = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                result.append(u)
        return result

    # ---------- Build ----------

    def build_plan(
        self,
        capability_report: Dict[str, Any],
        index_pages: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build endpoint plan cho tat ca source."""
        plan: Dict[str, Any] = {"generated_at": datetime.now().isoformat()}

        if not isinstance(capability_report, dict):
            return plan

        for source_key, source_caps in capability_report.items():
            if source_key == "generated_at":
                continue
            if not isinstance(source_caps, dict):
                continue

            # Lay index urls cho source nay (da dedup)
            index_data = index_pages.get(source_key, {}) if isinstance(index_pages, dict) else {}
            index_urls = []
            if isinstance(index_data, dict):
                urls = index_data.get("urls", [])
                if isinstance(urls, list):
                    index_urls = self._dedup_index_urls([u for u in urls if isinstance(u, str)])

            source_plan = {}
            for cap_name, cap_info in source_caps.items():
                if cap_name == "generated_at":
                    continue
                entry = self._select_for_capability(cap_name, cap_info, index_urls)
                if entry is not None:
                    source_plan[cap_name] = entry

            plan[source_key] = source_plan

        return plan

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        if output_path is None:
            output_path = self.output_dir / "endpoint_plan.json"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_url_selector(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay URL selector cho tat ca nguon. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    selector = UrlSelector(logger=logger)

    capability_report = selector._read_json(selector.output_dir / "capability_report.json")
    index_pages = selector._read_json(selector.output_dir / "index_pages.json")
    if capability_report is None or index_pages is None:
        logger.error("Thieu capability_report.json hoac index_pages.json")
        return {}

    logger.info("Xay dung endpoint plan (offline, deterministic)...")
    report = selector.build_plan(capability_report, index_pages)
    report_path = selector.save_report(report)

    # In tom tat
    planned = {k: v for k, v in report.items() if k != "generated_at"}
    print(f"\n  Ket qua xay dung endpoint plan:")
    for key, caps in planned.items():
        print(f"    - {key}: {len(caps)} capabilities planned")

    print(f"\n  Bao cao: output/endpoint_plan.json")
    return report
