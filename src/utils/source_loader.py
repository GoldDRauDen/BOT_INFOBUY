"""
Source Loader - Doc va validate cau hinh nguon du lieu.
Khong request internet.
"""
import re
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional

from .source_models import SourceConfig


class SourceError(Exception):
    """Loi khi load source."""
    pass


class SourceValidationError(SourceError):
    """Loi validate source."""
    pass


def get_config_dir() -> Path:
    """Lay duong dan thu muc config."""
    return Path(__file__).parent.parent.parent / "config"


def load_yaml(file_path: Path) -> Dict[str, Any]:
    """Doc file YAML."""
    if not file_path.exists():
        raise SourceError(f"File khong ton tai: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    
    if content is None:
        raise SourceError(f"File rong: {file_path}")
    
    return content


def validate_url(url: str) -> bool:
    """Kiem tra format URL co hop le khong."""
    if not url:
        return False
    
    # Basic URL pattern
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # or IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    
    return bool(url_pattern.match(url))


def validate_source(source: Dict[str, Any], index: int) -> SourceConfig:
    """
    Validate mot source config.
    Raises: SourceValidationError neu co loi.
    """
    errors = []
    name = source.get("name")
    enabled = source.get("enabled")
    source_type = source.get("type")
    base_url = source.get("base_url")
    
    # Required fields
    if not name:
        errors.append("Thieu truong 'name'")
    elif not isinstance(name, str):
        errors.append("'name' phai la string")
    elif not name.strip():
        errors.append("'name' khong duoc rong")
    
    if enabled is None:
        errors.append("Thieu truong 'enabled'")
    elif not isinstance(enabled, bool):
        errors.append("'enabled' phai la true/false")
    
    if not source_type:
        errors.append("Thieu truong 'type'")
    elif not isinstance(source_type, str):
        errors.append("'type' phai la string")
    
    if not base_url:
        errors.append("Thieu truong 'base_url'")
    elif not isinstance(base_url, str):
        errors.append("'base_url' phai la string")
    elif not validate_url(base_url.strip()):
        errors.append(f"URL khong hop le: {base_url}")
    
    if errors:
        source_name = name if name else f"[source #{index + 1}]"
        raise SourceValidationError(
            f"Loi validate source '{source_name}':\n" +
            "\n".join(f"  - {e}" for e in errors)
        )
    
    return SourceConfig(
        name=name.strip(),
        enabled=enabled,
        type=source_type.strip(),
        base_url=base_url.strip(),
        description=source.get("description", "").strip() if source.get("description") else None,
        timeout=source.get("timeout", 30),
        retry=source.get("retry", 3),
    )


def load_sources() -> List[SourceConfig]:
    """
    Doc va validate cau hinh nguon tu config/sources.yaml.
    Returns: List[SourceConfig]
    Raises:
        SourceError: Loi doc file
        SourceValidationError: Loi validate
    """
    config_path = get_config_dir() / "sources.yaml"
    content = load_yaml(config_path)
    
    if "sources" not in content:
        raise SourceError("Thieu truong 'sources' trong cau hinh")
    
    sources_list = content["sources"]
    if not isinstance(sources_list, list):
        raise SourceError("'sources' phai la danh sach (list)")
    
    # Parse va validate
    sources = []
    seen_names = {}
    
    for i, raw_source in enumerate(sources_list):
        if not isinstance(raw_source, dict):
            raise SourceError(f"Source #{i + 1} phai la mot dictionary")
        
        source = validate_source(raw_source, i)
        
        # Check trung ten
        if source.name in seen_names:
            raise SourceValidationError(
                f"Trung ten source: '{source.name}' xuat hien 2 lan "
                f"(tai source #{seen_names[source.name] + 1} va #{i + 1})"
            )
        seen_names[source.name] = i
        
        sources.append(source)
    
    return sources


def get_enabled_sources() -> List[SourceConfig]:
    """Lay chi nhung source dang duoc enable."""
    return [s for s in load_sources() if s.enabled]


def print_sources(sources: List[SourceConfig]) -> None:
    """In danh sach source ra console."""
    if not sources:
        print("  (Khong co nguon nao)")
        return
    
    print(f"\n  Da load {len(sources)} nguon:")
    for i, src in enumerate(sources, 1):
        status = "[ON ]" if src.enabled else "[OFF]"
        desc = f" - {src.description}" if src.description else ""
        print(f"    {i}. {status} {src.name} ({src.type}){desc}")
        print(f"       URL: {src.base_url}")
