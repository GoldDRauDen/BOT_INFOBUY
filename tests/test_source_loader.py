"""
Unit tests cho source_loader.
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.source_loader import (
    load_sources,
    validate_url,
    validate_source,
    get_enabled_sources,
    SourceError,
    SourceValidationError,
)


class TestValidateUrl:
    """Test validate_url()."""
    
    def test_valid_https(self):
        assert validate_url("https://www.hsx.vn/")
    
    def test_valid_http(self):
        assert validate_url("http://localhost:8080/api")
    
    def test_valid_ip(self):
        assert validate_url("http://192.168.1.1:3000")
    
    def test_invalid_empty(self):
        assert not validate_url("")
    
    def test_invalid_no_protocol(self):
        assert not validate_url("www.example.com")
    
    def test_invalid_random_string(self):
        assert not validate_url("abcdefgh")


class TestValidateSource:
    """Test validate_source()."""
    
    def test_valid_source(self):
        source = {
            "name": "TEST",
            "enabled": True,
            "type": "official",
            "base_url": "https://test.com",
        }
        result = validate_source(source, 0)
        assert result.name == "TEST"
        assert result.enabled is True
        assert result.type == "official"
        assert result.base_url == "https://test.com"
    
    def test_missing_name(self):
        source = {
            "enabled": True,
            "type": "official",
            "base_url": "https://test.com",
        }
        try:
            validate_source(source, 0)
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "name" in str(e).lower()
    
    def test_missing_enabled(self):
        source = {
            "name": "TEST",
            "type": "official",
            "base_url": "https://test.com",
        }
        try:
            validate_source(source, 0)
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "enabled" in str(e).lower()
    
    def test_missing_type(self):
        source = {
            "name": "TEST",
            "enabled": True,
            "base_url": "https://test.com",
        }
        try:
            validate_source(source, 0)
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "type" in str(e).lower()
    
    def test_missing_url(self):
        source = {
            "name": "TEST",
            "enabled": True,
            "type": "official",
        }
        try:
            validate_source(source, 0)
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "url" in str(e).lower() or "base_url" in str(e).lower()
    
    def test_invalid_url(self):
        source = {
            "name": "TEST",
            "enabled": True,
            "type": "official",
            "base_url": "not-a-valid-url",
        }
        try:
            validate_source(source, 0)
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "URL khong hop le" in str(e)
    
    def test_enabled_is_not_bool(self):
        source = {
            "name": "TEST",
            "enabled": "yes",  # Should be bool
            "type": "official",
            "base_url": "https://test.com",
        }
        try:
            validate_source(source, 0)
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "enabled" in str(e).lower()


class TestLoadSources:
    """Test load_sources() voi file tam thoi."""
    
    @classmethod
    def setup_class(cls):
        """Backup va tao config tam thoi."""
        cls.original_config = Path(__file__).parent.parent / "config" / "sources.yaml"
        cls.backup_path = cls.original_config.with_suffix(".yaml.bak")
        
        # Backup original
        if cls.original_config.exists():
            shutil.copy(cls.original_config, cls.backup_path)
    
    def setup_method(self):
        """Backup config truoc moi test."""
        if self.original_config.exists():
            shutil.copy(self.original_config, self.backup_path)
    
    def teardown_method(self):
        """Khoi phuc config sau moi test."""
        if self.backup_path.exists():
            shutil.move(self.backup_path, self.original_config)
    
    def _write_config(self, content: str):
        """Ghi config tam thoi."""
        with open(self.original_config, "w", encoding="utf-8") as f:
            f.write(content)
    
    def test_load_valid_sources(self):
        self._write_config("""
sources:
  - name: TEST1
    enabled: true
    type: official
    base_url: https://test1.com
  - name: TEST2
    enabled: false
    type: api
    base_url: http://test2.com/api
""")
        sources = load_sources()
        assert len(sources) == 2
        assert sources[0].name == "TEST1"
        assert sources[1].name == "TEST2"
    
    def test_missing_sources_key(self):
        self._write_config("""
wrong_key: []
""")
        try:
            load_sources()
            assert False, "Phai raise error"
        except SourceError as e:
            assert "sources" in str(e).lower()
    
    def test_duplicate_names(self):
        self._write_config("""
sources:
  - name: TEST
    enabled: true
    type: official
    base_url: https://test1.com
  - name: TEST
    enabled: true
    type: api
    base_url: http://test2.com
""")
        try:
            load_sources()
            assert False, "Phai raise error"
        except SourceValidationError as e:
            assert "trung ten" in str(e).lower() or "2 lan" in str(e).lower()
    
    def test_empty_sources_list(self):
        self._write_config("""
sources: []
""")
        sources = load_sources()
        assert len(sources) == 0
    
    def test_sources_not_list(self):
        self._write_config("""
sources: "not a list"
""")
        try:
            load_sources()
            assert False, "Phai raise error"
        except SourceError as e:
            assert "danh sach" in str(e).lower() or "list" in str(e).lower()


class TestGetEnabledSources:
    """Test get_enabled_sources()."""
    
    @classmethod
    def setup_class(cls):
        cls.original_config = Path(__file__).parent.parent / "config" / "sources.yaml"
        cls.backup_path = cls.original_config.with_suffix(".yaml.bak")
        if cls.original_config.exists():
            shutil.copy(cls.original_config, cls.backup_path)
    
    def setup_method(self):
        if self.original_config.exists():
            shutil.copy(self.original_config, self.backup_path)
    
    def teardown_method(self):
        if self.backup_path.exists():
            shutil.move(self.backup_path, self.original_config)
    
    def _write_config(self, content: str):
        with open(self.original_config, "w", encoding="utf-8") as f:
            f.write(content)
    
    def test_only_enabled_returned(self):
        self._write_config("""
sources:
  - name: ENABLED1
    enabled: true
    type: official
    base_url: https://test1.com
  - name: DISABLED
    enabled: false
    type: api
    base_url: http://test2.com
  - name: ENABLED2
    enabled: true
    type: unofficial
    base_url: https://test3.com
""")
        enabled = get_enabled_sources()
        assert len(enabled) == 2
        assert all(s.enabled for s in enabled)
        assert enabled[0].name == "ENABLED1"
        assert enabled[1].name == "ENABLED2"


def run_tests():
    """Chay tat ca tests."""
    import traceback
    
    test_classes = [
        TestValidateUrl,
        TestValidateSource,
        TestLoadSources,
        TestGetEnabledSources,
    ]
    
    passed = 0
    failed = 0
    
    for test_class in test_classes:
        print(f"\n{'='*50}")
        print(f"  {test_class.__name__}")
        print('='*50)
        
        instance = test_class()
        if hasattr(instance, 'setup_class'):
            test_class.setup_class()
        
        for name in dir(instance):
            if name.startswith('test_'):
                try:
                    if hasattr(instance, 'setup_method'):
                        instance.setup_method()
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
                finally:
                    if hasattr(instance, 'teardown_method'):
                        instance.teardown_method()
    
    print(f"\n{'='*50}")
    print(f"  Ket qua: {passed} passed, {failed} failed")
    print('='*50)
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
