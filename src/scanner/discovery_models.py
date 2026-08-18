"""
Discovery Result - Data models cho ket qua kham pha endpoint.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class DiscoveryResult:
    """Ket qua kham pha mot nguon."""
    name: str
    url: str
    robots: Optional[Dict[str, Any]] = None
    sitemap: Optional[Dict[str, Any]] = None
    rss: Optional[Dict[str, Any]] = None
    favicon: Optional[Dict[str, Any]] = None
    graphql: Optional[Dict[str, Any]] = None
    swagger: Optional[Dict[str, Any]] = None
    openapi: Optional[Dict[str, Any]] = None
    api_tests: List[Dict[str, Any]] = field(default_factory=list, init=False)
    error: Optional[str] = None
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __init__(
        self,
        name: str,
        url: str,
        robots: Optional[Dict[str, Any]] = None,
        sitemap: Optional[Dict[str, Any]] = None,
        rss: Optional[Dict[str, Any]] = None,
        favicon: Optional[Dict[str, Any]] = None,
        graphql: Optional[Dict[str, Any]] = None,
        swagger: Optional[Dict[str, Any]] = None,
        openapi: Optional[Dict[str, Any]] = None,
        api_tests: Optional[List[Dict[str, Any]]] = None,
        possible_api: Optional[List[str]] = None,
        error: Optional[str] = None,
        checked_at: Optional[str] = None
    ):
        self.name = name
        self.url = url
        self.robots = robots
        self.sitemap = sitemap
        self.rss = rss
        self.favicon = favicon
        self.graphql = graphql
        self.swagger = swagger
        self.openapi = openapi
        self.api_tests = api_tests if api_tests is not None else []
        if possible_api is not None:
            self.possible_api = possible_api
        self.error = error
        self.checked_at = checked_at if checked_at is not None else datetime.now().isoformat()

    @property
    def possible_api(self) -> List[str]:
        """Danh sach cac api endpoint tim thay."""
        return [t["url"] for t in self.api_tests if t.get("found", False)]

    @possible_api.setter
    def possible_api(self, value: List[str]):
        self.api_tests = [
            {
                "url": url,
                "status": 200,
                "found": True,
                "response_time_ms": 0.0,
                "retry": 0,
                "checked_at": datetime.now().isoformat()
            }
            for url in value
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Convert sang dict cho JSON."""
        d = asdict(self)
        d["possible_api"] = self.possible_api
        return d

    @property
    def summary(self) -> Dict[str, Any]:
        """Tom tat ket qua kham pha."""
        return {
            "resources": {
                "robots": self.robots.get("found", False) if isinstance(self.robots, dict) else bool(self.robots),
                "sitemap": self.sitemap.get("found", False) if isinstance(self.sitemap, dict) else bool(self.sitemap),
                "rss": self.rss.get("found", False) if isinstance(self.rss, dict) else bool(self.rss),
                "favicon": self.favicon.get("found", False) if isinstance(self.favicon, dict) else bool(self.favicon),
                "graphql": self.graphql.get("found", False) if isinstance(self.graphql, dict) else bool(self.graphql),
                "swagger": self.swagger.get("found", False) if isinstance(self.swagger, dict) else bool(self.swagger),
                "openapi": self.openapi.get("found", False) if isinstance(self.openapi, dict) else bool(self.openapi),
            },
            "possible_api": self.possible_api,
        }
