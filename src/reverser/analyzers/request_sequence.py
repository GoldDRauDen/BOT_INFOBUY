"""
Request Sequence - Gom call sites theo thu tu xuat hien.
Task 16: Chi ghi thu tu xuat hien, khong suy doan luong.
"""
import re
from typing import Any, Dict, List

# Tim vi tri cac call sites trong text
URL_CALL = re.compile(
    r"""(?:axios\.(?:get|post|put|delete|patch)|fetch|\.open\s*\()\s*\(\s*["'`]([^"'`]+)["'`]"""
)


class RequestSequenceAnalyzer:
    """Gom call sites cung bundle theo thu tu."""

    def analyze(self, js_text: str, target_url: str) -> List[Dict[str, Any]]:
        """
        Tim cac call sites cua target_url + cac call sites gan no.
        Tra ve [{step, url, purpose}] theo thu tu xuat hien.
        """
        calls = []
        for m in URL_CALL.finditer(js_text):
            url = m.group(1)
            if url and not url.startswith(("http", "//", "#")) or url.startswith("/"):
                calls.append((m.start(), url))

        # Tim vi tri target
        target_pos = None
        for pos, url in calls:
            if url == target_url:
                target_pos = pos
                break
        if target_pos is None:
            return []

        # Gom cac call trong cung vung (2000 ky tu truoc/sau target)
        window_start = target_pos - 2000
        window_end = target_pos + 2000
        sequence = []
        for pos, url in calls:
            if window_start <= pos <= window_end:
                purpose = "target" if url == target_url else "related"
                sequence.append({"step": len(sequence) + 1, "url": url, "purpose": purpose})

        return sequence
