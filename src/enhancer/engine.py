"""
Discovery Enhancement Engine - Tim endpoint that cua website hien dai.
Task 15: Chay SAU Discovery, TRUOC Capability. Chi tim endpoint moi.
Khong fetch stock data, khong capability, khong schema/quality/extraction.
Deterministic, khong AI, khong scoring.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from .http_client import EnhancerHttpClient
from .parsers.common import dedup_ordered, extract_from_js
from .parsers.html_parser import HtmlParser

try:
    from ..utils.source_loader import load_sources
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.source_loader import load_sources
    from utils.config_loader import load_settings


class DiscoveryEnhancer:
    """Orchestrator: doc input, chay parsers, gom candidates."""

    DEFAULT_MAX_HTML_PAGES = 5
    DEFAULT_MAX_JS_BUNDLES = 5
    DEFAULT_MAX_SOURCE_MAPS = 5
    DEFAULT_MAX_CANDIDATES = 50

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None,
                 config: Dict[str, Any] = None):
        self.logger = logger or logging.getLogger("discovery_enhancer")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"

        if config is None:
            try:
                settings = load_settings()
                config = settings.get("enhancer", {})
            except Exception:
                config = {}
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.max_html_pages = int(self.config.get("max_html_pages", self.DEFAULT_MAX_HTML_PAGES))
        self.max_js_bundles = int(self.config.get("max_js_bundles", self.DEFAULT_MAX_JS_BUNDLES))
        self.max_source_maps = int(self.config.get("max_source_maps", self.DEFAULT_MAX_SOURCE_MAPS))
        self.max_candidates = int(self.config.get("max_endpoint_candidates", self.DEFAULT_MAX_CANDIDATES))
        self.source_maps_enabled = bool(self.config.get("source_maps", True))

        self.http = EnhancerHttpClient(
            logger=logger,
            timeout=int(self.config.get("timeout", 15)),
            retries=int(self.config.get("retries", 3)),
            request_delay=float(self.config.get("request_delay", 1.0)),
        )

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Any]:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    # ---------- Per source ----------

    def _enhance_source(self, source_key: str, base_url: str,
                        discovery: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Enhance mot source: crawl pages, bundles, source maps."""
        result = {
            "base_url": base_url,
            "html_pages_scanned": 0,
            "js_bundles_scanned": 0,
            "source_maps_scanned": 0,
            "http_requests": 0,
            "errors": [],
            "endpoint_candidates": [],
        }

        # Thu thap URLs de scan
        pages = [base_url]
        if isinstance(discovery, dict):
            for ep_name in ("robots", "sitemap", "rss"):
                ep = discovery.get(ep_name)
                if isinstance(ep, dict) and ep.get("found") and ep.get("url"):
                    pages.append(ep["url"])

        candidates: List[Dict[str, Any]] = []
        js_bundles: List[str] = []
        seen_pages = set()
        page_count = 0

        for page_url in pages:
            if page_count >= self.max_html_pages:
                break
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            html = self.http.get_text(page_url)
            if html is None:
                continue
            page_count += 1
            result["html_pages_scanned"] += 1
            result["http_requests"] += 1

            parser = HtmlParser(page_url)
            parsed = parser.extract(html)

            # JS bundles moi
            for bundle in parsed["js_bundles"]:
                if bundle not in js_bundles:
                    js_bundles.append(bundle)

            # Doc links -> candidates (openapi)
            for doc in parsed["doc_links"]:
                candidates.append({
                    "url": doc, "found_in": "html", "source": page_url,
                    "method": None, "type": "openapi",
                    "evidence": f'<link href="{doc}">', "dynamic": False,
                })

            # Inline scripts -> candidates
            for script in parsed["inline_scripts"]:
                for c in extract_from_js(script, "inline_script", page_url):
                    candidates.append(c)

        # JS bundles
        bundle_count = 0
        for bundle in js_bundles:
            if bundle_count >= self.max_js_bundles:
                break
            js = self.http.get_text(bundle)
            if js is None:
                continue
            bundle_count += 1
            result["js_bundles_scanned"] += 1
            result["http_requests"] += 1

            for c in extract_from_js(js, "js_bundle", bundle):
                candidates.append(c)

            # Source maps
            if self.source_maps_enabled and result["source_maps_scanned"] < self.max_source_maps:
                map_url = bundle + ".map"
                map_data = self.http.get_json(map_url)
                if map_data is not None:
                    result["source_maps_scanned"] += 1
                    result["http_requests"] += 1
                    sources = map_data.get("sources", [])
                    for src in sources:
                        if isinstance(src, str) and any(
                            k in src.lower() for k in ("api", "endpoint", "service")
                        ):
                            candidates.append({
                                "url": src, "found_in": "source_map", "source": map_url,
                                "method": None, "type": "unknown",
                                "evidence": f"sources: {src}", "dynamic": False,
                            })

        # Dedup + cap
        seen = set()
        final_candidates = []
        for c in candidates:
            key = (c.get("url"), c.get("evidence"))
            if key in seen:
                continue
            seen.add(key)
            final_candidates.append(c)
            if len(final_candidates) >= self.max_candidates:
                break

        result["endpoint_candidates"] = final_candidates
        result["http_requests"] = self.http._request_count
        return result

    # ---------- Main flow ----------

    def run(self) -> Dict[str, Any]:
        """Enhance tat ca source."""
        report: Dict[str, Any] = {"generated_at": datetime.now().isoformat(), "sources": {}}

        discovery = self._read_json(self.output_dir / "discovery_report.json")
        sources = load_sources()

        for source in sources:
            if not source.enabled:
                continue
            key = source.name.lower()
            source_discovery = None
            if isinstance(discovery, dict):
                source_discovery = discovery.get(key)
            self.logger.info(f"Enhance discovery cho {key} ({source.base_url})")
            report["sources"][key] = self._enhance_source(
                key, source.base_url, source_discovery
            )

        return report

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        if output_path is None:
            output_path = self.output_dir / "enhanced_discovery_report.json"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_discovery_enhancement(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay discovery enhancement. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    enhancer = DiscoveryEnhancer(logger=logger)
    if not enhancer.enabled:
        logger.info("Discovery enhancement bi tat (enhancer.enabled=false)")
        return {}

    logger.info("Tim endpoint that qua HTML/JS/source maps...")
    report = enhancer.run()
    report_path = enhancer.save_report(report)

    # In tom tat
    print(f"\n  Ket qua discovery enhancement:")
    for key, src in report.get("sources", {}).items():
        n = len(src.get("endpoint_candidates", []))
        print(f"    - {key}: {n} candidates, {src['js_bundles_scanned']} bundles, "
              f"{src['source_maps_scanned']} source maps")
    print(f"\n  Bao cao: output/enhanced_discovery_report.json")
    return report
