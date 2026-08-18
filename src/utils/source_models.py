"""
Source models - Data classes cho nguon du lieu.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SourceConfig:
    """Cau hinh mot nguon du lieu."""
    name: str
    enabled: bool
    type: str
    base_url: str
    description: Optional[str] = None
    timeout: int = 30
    retry: int = 3
    
    def __post_init__(self):
        """Clean data sau khi khoi tao."""
        self.name = self.name.strip()
        self.base_url = self.base_url.strip()
        if self.description:
            self.description = self.description.strip()
    
    def __str__(self) -> str:
        status = "ON" if self.enabled else "OFF"
        return f"[{status}] {self.name} ({self.type}) - {self.base_url}"
