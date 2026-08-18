"""
Capability Analyzer - Xac dinh kha nang cung cap du lieu cua moi nguon.
CHI DOC du lieu tu discovery_report.json + connectivity_report.json + sources.yaml.
KHONG request lai bat ky URL nao da co trong discovery_report.json.
KHONG scoring. KHONG confidence. KHONG AI inference.

Rules (Task 5 Revision):
  A. Phan loai endpoint truoc khi match (json/xml/html/openapi/robots/rss/error_page)
  B. Chuan hoa du lieu truoc khi match (unescape, NFKC, lowercase, whitespace)
  C. Blacklist Home/Index/Error/404 -> khong tinh la content evidence
  D. Keyword strong/weak, uu tien strong khi tie-break
"""
import html
import json
import copy
import re
import logging
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from ..utils.source_loader import load_sources
    from ..utils.source_models import SourceConfig
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.source_loader import load_sources
    from utils.source_models import SourceConfig
    from utils.config_loader import load_settings


class CapabilityAnalyzer:
    """Phan tich capability dua tren discovery_report.json (offline)."""

    # 16 capabilities voi keyword sets
    CAPABILITIES = {
        "stock_list": ["symbol", "code", "ticker", "ma_ck", "company", "stock", "stocks", "danh-sach", "listing"],
        "current_price": ["price", "quote", "last", "close", "gia", "bang-gia"],
        "historical_price": ["history", "historical", "lich-su", "ngay", "date"],
        "ohlcv": ["ohlcv", "open", "high", "low", "volume"],
        "financial_reports": ["financial", "revenue", "profit", "asset", "tai-chinh", "doanh-thu", "loi-nhuan"],
        "dividends": ["dividend", "co-tuc", "cash-dividend"],
        "bonus_shares": ["bonus", "chia-khuyen-mai", "stock-dividend"],
        "rights_issue": ["rights", "warrant", "quyen-mua"],
        "foreign_trading": ["foreign", "khoi-ngoai", "north"],
        "company_news": ["news", "tin-tuc", "tin", "article", "title"],
        "company_announcements": ["announcement", "thong-bao", "notice"],
        "sector": ["sector", "industry", "nganh", "icb"],
        "market_cap": ["market-cap", "marketcap", "von-hoa"],
        "eps": ["eps", "earnings-per-share"],
        "pe_ratio": ["pe_ratio", "pe-ratio", "p-e", "p/e", "price-earnings"],
        "pb_ratio": ["pb_ratio", "pb-ratio", "p-b", "p/b", "price-book"],
    }

    # Rule D: strong keywords - match gan nhu chac chan capability
    STRONG_KEYWORDS = {
        "stock_list": ["symbol", "ticker", "ma_ck", "company_code"],
        "current_price": ["last_price", "close_price", "gia_hien_tai"],
        "historical_price": ["trading_date", "lich_su_gia"],
        "ohlcv": ["ohlcv"],
        "financial_reports": ["net_profit", "total_assets", "doanh_thu", "loi_nhuan"],
        "dividends": ["cash_dividend", "co_tuc"],
        "bonus_shares": ["stock_dividend", "chia_khuyen_mai"],
        "rights_issue": ["exercise_price", "quyen_mua"],
        "foreign_trading": ["foreign_buy", "foreign_sell", "khoi_ngoai"],
        "company_news": ["published_date", "article"],
        "company_announcements": ["announcement_type", "thong_bao"],
        "sector": ["icb_code", "industry_code"],
        "market_cap": ["market_cap", "marketcap", "von_hoa"],
        "eps": ["earnings_per_share", "eps"],
        "pe_ratio": ["price_earnings", "pe_ratio"],
        "pb_ratio": ["price_book", "pb_ratio"],
    }

    # Rule D: weak keywords - keyword chung, can endpoint nhom phu hop
    WEAK_KEYWORDS = {
        "stock_list": ["stock", "stocks", "company", "code", "listing", "danh-sach"],
        "current_price": ["price", "quote", "last", "close", "gia", "bang-gia"],
        "historical_price": ["history", "historical", "date", "ngay", "lich-su"],
        "ohlcv": ["open", "high", "low", "volume"],
        "financial_reports": ["financial", "revenue", "profit", "asset", "tai-chinh"],
        "dividends": ["dividend"],
        "bonus_shares": ["bonus"],
        "rights_issue": ["rights", "warrant"],
        "foreign_trading": ["foreign", "north"],
        "company_news": ["news", "tin-tuc", "tin", "title"],
        "company_announcements": ["announcement", "notice"],
        "sector": ["sector", "industry", "nganh"],
        "market_cap": ["free_float"],
        "eps": [],
        "pe_ratio": ["p/e", "p-e"],
        "pb_ratio": ["p/b", "p-b"],
    }

    # Cac truong metadata trong entry can match
    META_FIELDS = [
        "url", "redirect_url", "json_keys", "xml_root_tag",
        "html_title", "meta_description", "h1", "response_sample",
    ]

    # Evidence priority (1 = manh nhat, 9 = yeu nhat)
    EVIDENCE_PRIORITY = {
        "json_keys": 1,
        "xml_root_tag": 2,
        "openapi": 3,
        "response_sample": 4,
        "html_title": 5,
        "meta_description": 6,
        "h1": 7,
        "url": 8,
        "redirect_url": 9,
    }

    # Content evidence: du dieu kien supported (priority 1-7)
    # URL path (8) va redirect_url (9) KHONG phai content evidence
    CONTENT_EVIDENCE_FIELDS = [
        "json_keys", "xml_root_tag", "openapi", "response_sample",
        "html_title", "meta_description", "h1",
    ]

    # Rule C: blacklist title/description/h1 chung chung
    BLACKLIST_TITLES = [
        "home", "index", "trang chu", "trang chủ", "404", "not found",
        "error", "access denied", "forbidden", "unauthorized",
        "server error", "maintenance", "403", "401", "405", "500",
    ]

    # Gap #4: JSON wrapper keys - chi la container, khong phai content
    JSON_WRAPPER_KEYS = {"data", "result", "items", "results", "content", "body"}

    # Nhom endpoint phu hop cho weak keyword (Rule D)
    # weak match chi duoc tinh khi endpoint thuoc nhom nay
    WEAK_OK_TYPES = {"json", "xml", "openapi", "html"}

    DEFAULT_OUTPUT = "capability_report.json"

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("capability_analyzer")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.output_dir = self.base_dir / "output"

    # ---------- IO ----------

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """Doc file JSON, tra ve None neu loi."""
        if not path.exists():
            self.logger.error(f"File khong ton tai: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Loi doc {path}: {e}")
            return None

    def _collect_endpoints(self, source_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Gom toan bo endpoint entries cua mot source tu discovery_report.
        Moi entry them truong "_type" de biet nguon goc (robots, sitemap, api_tests...).
        """
        endpoints = []
        if not isinstance(source_data, dict):
            return endpoints

        for key, value in source_data.items():
            if key == "api_tests" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item = dict(item)
                        item["_type"] = "api_tests"
                        endpoints.append(item)
            elif isinstance(value, dict):
                item = dict(value)
                item["_type"] = key
                endpoints.append(item)
        return endpoints

    # ---------- Rule B: Normalization ----------

    def _normalize(self, value: Any) -> Any:
        """
        Chuan hoa du lieu truoc khi match (Rule B):
        1. html.unescape - decode HTML entities
        2. unicodedata.normalize NFKC - chuan hoa Unicode
        3. lower - lowercase
        4. gop khoang trang
        """
        if isinstance(value, str):
            text = html.unescape(value)
            text = unicodedata.normalize("NFKC", text)
            text = text.lower()
            text = re.sub(r"\s+", " ", text)
            return text
        if isinstance(value, list):
            return [self._normalize(v) for v in value]
        return value

    # ---------- Rule A: Endpoint classification ----------

    def _classify_endpoint(self, endpoint: Dict[str, Any]) -> str:
        """
        Phan loai endpoint truoc khi match (Rule A).
        Returns: json | xml | html | openapi | robots | rss | error_page | unknown
        """
        _type = endpoint.get("_type")
        status = endpoint.get("status")
        found = endpoint.get("found")
        content_type = (endpoint.get("content_type") or "").lower()

        # Error page: status >= 400 hoac (found=false + html)
        if status is not None and status >= 400:
            return "error_page"
        if not found and content_type and "html" in content_type:
            return "error_page"

        # robots.txt: cam match content (Gap #7)
        if _type == "robots":
            return "robots"

        # RSS: chi match company_news
        if _type == "rss":
            return "rss"

        # OpenAPI/Swagger spec
        if _type in ("swagger", "openapi") and found:
            return "openapi"

        # Content-type routing (Gap #2: json > xml > html)
        if "json" in content_type:
            return "json"
        if "xml" in content_type:
            # application/xhtml+xml la HTML thuc chat
            if "xhtml" in content_type:
                return "html"
            return "xml"
        if "html" in content_type:
            return "html"

        return "unknown"

    def _is_blacklisted(self, text: str) -> bool:
        """Kiem tra text co thuoc blacklist khong (Rule C)."""
        if not text:
            return False
        norm = self._normalize(text)
        return any(b in norm for b in self.BLACKLIST_TITLES)

    # ---------- Matching ----------

    def _match_keywords_in_field(
        self, value: Any, keywords: List[str], is_path: bool = False
    ) -> List[str]:
        """
        Tim keyword nao xuat hien trong mot truong metadata (sau normalize).
        - is_path=True (URL paths): match theo segment prefix
        - is_path=False (text): match theo word boundary
        """
        if value is None:
            return []
        norm = self._normalize(value)
        if isinstance(norm, str):
            if is_path:
                tokens = re.split(r"[^a-z0-9]+", norm)
                return [
                    k for k in keywords
                    if any(t.startswith(k) for t in tokens if t)
                ]
            return [k for k in keywords if re.search(rf"\b{re.escape(k)}\b", norm)]
        if isinstance(norm, list):
            text = " ".join(str(v) for v in norm if v is not None)
            return [k for k in keywords if re.search(rf"\b{re.escape(k)}\b", text)]
        return []

    def _match_endpoint(
        self, endpoint: Dict[str, Any], cap_name: str, keywords: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Kiem tra endpoint co match keyword capability.
        Tra ve match co priority cao nhat (manh nhat).

        Rule D: chi content evidence khi:
          - strong keyword match, HOAC
          - weak keyword match + endpoint nhom phu hop (json/xml/openapi/html)
        """
        classification = self._classify_endpoint(endpoint)

        # Cam match content: robots, error_page (Gap #7, Rule A)
        if classification in ("robots", "error_page"):
            return None

        strong_kw = self.STRONG_KEYWORDS.get(cap_name, [])
        weak_kw = self.WEAK_KEYWORDS.get(cap_name, [])

        # Lua chon keywords theo nhom endpoint
        if classification == "rss":
            # RSS chi match company_news, qua response_sample
            if cap_name != "company_news":
                return None
            matched = self._match_keywords_in_field(endpoint.get("response_sample"), strong_kw + weak_kw)
            if matched:
                return {"matched_field": "response_sample", "matched_keywords": matched}
            return None

        if classification == "openapi":
            matched = self._match_keywords_in_field(endpoint.get("json_keys"), strong_kw)
            if matched:
                return {"matched_field": "openapi", "matched_keywords": matched}
            matched = self._match_keywords_in_field(endpoint.get("response_sample"), strong_kw + weak_kw)
            if matched:
                return {"matched_field": "openapi", "matched_keywords": matched}
            return None

        best = None

        def _consider(candidate):
            nonlocal best
            if candidate is None:
                return
            if best is None or (
                self.EVIDENCE_PRIORITY.get(candidate["matched_field"], 99)
                < self.EVIDENCE_PRIORITY.get(best["matched_field"], 99)
            ):
                best = candidate

        # Content evidence fields: json_keys > xml_root_tag > response_sample >
        # html_title > meta_description > h1
        # Rule D: strong match luon tinh; weak match can nhom phu hop
        fields_order = ["json_keys", "xml_root_tag", "response_sample",
                        "html_title", "meta_description", "h1"]

        for field in fields_order:
            if field not in endpoint:
                continue
            value = endpoint.get(field)

            # Gap #4: json_keys chi wrapper (data/result/items) -> match qua response_sample
            if field == "json_keys" and isinstance(value, list):
                non_wrapper = [k for k in value if k not in self.JSON_WRAPPER_KEYS]
                if not non_wrapper:
                    # Chi wrapper keys: thu match response_sample voi strong keywords
                    strong_matched = self._match_keywords_in_field(
                        endpoint.get("response_sample"), strong_kw
                    )
                    if strong_matched:
                        _consider({"matched_field": "response_sample", "matched_keywords": strong_matched})
                    continue

            # Rule D: response_sample cua HTML pages la raw HTML chua tags
            # (title, link, date...) -> chi strong keyword match, weak la noise
            if field == "response_sample" and classification == "html":
                strong_only = self._match_keywords_in_field(value, strong_kw)
                if strong_only:
                    _consider({"matched_field": field, "matched_keywords": strong_only})
                continue

            strong_matched = self._match_keywords_in_field(value, strong_kw)
            if strong_matched:
                _consider({"matched_field": field, "matched_keywords": strong_matched})
                continue

            # Weak match chi khi nhom endpoint phu hop
            if classification in self.WEAK_OK_TYPES or classification == "unknown":
                weak_matched = self._match_keywords_in_field(value, weak_kw)
                if weak_matched:
                    _consider({"matched_field": field, "matched_keywords": weak_matched})

        # Blacklist check cho html fields (Rule C)
        for field in ("html_title", "meta_description", "h1"):
            if field not in endpoint:
                continue
            value = endpoint.get(field)
            if isinstance(value, str) and self._is_blacklisted(value):
                # Xoa match blacklist khoi best
                if best is not None and best["matched_field"] == field:
                    best = None

        # URL path (priority 8) va redirect_url (priority 9): chi tao candidate
        url_value = endpoint.get("url")
        if url_value is not None:
            matched = self._match_keywords_in_field(url_value, keywords, is_path=True)
            if matched:
                _consider({"matched_field": "url", "matched_keywords": matched})

        redirect_value = endpoint.get("redirect_url")
        if redirect_value is not None:
            matched = self._match_keywords_in_field(redirect_value, keywords, is_path=True)
            if matched:
                _consider({"matched_field": "redirect_url", "matched_keywords": matched})

        return best

    # ---------- Phan tich ----------

    def _analyze_capability(
        self, endpoints: List[Dict[str, Any]], cap_name: str, keywords: List[str]
    ) -> Dict[str, Any]:
        """
        Decision rules (Task 5 Revision):
          supported  <- endpoint found=true (khong 401/403/405) + content evidence
                        (json_keys, xml_root_tag, openapi, response_sample,
                         html_title, meta_description, h1)
          unsupported<- endpoint URL match keyword + found=false
                        + KHONG co bat ky content evidence nao o source
          unknown    <- 401/403/405, sample khong du du lieu, hoac thieu bang chung

        URL path / redirect_url khong du de ket luan supported (chi evidence phu).
        """
        best_supported = None

        # Pass 1: tim endpoint supported tot nhat
        for ep in endpoints:
            if not ep.get("found"):
                continue
            status = ep.get("status")
            if status in (401, 403, 405):
                continue
            match = self._match_endpoint(ep, cap_name, keywords)
            if not match:
                continue
            # Chi content evidence moi du supported
            if match["matched_field"] not in self.CONTENT_EVIDENCE_FIELDS:
                continue

            candidate = {
                "endpoint": ep,
                "match": match,
                "priority": self.EVIDENCE_PRIORITY.get(match["matched_field"], 99),
                "strong": bool(set(match["matched_keywords"]) & set(self.STRONG_KEYWORDS.get(cap_name, []))),
            }
            if best_supported is None or self._better_evidence(candidate, best_supported):
                best_supported = candidate

        if best_supported is not None:
            ep = best_supported["endpoint"]
            match = best_supported["match"]
            return {
                "status": "supported",
                "evidence": {
                    "url": ep.get("url"),
                    "content_type": ep.get("content_type"),
                    "http_status": ep.get("status"),
                    "matched_field": match["matched_field"],
                    "matched_keywords": match["matched_keywords"],
                    "detail": f"Endpoint found=true, content evidence o truong "
                              f"{match['matched_field']}: {', '.join(match['matched_keywords'])}",
                },
            }

        # Pass 2: co content evidence nao o source khong (de phan biet unsupported/unknown)
        has_content_evidence = False
        for ep in endpoints:
            if not ep.get("found"):
                continue
            status = ep.get("status")
            if status in (401, 403, 405):
                continue
            match = self._match_endpoint(ep, cap_name, keywords)
            if match and match["matched_field"] in self.CONTENT_EVIDENCE_FIELDS:
                has_content_evidence = True
                break

        url_404_matched = False
        for ep in endpoints:
            if ep.get("found"):
                continue
            url_value = ep.get("url")
            if url_value is None:
                continue
            matched = self._match_keywords_in_field(url_value, keywords, is_path=True)
            if matched:
                url_404_matched = True
                break

        # Pass 3: unsupported - chi khi URL 404 match va KHONG co content evidence nao
        if url_404_matched and not has_content_evidence:
            for ep in endpoints:
                if ep.get("found"):
                    continue
                url_value = ep.get("url")
                if url_value is None:
                    continue
                matched = self._match_keywords_in_field(url_value, keywords, is_path=True)
                if matched:
                    return {
                        "status": "unsupported",
                        "evidence": {
                            "url": ep.get("url"),
                            "content_type": ep.get("content_type"),
                            "http_status": ep.get("status"),
                            "matched_field": "url",
                            "matched_keywords": matched,
                            "detail": f"URL chua keyword, endpoint khong ton tai "
                                      f"(found=false, status={ep.get('status')}), "
                                      f"va khong co content evidence nao o source",
                        },
                    }

        # Pass 4: unknown
        return {"status": "unknown", "evidence": None}

    def _better_evidence(self, candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
        """
        So sanh 2 candidate supported (Rule 8 + Rule D):
        1. Evidence priority cao hon (so nho hon)
        2. Cung priority: strong keyword truoc weak
        3. response_size lon hon
        """
        if candidate["priority"] != current["priority"]:
            return candidate["priority"] < current["priority"]
        if candidate["strong"] != current["strong"]:
            return candidate["strong"] and not current["strong"]
        cand_size = candidate["endpoint"].get("response_size_bytes") or 0
        curr_size = current["endpoint"].get("response_size_bytes") or 0
        return cand_size > curr_size

    def analyze_source(self, source_name: str, source_data: Dict[str, Any]) -> Dict[str, Any]:
        """Phan tich toan bo capabilities cua mot source."""
        endpoints = self._collect_endpoints(source_data)
        capabilities = {}
        checked_at = datetime.now().isoformat()

        for cap_name in self.CAPABILITIES.keys():
            keywords = self.CAPABILITIES[cap_name]
            result = self._analyze_capability(endpoints, cap_name, keywords)
            result["checked_at"] = checked_at
            capabilities[cap_name] = result

        return capabilities

    def analyze_all(self, discovery_data: Dict[str, Any]) -> Dict[str, Any]:
        """Phan tich tat ca source co trong discovery report."""
        report = {}
        generated_at = datetime.now().isoformat()
        for source_name, source_data in discovery_data.items():
            if not isinstance(source_data, dict):
                continue
            report[source_name] = self.analyze_source(source_name, source_data)
        report["generated_at"] = generated_at
        return report

    # ---------- Report ----------

    def save_report(self, report: Dict[str, Any], output_path: str = None) -> str:
        """Luu capability report ra JSON."""
        if output_path is None:
            output_path = self.output_dir / self.DEFAULT_OUTPUT
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def merge_enhanced_discovery(discovery: Dict[str, Any],
                             enhanced: Dict[str, Any]) -> Dict[str, Any]:
    """
    Task 15: Merge endpoint candidates tu enhanced_discovery_report vao
    discovery report (field api_tests) - CHI endpoint moi, khong trung URL.

    Quy tac:
    - Giu nguyen discovery goc (khong sua bat ky endpoint nao)
    - Candidate da co URL trong discovery -> bo qua (khong trung request)
    - Candidate dynamic=true -> bo qua (khong GET duoc, co placeholder)
    - Con lai -> them vao api_tests entries
    """
    merged = copy.deepcopy(discovery)

    if not isinstance(enhanced, dict):
        return merged

    enhanced_sources = enhanced.get("sources", {})
    if not isinstance(enhanced_sources, dict):
        return merged

    for source_key, source_data in enhanced_sources.items():
        if not isinstance(source_data, dict):
            continue
        candidates = source_data.get("endpoint_candidates", [])
        if not isinstance(candidates, list):
            continue

        source_discovery = merged.get(source_key)
        if not isinstance(source_discovery, dict):
            source_discovery = {}
            merged[source_key] = source_discovery

        # Gom toan bo URL da co trong discovery source
        existing_urls = set()
        for ep_name, ep_value in source_discovery.items():
            if ep_name == "api_tests":
                if isinstance(ep_value, list):
                    for item in ep_value:
                        if isinstance(item, dict) and isinstance(item.get("url"), str):
                            existing_urls.add(item["url"])
            elif isinstance(ep_value, dict) and isinstance(ep_value.get("url"), str):
                existing_urls.add(ep_value["url"])

        api_tests = source_discovery.get("api_tests")
        if not isinstance(api_tests, list):
            api_tests = []
            source_discovery["api_tests"] = api_tests

        # Them endpoint moi
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            url = candidate.get("url")
            if not isinstance(url, str) or not url:
                continue
            # Khong trung URL
            if url in existing_urls:
                continue
            # Dynamic route khong GET duoc -> bo qua
            if candidate.get("dynamic"):
                continue
            api_tests.append({
                "url": url,
                "found": True,
                "status": 200,
                "content_type": "text/plain",
                "response_sample": "",
                "evidence": candidate.get("evidence"),
                "method": candidate.get("method"),
                "type": candidate.get("type"),
            })
            existing_urls.add(url)

    return merged


def run_capability_test(logger: logging.Logger = None) -> Dict[str, Any]:
    """
    Chay phan tich capability cho tat ca nguon.
    Ham tien ich cho main.py.
    """
    if logger is None:
        logger = logging.getLogger("stock_scanner")

    analyzer = CapabilityAnalyzer(logger=logger)

    # Doc input files
    discovery_data = analyzer._read_json(analyzer.output_dir / "discovery_report.json")
    if discovery_data is None:
        logger.error("Thieu discovery_report.json, khong the phan tich capability")
        return {}

    # Task 15: merge enhanced discovery (neu co) - chi them endpoint moi
    enhanced_data = analyzer._read_json(analyzer.output_dir / "enhanced_discovery_report.json")
    if enhanced_data is not None:
        logger.info("Phat hien enhanced_discovery_report.json, merge endpoint moi...")
        discovery_data = merge_enhanced_discovery(discovery_data, enhanced_data)

    # Phan tich offline
    logger.info("Bat dau phan tich capability (offline, khong request)...")
    report = analyzer.analyze_all(discovery_data)
    report_path = analyzer.save_report(report)

    # In tom tat
    print(f"\n  Ket qua phan tich capability:")
    for source_name, caps in report.items():
        if source_name == "generated_at":
            continue
        counts = {"supported": 0, "unsupported": 0, "unknown": 0}
        for cap in caps.values():
            status = cap.get("status", "unknown")
            if status in counts:
                counts[status] += 1
        print(f"    - {source_name}: supported={counts['supported']}, "
              f"unsupported={counts['unsupported']}, unknown={counts['unknown']}")

    print(f"\n  Bao cao: output/capability_report.json")
    return report
