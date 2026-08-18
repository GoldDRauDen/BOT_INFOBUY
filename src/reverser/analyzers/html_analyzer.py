"""
HTML Analyzer - Phan tich HTML de tim csrf, form actions, cookie hints.
Task 16: Static analysis (khong HTTP).
"""
import re
from typing import Any, Dict


class HtmlAnalyzer:
    """Phan tich HTML tim thong tin goi API."""

    CSRF_META = re.compile(r"""<meta[^>]+name\s*=\s*["'](?:csrf-token|csrf_token|_csrf)["'][^>]*content\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    FORM_ACTION = re.compile(r"""<form[^>]+action\s*=\s*["']([^"']+)["'][^>]*method\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    FORM_FIELDS = re.compile(r"""<input[^>]+name\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    COOKIE_JS = re.compile(r"""document\.cookie\s*=\s*["']([^"';]+)""", re.IGNORECASE)

    def analyze(self, html: str) -> Dict[str, Any]:
        """Phan tich HTML, tra csrf + form hints."""
        result = {
            "csrf_required": False,
            "csrf_token_source": None,
            "form_actions": [],
            "cookie_hints": [],
        }

        # CSRF meta
        m = self.CSRF_META.search(html)
        if m:
            result["csrf_required"] = True
            result["csrf_token_source"] = f"meta:{m.group(0)[:80]}"

        # Form actions
        for fm in self.FORM_ACTION.finditer(html):
            action, method = fm.group(1), fm.group(2).upper()
            # Lay fields cua form nay (gop cac input truoc </form> - don gian: toan bo)
            result["form_actions"].append({
                "action": action,
                "method": method,
            })

        # Cookie hints
        for cm in self.COOKIE_JS.finditer(html):
            result["cookie_hints"].append(cm.group(1))

        return result
