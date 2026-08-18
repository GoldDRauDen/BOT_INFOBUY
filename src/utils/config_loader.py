import yaml
from pathlib import Path
from typing import List, Dict, Any


class ConfigError(Exception):
    """Loi cau hinh - supports both Vietnamese and ASCII output."""
    pass


def _fmt(msg: str) -> str:
    """Format message for display."""
    return msg


def get_config_dir() -> Path:
    """Lấy đường dẫn thư mục config."""
    return Path(__file__).parent.parent.parent / "config"


def load_sources() -> List[Dict[str, Any]]:
    """Đọc và validate sources.yaml."""
    sources_path = get_config_dir() / "sources.yaml"
    
    if not sources_path.exists():
        raise ConfigError(
            f"File cấu hình nguồn không tồn tại: {sources_path}\n"
            "Vui lòng tạo config/sources.yaml"
        )
    
    with open(sources_path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    
    if content is None:
        raise ConfigError(
            f"File {sources_path} trống. Cần định nghĩa 'sources'."
        )
    
    if "sources" not in content:
        raise ConfigError(
            f"File {sources_path} thiếu khóa 'sources'.\n"
            "Format: sources: [...]"
        )
    
    sources = content["sources"]
    if not isinstance(sources, list):
        raise ConfigError(
            f"'{sources_path}' phải có 'sources' là danh sách (list)."
        )
    
    return sources


def load_settings() -> Dict[str, Any]:
    """Đọc settings.yaml."""
    settings_path = get_config_dir() / "settings.yaml"
    
    if not settings_path.exists():
        return {"app": {"name": "Stock Scanner", "version": "1.0.0"}}
    
    with open(settings_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
