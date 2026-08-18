"""
Connectivity Tester - Kiem tra ket noi den cac nguon du lieu.
Chi kiem tra ket noi, khong parse du lieu.
"""
import ssl
import json
import time
import socket
import logging
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from datetime import datetime

import requests
from requests.exceptions import (
    RequestException,
    Timeout,
    ConnectionError as ReqConnectionError,
    SSLError,
    TooManyRedirects
)

try:
    from ..utils.source_loader import load_sources
    from ..utils.source_models import SourceConfig
except ImportError:
    from utils.source_loader import load_sources
    from utils.source_models import SourceConfig
from .connectivity_models import ConnectivityResult


class ConnectivityTester:
    """Kiem tra ket noi den cac nguon."""
    
    DEFAULT_TIMEOUT = 10
    MAX_RETRIES = 3
    USER_AGENT = "StockScanner/1.0"
    
    def __init__(self, timeout: int = None, max_retries: int = None, logger: logging.Logger = None):
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.max_retries = max_retries or self.MAX_RETRIES
        self.logger = logger or logging.getLogger("connectivity_tester")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
    
    def check_ssl(self, url: str) -> tuple[bool, Optional[str]]:
        """
        Kiem tra SSL certificate.
        Returns: (ssl_ok, ssl_error)
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            port = parsed.port or 443
            
            context = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=self.timeout) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
                    if cert:
                        return True, None
                    return False, "No certificate"
        except SSLError as e:
            return False, f"SSL Error: {e}"
        except socket.timeout:
            return False, "SSL timeout"
        except Exception as e:
            return False, str(e)
    
    def test_source(self, source: SourceConfig) -> ConnectivityResult:
        """
        Kiem tra ket noi mot nguon.
        Retry neu that bai.
        """
        result = ConnectivityResult(
            name=source.name,
            url=source.base_url
        )
        
        for attempt in range(self.max_retries):
            try:
                self.logger.info(f"Kiem tra {source.name} (lan {attempt + 1}/{self.max_retries})")
                
                # HEAD request truoc
                start_time = time.time()
                head_response = self.session.head(
                    source.base_url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    verify=True
                )
                head_time = time.time() - start_time
                
                # GET request
                start_time = time.time()
                get_response = self.session.get(
                    source.base_url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    verify=True,
                    stream=True
                )
                get_time = time.time() - start_time
                
                # Tinh response time trung binh (ms)
                result.response_time_ms = round((head_time + get_time) / 2 * 1000, 2)
                result.http_status = get_response.status_code
                result.reachable = get_response.status_code < 500
                
                # Kiem tra redirect
                if get_response.url != source.base_url:
                    result.redirect = True
                    result.redirect_url = get_response.url
                
                # Kiem tra SSL cho HTTPS
                if source.base_url.startswith("https"):
                    ssl_ok, ssl_error = self.check_ssl(source.base_url)
                    result.ssl_ok = ssl_ok
                    result.ssl_error = ssl_error
                
                result.retry = attempt
                get_response.close()  # Dong connection
                
                self.logger.info(
                    f"{source.name}: OK - Status {result.http_status}, "
                    f"Time {result.response_time_ms}ms, SSL {result.ssl_ok}"
                )
                return result
                
            except Timeout as e:
                result.retry = attempt + 1
                result.error = f"Timeout after {self.timeout}s"
                self.logger.warning(f"{source.name}: Timeout (lan {attempt + 1})")
                
                if attempt == self.max_retries - 1:
                    break
                time.sleep(1)
                
            except SSLError as e:
                # SSL Error - khac voi website down
                result.retry = attempt + 1
                result.ssl_ok = False
                result.ssl_error = str(e)
                
                # Thu lai voi verify=False
                self.logger.warning(f"{source.name}: SSL Error (lan {attempt + 1}), thu verify=False")
                
                if attempt == self.max_retries - 1:
                    # SSL that bai nhung van thu HTTP
                    try:
                        self.logger.info(f"{source.name}: Thu GET voi verify=False")
                        start_time = time.time()
                        get_response = self.session.get(
                            source.base_url,
                            timeout=self.timeout,
                            allow_redirects=True,
                            verify=False,
                            stream=True
                        )
                        result.response_time_ms = round((time.time() - start_time) * 1000, 2)
                        result.http_status = get_response.status_code
                        result.reachable = get_response.status_code < 500
                        result.retry = attempt + 1
                        get_response.close()
                        self.logger.info(
                            f"{source.name}: OK (SSL failed but HTTP OK) - "
                            f"Status {result.http_status}, Time {result.response_time_ms}ms"
                        )
                        return result
                    except Exception:
                        result.error = f"SSL Error: {e}"
                        result.reachable = False
                        break
                time.sleep(1)
            
            except ReqConnectionError as e:
                result.retry = attempt + 1
                result.error = f"Connection Error: {e}"
                self.logger.warning(f"{source.name}: Connection Error (lan {attempt + 1})")
                
                if attempt == self.max_retries - 1:
                    break
                time.sleep(1)
                
            except TooManyRedirects as e:
                result.retry = attempt + 1
                result.error = f"Too many redirects: {e}"
                result.redirect = True
                self.logger.warning(f"{source.name}: Too many redirects")
                break
                
            except RequestException as e:
                result.retry = attempt + 1
                result.error = f"Request Error: {e}"
                self.logger.warning(f"{source.name}: Request Error (lan {attempt + 1})")
                
                if attempt == self.max_retries - 1:
                    break
                time.sleep(1)
                
            except Exception as e:
                result.error = f"Unexpected Error: {e}"
                self.logger.error(f"{source.name}: Unexpected error - {e}")
                break
        
        result.reachable = False
        return result
    
    def test_all(self, sources: List[SourceConfig] = None) -> List[ConnectivityResult]:
        """
        Kiem tra tat ca cac nguon.
        Neu khong truyen sources, doc tu config.
        """
        if sources is None:
            sources = load_sources()
        
        results = []
        for source in sources:
            try:
                result = self.test_source(source)
                results.append(result)
            except Exception as e:
                # Khong crash, ghi loi va tiep tuc
                self.logger.error(f"Loi khi kiem tra {source.name}: {e}")
                results.append(ConnectivityResult(
                    name=source.name,
                    url=source.base_url,
                    reachable=False,
                    error=f"Tester Error: {e}"
                ))
        
        return results
    
    def save_report(self, results: List[ConnectivityResult], output_path: str = None) -> str:
        """
        Luu ket qua ra JSON.
        Returns: Duong dan file da luu.
        """
        from pathlib import Path
        
        if output_path is None:
            output_dir = Path(__file__).parent.parent.parent / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "connectivity_report.json"
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "total_sources": len(results),
            "reachable": sum(1 for r in results if r.reachable),
            "unreachable": sum(1 for r in results if not r.reachable),
            "results": {r.name.lower(): r.to_dict() for r in results}
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Da luu bao cao: {output_path}")
        return str(output_path)


def run_connectivity_test(logger: logging.Logger = None) -> List[ConnectivityResult]:
    """
    Chay kiem tra ket noi cho tat ca nguon.
    Ham tien ich cho main.py.
    """
    if logger is None:
        logger = logging.getLogger("stock_scanner")
    
    tester = ConnectivityTester(logger=logger)
    
    # Chi lay nhung source enable
    sources = [s for s in load_sources() if s.enabled]
    
    logger.info(f"Bat dau kiem tra {len(sources)} nguon...")
    
    results = tester.test_all(sources)
    report_path = tester.save_report(results)
    
    # In tom tat
    reachable = sum(1 for r in results if r.reachable)
    print(f"\n  Ket qua: {reachable}/{len(results)} nguon hoat dong")
    
    return results
