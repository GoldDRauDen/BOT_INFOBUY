"""
Connectivity Result - Data models cho ket qua kiem tra ket noi.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class ConnectivityResult:
    """Ket qua kiem tra ket noi mot nguon."""
    name: str
    url: str
    reachable: bool = False
    http_status: Optional[int] = None
    response_time_ms: float = 0.0
    ssl_ok: bool = False
    ssl_error: Optional[str] = None
    redirect: bool = False
    redirect_url: Optional[str] = None
    retry: int = 0
    error: Optional[str] = None
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert sang dict cho JSON."""
        return asdict(self)
    
    @property
    def status(self) -> str:
        """Trang thai tong quan."""
        if self.reachable:
            return f"OK ({self.http_status})"
        return f"FAIL ({self.error or 'Unknown'})"
