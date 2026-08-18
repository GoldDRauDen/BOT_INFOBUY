"""
JS Analyzer - Phan tich call sites trong JS de reverse engineer API.
Task 16: Static analysis (khong HTTP). Thu thuat quy tac, khong AI.
"""
import re
from typing import Any, Dict, List, Optional

# Call site patterns
AXIOS_CALL = re.compile(
    r"""axios\.(get|post|put|delete|patch)\s*\(\s*["'`]([^"'`]+)["'`]\s*(?:,\s*([^;]{0,300}))?""",
    re.DOTALL,
)
FETCH_CALL = re.compile(
    r"""fetch\s*\(\s*["'`]([^"'`]+)["'`]\s*(?:,\s*(\{(?:[^{}]|\{[^{}]*\}){0,600}\}))?""",
    re.DOTALL,
)
# Params trong URL query
QUERY_PARAM = re.compile(r"[?&]([A-Za-z_]\w*)=([^&\"'`\s]+)")
# Object literal keys (cho phep dash cho header names)
OBJ_KEYS = re.compile(r"""["']?([A-Za-z_][\w-]*)["']?\s*:\s*([^,}\]]{1,60})""")
# Header names
HEADER_NAMES = re.compile(r"""["'](?:X-|x-)?[A-Za-z-]*(?:Token|Auth|Key|Cookie|CSRF)[A-Za-z-]*["']""",
                          re.IGNORECASE)
# Pagination patterns
PAGINATION = re.compile(
    r"""(?:page|offset|limit|size|per_page|perPage|pageSize|pagesize|start|rows)\s*[:=]\s*([^,;}\s]{1,20})""",
    re.IGNORECASE,
)
# Auth token patterns (cho phep key co quote)
AUTH_PATTERN = re.compile(
    r"""["']?(?:Authorization|Bearer|api[_-]?key|token|access_token)["']?\s*[:=]\s*["'`]?([^"'`,;}\s]{1,60})""",
    re.IGNORECASE,
)
# CSRF patterns
CSRF_PATTERN = re.compile(r"""csrf|X-CSRF|x_csrf|_token""", re.IGNORECASE)

PAGINATION_PARAMS = {"page", "offset", "limit", "size", "per_page", "perPage",
                     "pageSize", "pagesize", "start", "rows"}


class JsAnalyzer:
    """Phan tich JS text quanh call site."""

    def analyze_call_site(self, text: str, url: str) -> Dict[str, Any]:
        """
        Phan tich 1 doan JS chua call site cua url.
        Gioi han window +-2000 ky tu quanh call site (tranh noise toan bundle).
        """
        result = {
            "method": None,
            "query_parameters": {},
            "required_headers": {},
            "body_schema": None,
            "pagination": None,
            "authentication": {"required": False, "type": None, "evidence": None},
            "csrf_required": False,
            "csrf_token_source": None,
        }

        # Tim vi tri call site cua url -> gioi han window
        window = text
        for marker in (url, url.split("?")[0]):
            pos = text.find(marker)
            if pos >= 0:
                start = max(0, pos - 2000)
                end = min(len(text), pos + 2000)
                window = text[start:end]
                break

        # --- axios calls ---
        for m in AXIOS_CALL.finditer(window):
            method, call_url, rest = m.group(1), m.group(2), m.group(3)
            if url not in call_url and call_url not in url:
                continue
            result["method"] = method.upper()
            if rest:
                self._analyze_object(rest, result, text)

        # --- fetch calls ---
        for m in FETCH_CALL.finditer(window):
            call_url, options = m.group(1), m.group(2)
            if url not in call_url and call_url not in url:
                continue
            if options:
                # method trong options (co hoac khong quote)
                mm = re.search(r"""["']?method["']?\s*:\s*["'](\w+)["']""", options)
                if mm:
                    result["method"] = mm.group(1).upper()
                # headers object (nested braces)
                hm = re.search(r"""["']?headers["']?\s*:\s*(\{(?:[^{}]|\{[^{}]*\}){0,300}\})""", options)
                if hm:
                    self._analyze_object(hm.group(1), result, text)
                # body
                bm = re.search(r"""["']body["']\s*:\s*JSON\.stringify\((\{[^{}]{0,300}\})\)""", options)
                if bm:
                    self._analyze_object(bm.group(1), result, text)

        # --- query params trong URL ---
        for pm in QUERY_PARAM.finditer(url):
            name, value = pm.group(1), pm.group(2)
            result["query_parameters"][name] = {
                "type": self._guess_type(value),
                "required": False,
                "evidence": f"url: {url}",
            }
            if name in PAGINATION_PARAMS:
                result["pagination"] = {
                    "type": "query_page" if name in ("page", "offset") else "query_size",
                    "param": name,
                    "evidence": f"url: {url}",
                }

        # --- pagination tu context ---
        if result["pagination"] is None:
            for pm in PAGINATION.finditer(window):
                name = pm.group(0).split(":")[0].split("=")[0].strip()
                value = pm.group(1) if pm.lastindex else ""
                # Bo qua value 1 ky tu (minified noise: pageSize:e)
                if value and len(value.strip()) == 1:
                    continue
                result["pagination"] = {
                    "type": "query_page",
                    "param": name,
                    "evidence": pm.group(0).strip(),
                }
                break

        # --- auth ---
        for am in AUTH_PATTERN.finditer(window):
            key = am.group(0).split(":")[0].split("=")[0].strip()
            value = am.group(1) if am.lastindex else ""
            result["authentication"] = {
                "required": True,
                "type": "bearer" if "bearer" in (key + value).lower() else "token",
                "evidence": am.group(0).strip(),
            }
            break

        # --- csrf ---
        if CSRF_PATTERN.search(window):
            result["csrf_required"] = True
            cm = re.search(r"""["'](X-CSRF-Token|X-CSRFToken|csrf_token|_csrf)["']""", window)
            result["csrf_token_source"] = cm.group(1) if cm else "header"

        return result

    def _analyze_object(self, obj_text: str, result: Dict[str, Any], full_text: str):
        """Phan tich object literal: params/headers/body."""
        for km in OBJ_KEYS.finditer(obj_text):
            key, value = km.group(1), km.group(2).strip()
            key_lower = key.lower()
            if key in PAGINATION_PARAMS:
                result["query_parameters"][key] = {
                    "type": self._guess_type(value),
                    "required": False,
                    "evidence": f"{key}: {value}",
                }
                if result["pagination"] is None:
                    result["pagination"] = {
                        "type": "query_page",
                        "param": key,
                        "evidence": f"{key}: {value}",
                    }
            elif "header" in key_lower or "csrf" in key_lower or "token" in key_lower:
                result["required_headers"][key] = value
                if CSRF_PATTERN.search(key):
                    result["csrf_required"] = True
                    result["csrf_token_source"] = key
            elif key in ("Accept", "Content-Type", "X-Requested-With",
                         "Authorization", "User-Agent", "Referer"):
                result["required_headers"][key] = value
                if CSRF_PATTERN.search(key):
                    result["csrf_required"] = True
                    result["csrf_token_source"] = key
            else:
                # Param data
                if result["body_schema"] is None:
                    result["body_schema"] = {}
                result["body_schema"][key] = {
                    "type": self._guess_type(value),
                    "evidence": f"{key}: {value}",
                }

    @staticmethod
    def _guess_type(value: str) -> str:
        """Doan kieu tu gia tri."""
        v = value.strip().strip("'\"")
        if re.fullmatch(r"-?\d+", v):
            return "number"
        if re.fullmatch(r"-?\d+\.\d+", v):
            return "number"
        if v in ("true", "false"):
            return "boolean"
        if v.startswith(("${", "$.")):
            return "string"
        return "string"
