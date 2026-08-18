"""
API Reverse Engineering Engine - Xac dinh cach goi API that.
Task 16: Chay SAU Capability, TRUOC Data Fetcher. Chi reverse engineer.
Khong fetch stock data, khong validate, khong extraction, khong AI, khong scoring.
KHONG CO confidence field.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from .probe import ProbeClient
from .analyzers.js_analyzer import JsAnalyzer
from .analyzers.html_analyzer import HtmlAnalyzer
from .analyzers.request_sequence import RequestSequenceAnalyzer

try:
    from ..utils.source_loader import load_sources
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.source_loader import load_sources
    from utils.config_loader import load_settings


class ReverserEngine:
    """Orchestrator: doc input, reverse engineer moi endpoint."""

    DEFAULT_MAX_PROBES = 10

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None,
                 config: Dict[str, Any] = None):
        self.logger = logger or logging.getLogger("reverser")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"

        if config is None:
            try:
                settings = load_settings()
                config = settings.get("reverser", {})
            except Exception:
                config = {}
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.max_probes = int(self.config.get("max_probes", self.DEFAULT_MAX_PROBES))

        self.js_analyzer = JsAnalyzer()
        self.html_analyzer = HtmlAnalyzer()
        self.sequence_analyzer = RequestSequenceAnalyzer()
        self.probe = ProbeClient(
            logger=logger,
            timeout=int(self.config.get("timeout", 10)),
            retries=int(self.config.get("retries", 2)),
            request_delay=float(self.config.get("request_delay", 1.0)),
        )
        # Cache bundle/html content de khong GET lai
        self._bundle_cache: Dict[str, str] = {}
        self._html_cache: Dict[str, str] = {}
        self._probe_cache: Dict[str, Dict] = {}

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Any]:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _get_bundle(self, url: str) -> Optional[str]:
        """Lay bundle content (cache)."""
        if url in self._bundle_cache:
            return self._bundle_cache[url]
        text = self._fetch_text(url)
        self._bundle_cache[url] = text
        return text

    def _fetch_text(self, url: str) -> Optional[str]:
        try:
            resp = self.probe.session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        return None

    # ---------- Profile building ----------

    def _is_probeable(self, url: str) -> bool:
        """Chi probe endpoint khong dynamic."""
        return not any(ch in url for ch in ("{", "}", "$"))

    def _build_profile(self, url: str, candidate: Dict[str, Any],
                       base_url: str, source_data: Dict[str, Any]) -> Dict[str, Any]:
        """Reverse engineer 1 endpoint."""
        profile = {
            "method": None,
            "url": url,
            "required_headers": {},
            "cookies_required": [],
            "csrf_required": False,
            "csrf_token_source": None,
            "query_parameters": {},
            "body_schema": None,
            "pagination": None,
            "authentication": {"required": False, "type": None, "evidence": None},
            "response_format": None,
            "sample_request": None,
            "sample_response": None,
            "evidence_refs": [],
        }

        # --- JS static analysis ---
        source_url = candidate.get("source") or base_url
        js_text = self._get_bundle(source_url)
        if js_text:
            js_result = self.js_analyzer.analyze_call_site(js_text, url)
            profile["method"] = js_result["method"]
            profile["query_parameters"] = js_result["query_parameters"]
            profile["required_headers"] = js_result["required_headers"]
            profile["body_schema"] = js_result["body_schema"]
            profile["pagination"] = js_result["pagination"]
            profile["authentication"] = js_result["authentication"]
            profile["csrf_required"] = js_result["csrf_required"]
            profile["csrf_token_source"] = js_result["csrf_token_source"]
            for k, v in js_result.items():
                if v:
                    profile["evidence_refs"].append({
                        "field": f"js.{k}", "source": source_url,
                        "evidence": candidate.get("evidence", ""),
                    })
            # Request sequence
            sequence = self.sequence_analyzer.analyze(js_text, url)
            if sequence:
                profile["request_sequence"] = sequence
                profile["evidence_refs"].append({
                    "field": "request_sequence", "source": source_url,
                    "evidence": f"{len(sequence)} call sites",
                })

        # --- HTML analysis (csrf/cookies) ---
        html = self._html_cache.get(base_url) or self._fetch_text(base_url)
        if html:
            self._html_cache[base_url] = html
            html_result = self.html_analyzer.analyze(html)
            if html_result["csrf_required"] and not profile["csrf_required"]:
                profile["csrf_required"] = True
                profile["csrf_token_source"] = html_result["csrf_token_source"]
            if html_result["cookie_hints"]:
                profile["cookies_required"] = html_result["cookie_hints"]
            for fa in html_result["form_actions"]:
                if url in fa["action"]:
                    profile["method"] = profile["method"] or fa["method"]
                    profile["evidence_refs"].append({
                        "field": "method", "source": base_url,
                        "evidence": f'<form action="{fa["action"]}" method="{fa["method"]}">',
                    })

        # --- Probe (GET only, safe) ---
        if self._is_probeable(url):
            full_url = url if url.startswith(("http://", "https://")) else urljoin(base_url, url)
            probe_result = self._probe_cache.get(full_url) or self.probe.probe(full_url, referer=base_url)
            self._probe_cache[full_url] = probe_result
            if probe_result:
                profile["response_format"] = self._guess_format(probe_result.get("content_type", ""))
                profile["sample_response"] = {
                    "status": probe_result.get("status"),
                    "content_type": probe_result.get("content_type", ""),
                    "body_sample": probe_result.get("body_sample", ""),
                    "truncated": probe_result.get("truncated", False),
                }
                if probe_result.get("status") in (401, 403):
                    profile["authentication"] = {
                        "required": True, "type": "unknown",
                        "evidence": f"probe status {probe_result.get('status')}",
                    }
                # Probe 200 + khong co method -> GET (probe dung GET)
                if probe_result.get("status") == 200 and profile.get("method") is None:
                    profile["method"] = "GET"
                profile["evidence_refs"].append({
                    "field": "sample_response", "source": full_url,
                    "evidence": f"probe GET -> {probe_result.get('status')}",
                })
                # Set-Cookie headers
                for name, value in probe_result.get("headers", {}).items():
                    if name.lower() == "set-cookie":
                        cookie_name = value.split("=")[0].strip()
                        if cookie_name and cookie_name not in profile["cookies_required"]:
                            profile["cookies_required"].append(cookie_name)

        # --- sample_request ---
        profile["sample_request"] = self._build_sample_request(profile, url, base_url)

        return profile

    def _build_sample_request(self, profile: Dict, url: str, base_url: str) -> Optional[Dict]:
        """Xay sample request: thay param bang gia tri mau (1, FPT)."""
        if not self._is_probeable(url):
            return None
        full_url = url if url.startswith(("http://", "https://")) else urljoin(base_url, url)
        # Them query params mau
        if profile.get("query_parameters"):
            parts = []
            for name, spec in profile["query_parameters"].items():
                value = "1" if spec.get("type") == "number" else "FPT"
                parts.append(f"{name}={value}")
            sep = "&" if "?" in full_url else "?"
            full_url = f"{full_url}{sep}{'&'.join(parts)}"
        return {
            "method": profile.get("method") or "GET",
            "url": full_url,
            "headers": profile.get("required_headers") or {},
            "body": None,
        }

    @staticmethod
    def _guess_format(content_type: str) -> str:
        """Doan response format tu content type."""
        ct = content_type.lower()
        if "json" in ct:
            return "json"
        if "xml" in ct:
            return "xml"
        if "html" in ct:
            return "html"
        if "text" in ct:
            return "text"
        return "unknown"

    # ---------- Main flow ----------

    def run(self) -> Dict[str, Any]:
        """Reverse engineer tat ca endpoints."""
        report: Dict[str, Any] = {"generated_at": datetime.now().isoformat(), "sources": {}}

        enhanced = self._read_json(self.output_dir / "enhanced_discovery_report.json")
        capability = self._read_json(self.output_dir / "capability_report.json")
        sources = load_sources()

        if not isinstance(enhanced, dict) or "sources" not in enhanced:
            self.logger.error("Thieu enhanced_discovery_report.json, khong reverse engineer duoc")
            return report

        for source in sources:
            if not source.enabled:
                continue
            key = source.name.lower()
            src_enhanced = enhanced.get("sources", {}).get(key, {})
            candidates = src_enhanced.get("endpoint_candidates", []) if isinstance(src_enhanced, dict) else []
            if not candidates:
                continue

            # Chi reverse engineer endpoint khong unsupported (capability)
            cap_source = capability.get(key, {}) if isinstance(capability, dict) else {}
            excluded = set()
            for cap_name, cap_data in cap_source.items():
                if isinstance(cap_data, dict) and cap_data.get("status") == "unsupported":
                    evidence = cap_data.get("evidence", {})
                    if isinstance(evidence, dict):
                        excluded.add(evidence.get("url"))

            profiles = []
            probe_count = 0
            seen = set()
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                url = candidate.get("url")
                if not isinstance(url, str) or not url:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                if url in excluded:
                    continue
                if probe_count >= self.max_probes:
                    # Van build profile (static) nhung khong probe them
                    pass
                profile = self._build_profile(url, candidate, source.base_url, {})
                if self._is_probeable(url):
                    probe_count += 1
                profiles.append(profile)

            report["sources"][key] = {
                "base_url": source.base_url,
                "profiles": profiles,
            }

        return report

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        if output_path is None:
            output_path = self.output_dir / "endpoint_profiles.json"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_reverse_engineering(logger: logging.Logger = None) -> Dict[str, Any]:
    """Chay reverse engineering. Ham tien ich cho main.py."""
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    engine = ReverserEngine(logger=logger)
    if not engine.enabled:
        logger.info("Reverse engineering bi tat (reverser.enabled=false)")
        return {}

    logger.info("Reverse engineer API endpoints (static + probe)...")
    report = engine.run()
    report_path = engine.save_report(report)

    # In tom tat
    print(f"\n  Ket qua reverse engineering:")
    for key, src in report.get("sources", {}).items():
        n = len(src.get("profiles", []))
        print(f"    - {key}: {n} profiles")
    print(f"\n  Bao cao: output/endpoint_profiles.json")
    return report
