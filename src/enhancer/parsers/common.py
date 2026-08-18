"""
Parser chung - Regex thu thuat, khong AI. Dung cho cac parser khac.
"""
import re

# Asset extensions can loai bo
ASSET_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".pdf", ".zip", ".mp4",
    ".webp", ".avif", ".json", ".xml", ".txt",
}

# Endpoint patterns
FETCH_PATTERN = re.compile(r"""fetch\s*\(\s*["'`]([^"'`]+)["'`]""")
AXIOS_PATTERN = re.compile(r"""axios\.(?:get|post|put|delete|patch)\s*\(\s*["'`]([^"'`]+)["'`]""")
XHR_PATTERN = re.compile(r"""\.open\s*\(\s*["'](?:GET|POST|PUT|DELETE|PATCH)["']\s*,\s*["'`]([^"'`]+)["'`]""")
WEBSOCKET_PATTERN = re.compile(r"""new\s+WebSocket\s*\(\s*["'`]([^"'`]+)["'`]""")
GRAPHQL_PATTERN = re.compile(r"""["'`]([^"'`]*(?:graphql)[^"'`]*)["'`]""", re.IGNORECASE)
# String literal chua URL path (cho JS bundles)
STRING_URL_PATTERN = re.compile(r"""["'`]((?:/|https?://|wss?://)[^"'`\s]{2,})["'`]""")
# Object key dang path/url/endpoint/api
OBJ_URL_PATTERN = re.compile(
    r"""(?:path|url|endpoint|api|href|action)\s*[:=]\s*["'`]([^"'`]+)["'`]""",
    re.IGNORECASE,
)
# Dynamic route template: /api/quote/${symbol} hoac /api/quote/{symbol}
DYNAMIC_PATTERN = re.compile(r"\$\{|\{[\w]+\}")

# Prefix hop le cho endpoint
VALID_PREFIXES = ("/", "http://", "https://", "ws://", "wss://")


def is_valid_endpoint(url: str) -> bool:
    """Endpoint co hop le khong (prefix + khong phai asset + khong noise)."""
    if not url or len(url) < 2:
        return False
    if not url.startswith(VALID_PREFIXES):
        return False
    if url.startswith(("//", "#", "javascript:", "mailto:")):
        return False
    if "node_modules" in url or "webpack://" in url or "chrome-extension://" in url:
        return False
    # Loai bo asset extensions (phai la path sau query)
    path = url.split("?")[0].split("#")[0].lower()
    if any(path.endswith(ext) for ext in ASSET_EXTENSIONS):
        return False
    # Loai bo data URI / base64
    if "data:" in url or "base64" in url.lower():
        return False
    return True


def is_dynamic(url: str) -> bool:
    """URL co placeholder (${...} hoac {param}) khong."""
    return bool(DYNAMIC_PATTERN.search(url))


def guess_type(url: str) -> str:
    """Doan loai endpoint tu url."""
    if "graphql" in url.lower():
        return "graphql"
    if url.startswith(("ws://", "wss://")):
        return "websocket"
    if "swagger" in url.lower() or "openapi" in url.lower():
        return "openapi"
    return "rest"


def dedup_ordered(items):
    """Dedup giu thu tu."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def extract_from_js(text: str, found_in: str, source_url: str) -> list:
    """
    Trich endpoint candidates tu JS text (inline hoac bundle).
    Tra ve list dict {url, method, type, evidence, dynamic}.
    """
    candidates = []

    # fetch()
    for m in FETCH_PATTERN.finditer(text):
        url = m.group(1)
        if is_valid_endpoint(url):
            candidates.append({
                "url": url, "method": "GET", "type": guess_type(url),
                "evidence": m.group(0), "dynamic": is_dynamic(url),
            })

    # axios.get/post/...
    for m in AXIOS_PATTERN.finditer(text):
        url = m.group(1)
        if is_valid_endpoint(url):
            method = m.group(0).split(".")[1].split("(")[0].upper()
            candidates.append({
                "url": url, "method": method, "type": guess_type(url),
                "evidence": m.group(0), "dynamic": is_dynamic(url),
            })

    # XHR open()
    for m in XHR_PATTERN.finditer(text):
        url = m.group(1)
        if is_valid_endpoint(url):
            candidates.append({
                "url": url, "method": "GET", "type": guess_type(url),
                "evidence": m.group(0), "dynamic": is_dynamic(url),
            })

    # WebSocket
    for m in WEBSOCKET_PATTERN.finditer(text):
        url = m.group(1)
        if is_valid_endpoint(url):
            candidates.append({
                "url": url, "method": None, "type": "websocket",
                "evidence": m.group(0), "dynamic": is_dynamic(url),
            })

    # GraphQL paths (fetch/axios pattern da bat; bat them string don le)
    for m in GRAPHQL_PATTERN.finditer(text):
        url = m.group(1)
        if is_valid_endpoint(url):
            candidates.append({
                "url": url, "method": "POST", "type": "graphql",
                "evidence": m.group(0), "dynamic": is_dynamic(url),
            })

    # Object key: path/url/endpoint/api
    for m in OBJ_URL_PATTERN.finditer(text):
        url = m.group(1)
        if is_valid_endpoint(url):
            candidates.append({
                "url": url, "method": None, "type": guess_type(url),
                "evidence": m.group(0), "dynamic": is_dynamic(url),
            })

    # Dedup theo url + method + evidence
    seen = set()
    result = []
    for c in candidates:
        key = (c["url"], c["method"], c["evidence"])
        if key not in seen:
            seen.add(key)
            c["found_in"] = found_in
            c["source"] = source_url
            result.append(c)
    return result
