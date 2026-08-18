"""
Unit tests cho connectivity_tester.
"""
import sys
import json
import time
import socket
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scanner.connectivity_models import ConnectivityResult
from scanner.connectivity_tester import ConnectivityTester
from utils.source_models import SourceConfig


class TestConnectivityResult:
    """Test ConnectivityResult model."""
    
    def test_create_empty_result(self):
        result = ConnectivityResult(name="TEST", url="https://test.com")
        assert result.name == "TEST"
        assert result.url == "https://test.com"
        assert result.reachable is False
        assert result.response_time_ms == 0.0
        assert result.ssl_ok is False
        assert result.http_status is None
    
    def test_new_schema_fields(self):
        """Kiem tra cac truong moi: http_status, response_time_ms, ssl_ok."""
        result = ConnectivityResult(
            name="TEST",
            url="https://test.com",
            reachable=True,
            http_status=200,
            response_time_ms=150.5,
            ssl_ok=True
        )
        d = result.to_dict()
        assert "http_status" in d
        assert "response_time_ms" in d
        assert "ssl_ok" in d
        assert "status_code" not in d
        assert "response_time" not in d
        assert "ssl_valid" not in d
    
    def test_to_dict(self):
        result = ConnectivityResult(
            name="TEST",
            url="https://test.com",
            reachable=True,
            http_status=200,
            response_time_ms=150.5,
            ssl_ok=True
        )
        d = result.to_dict()
        assert d["name"] == "TEST"
        assert d["reachable"] is True
        assert d["http_status"] == 200
        assert d["response_time_ms"] == 150.5
        assert d["ssl_ok"] is True
    
    def test_status_ok(self):
        result = ConnectivityResult(
            name="TEST",
            url="https://test.com",
            reachable=True,
            http_status=200
        )
        assert "OK" in result.status
        assert "200" in result.status
    
    def test_status_fail(self):
        result = ConnectivityResult(
            name="TEST",
            url="https://test.com",
            reachable=False,
            error="Timeout"
        )
        assert "FAIL" in result.status
        assert "Timeout" in result.status


class TestConnectivityTester:
    """Test ConnectivityTester."""
    
    def test_init_default_values(self):
        tester = ConnectivityTester()
        assert tester.timeout == 10
        assert tester.max_retries == 3
        assert tester.USER_AGENT in tester.session.headers["User-Agent"]
    
    def test_init_custom_values(self):
        tester = ConnectivityTester(timeout=5, max_retries=5)
        assert tester.timeout == 5
        assert tester.max_retries == 5
    
    @patch('socket.create_connection')
    @patch('ssl.create_default_context')
    def test_check_ssl_https(self, mock_ssl_ctx, mock_conn):
        """Test SSL check cho HTTPS."""
        tester = ConnectivityTester()
        
        mock_sock = MagicMock()
        mock_ssl_sock = MagicMock()
        mock_ssl_sock.getpeercert.return_value = {"subject": "test"}
        
        mock_conn.return_value.__enter__.return_value = mock_sock
        mock_ssl_ctx.return_value.wrap_socket.return_value.__enter__.return_value = mock_ssl_sock
        
        valid, error = tester.check_ssl("https://example.com")
        assert valid is True
        assert error is None
    
    @patch('socket.create_connection')
    def test_check_ssl_timeout(self, mock_conn):
        """Test SSL check timeout."""
        tester = ConnectivityTester(timeout=1)
        mock_conn.side_effect = socket.timeout("timed out")
        
        valid, error = tester.check_ssl("https://example.com")
        assert valid is False
        assert "timeout" in error.lower()
    
    @patch('scanner.connectivity_tester.time.time')
    @patch('scanner.connectivity_tester.time.sleep')
    def test_test_source_success(self, mock_sleep, mock_time):
        """Test kiem tra source thanh cong."""
        mock_time.side_effect = [0, 0.5, 0.5, 1.0]
        
        with patch('scanner.connectivity_tester.requests.Session') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            
            mock_head_response = MagicMock()
            mock_head_response.status_code = 200
            
            mock_get_response = MagicMock()
            mock_get_response.status_code = 200
            mock_get_response.url = "https://example.com"
            
            mock_session.head.return_value = mock_head_response
            mock_session.get.return_value = mock_get_response
            
            tester = ConnectivityTester()
            source = SourceConfig(
                name="TEST",
                enabled=True,
                type="official",
                base_url="https://example.com"
            )
            
            result = tester.test_source(source)
            
            assert result.name == "TEST"
            assert result.reachable is True
            assert result.http_status == 200
            assert result.retry == 0
    
    @patch('scanner.connectivity_tester.time.sleep')
    def test_test_source_timeout_with_retry(self, mock_sleep):
        """Test retry khi timeout."""
        from requests.exceptions import Timeout
        
        with patch('scanner.connectivity_tester.requests.Session') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_session.head.side_effect = Timeout("timed out")
            
            tester = ConnectivityTester(timeout=1, max_retries=3)
            source = SourceConfig(
                name="TEST",
                enabled=True,
                type="official",
                base_url="https://example.com"
            )
            
            result = tester.test_source(source)
            
            assert result.reachable is False
            assert result.retry == 3
            assert "Timeout" in result.error
            assert mock_session.head.call_count == 3
    
    @patch('scanner.connectivity_tester.time.sleep')
    def test_test_source_connection_error(self, mock_sleep):
        """Test retry khi connection error."""
        from requests.exceptions import ConnectionError as ReqConnectionError
        
        with patch('scanner.connectivity_tester.requests.Session') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_session.head.side_effect = ReqConnectionError("Failed to connect")
            
            tester = ConnectivityTester(max_retries=2)
            source = SourceConfig(
                name="TEST",
                enabled=True,
                type="official",
                base_url="https://example.com"
            )
            
            result = tester.test_source(source)
            
            assert result.reachable is False
            assert result.retry == 2
            assert "Connection Error" in result.error
    
    def test_test_source_redirect(self):
        """Test phat hien redirect."""
        with patch('scanner.connectivity_tester.time.sleep'):
            with patch('scanner.connectivity_tester.time.time', side_effect=[0, 0.1, 0.1, 0.2]):
                with patch('scanner.connectivity_tester.requests.Session') as mock_session_class:
                    mock_session = MagicMock()
                    mock_session_class.return_value = mock_session
                    
                    mock_head_response = MagicMock()
                    mock_get_response = MagicMock()
                    mock_get_response.status_code = 301
                    mock_get_response.url = "https://www.example.com/redirect"
                    
                    mock_session.head.return_value = mock_head_response
                    mock_session.get.return_value = mock_get_response
                    
                    tester = ConnectivityTester()
                    source = SourceConfig(
                        name="TEST",
                        enabled=True,
                        type="official",
                        base_url="https://example.com"
                    )
                    
                    result = tester.test_source(source)
                    
                    assert result.redirect is True
                    assert result.redirect_url == "https://www.example.com/redirect"
    
    def test_test_source_ssl_error_fallback(self):
        """Test SSL error khong dong nghia website down."""
        from requests.exceptions import SSLError
        
        with patch('scanner.connectivity_tester.time.sleep'):
            # time values for: head, get (ssl fallback), and close
            with patch('scanner.connectivity_tester.time.time', side_effect=[0, 0.1, 0.1, 0.2]):
                with patch('scanner.connectivity_tester.requests.Session') as mock_session_class:
                    mock_session = MagicMock()
                    mock_session_class.return_value = mock_session
                    
                    # HEAD raises SSL error
                    mock_session.head.side_effect = SSLError("SSL cert error")
                    
                    # GET with verify=False succeeds
                    mock_get_response = MagicMock()
                    mock_get_response.status_code = 200
                    mock_get_response.url = "https://example.com"
                    mock_session.get.return_value = mock_get_response
                    
                    tester = ConnectivityTester(timeout=5, max_retries=1)
                    source = SourceConfig(
                        name="TEST",
                        enabled=True,
                        type="official",
                        base_url="https://example.com"
                    )
                    
                    result = tester.test_source(source)
                    
                    # SSL failed nhung HTTP van OK
                    assert result.ssl_ok is False
                    assert result.ssl_error is not None
                    assert result.reachable is True
                    assert result.http_status == 200
    
    def test_test_all_continues_on_error(self):
        """Test khong crash khi mot source loi."""
        sources = [
            SourceConfig(name="OK", enabled=True, type="official", base_url="https://ok.com"),
            SourceConfig(name="FAIL", enabled=True, type="official", base_url="https://fail.com"),
        ]
        
        tester = ConnectivityTester()
        
        # Mock test_source de fail o source thu 2
        call_count = [0]
        
        def patched_test(source):
            call_count[0] += 1
            if source.name == "FAIL":
                raise Exception("Simulated error")
            result = ConnectivityResult(
                name=source.name,
                url=source.base_url,
                reachable=True,
                http_status=200
            )
            return result
        
        tester.test_source = patched_test
        results = tester.test_all(sources)
        
        assert len(results) == 2
        assert results[0].name == "OK"
        assert results[0].reachable is True
        assert results[1].name == "FAIL"
        assert results[1].reachable is False


class TestSaveReport:
    """Test luu bao cao JSON."""
    
    def test_save_report_new_schema(self):
        """Kiem tra bao cao dung schema moi."""
        tester = ConnectivityTester()
        
        results = [
            ConnectivityResult(
                name="TEST1", 
                url="https://test1.com", 
                reachable=True, 
                http_status=200,
                response_time_ms=150.5,
                ssl_ok=True
            ),
            ConnectivityResult(
                name="TEST2", 
                url="https://test2.com", 
                reachable=False, 
                error="Timeout",
                response_time_ms=0.0,
                ssl_ok=False
            ),
        ]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        
        try:
            saved_path = tester.save_report(results, temp_path)
            assert Path(saved_path).exists()
            
            with open(temp_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            assert report["total_sources"] == 2
            assert report["reachable"] == 1
            assert report["unreachable"] == 1
            
            # Kiem tra schema
            test1 = report["results"]["test1"]
            assert "http_status" in test1
            assert "response_time_ms" in test1
            assert "ssl_ok" in test1
            assert test1["http_status"] == 200
            assert test1["response_time_ms"] == 150.5
            assert test1["ssl_ok"] is True
            
            test2 = report["results"]["test2"]
            assert "http_status" in test2
            assert test2["http_status"] is None
            assert "ssl_ok" in test2
            assert test2["ssl_ok"] is False
        finally:
            Path(temp_path).unlink(missing_ok=True)


def run_tests():
    """Chay tat ca tests."""
    test_classes = [
        TestConnectivityResult,
        TestConnectivityTester,
        TestSaveReport,
    ]
    
    passed = 0
    failed = 0
    
    for test_class in test_classes:
        print(f"\n{'='*50}")
        print(f"  {test_class.__name__}")
        print('='*50)
        
        instance = test_class()
        for name in dir(instance):
            if name.startswith('test_'):
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    print(f"        {e}")
                    failed += 1
    
    print(f"\n{'='*50}")
    print(f"  Ket qua: {passed} passed, {failed} failed")
    print('='*50)
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
