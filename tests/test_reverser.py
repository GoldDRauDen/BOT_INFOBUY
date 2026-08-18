"""
Unit tests cho API Reverse Engineering (Task 16).
Offline tests (mock HTTP) cho analyzers + engine.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reverser.analyzers.js_analyzer import JsAnalyzer
from reverser.analyzers.html_analyzer import HtmlAnalyzer
from reverser.analyzers.request_sequence import RequestSequenceAnalyzer
from reverser.engine import ReverserEngine


class TestJsAnalyzer:
    """Test phan tich call sites."""

    def test_axios_post_body(self):
        js = 'axios.post("/api/orders", {symbol: "FPT", qty: 100})'
        result = JsAnalyzer().analyze_call_site(js, "/api/orders")
        assert result["method"] == "POST"
        assert result["body_schema"]["symbol"]["type"] == "string"
        assert result["body_schema"]["qty"]["type"] == "number"

    def test_fetch_headers_and_method(self):
        js = 'fetch("/api/data", {method: "POST", headers: {"X-CSRF-Token": token}})'
        result = JsAnalyzer().analyze_call_site(js, "/api/data")
        assert result["method"] == "POST"
        assert result["csrf_required"] is True
        assert "X-CSRF-Token" in result["required_headers"]

    def test_query_params(self):
        js = 'fetch("/api/data?page=1&size=20")'
        result = JsAnalyzer().analyze_call_site(js, "/api/data?page=1&size=20")
        assert "page" in result["query_parameters"]
        assert result["query_parameters"]["page"]["type"] == "number"
        assert result["pagination"] is not None

    def test_auth(self):
        js = 'axios.get("/api/x", {headers: {"Authorization": "Bearer " + token}})'
        result = JsAnalyzer().analyze_call_site(js, "/api/x")
        assert result["authentication"]["required"] is True
        assert result["authentication"]["type"] == "bearer"

    def test_no_match(self):
        result = JsAnalyzer().analyze_call_site('fetch("/other")', "/api/x")
        assert result["method"] is None
        assert result["query_parameters"] == {}


class TestHtmlAnalyzer:
    """Test phan tich HTML."""

    def test_csrf_meta(self):
        html = '<meta name="csrf-token" content="abc123">'
        result = HtmlAnalyzer().analyze(html)
        assert result["csrf_required"] is True
        assert "abc123" in result["csrf_token_source"]

    def test_form_action(self):
        html = '<form action="/api/login" method="post">'
        result = HtmlAnalyzer().analyze(html)
        assert result["form_actions"][0]["action"] == "/api/login"
        assert result["form_actions"][0]["method"] == "POST"

    def test_cookie_js(self):
        html = '<script>document.cookie = "session=abc"</script>'
        result = HtmlAnalyzer().analyze(html)
        assert "session=abc" in result["cookie_hints"]


class TestRequestSequence:
    """Test request sequence."""

    def test_sequence_order(self):
        js = 'fetch("/auth/token"); axios.get("/api/data"); fetch("/api/other")'
        result = RequestSequenceAnalyzer().analyze(js, "/api/data")
        urls = [r["url"] for r in result]
        assert "/auth/token" in urls
        assert "/api/data" in urls
        # Target phai co purpose=target
        target = [r for r in result if r["url"] == "/api/data"][0]
        assert target["purpose"] == "target"
        # Thu tu giu nguyen
        assert urls.index("/auth/token") < urls.index("/api/data")

    def test_no_target(self):
        result = RequestSequenceAnalyzer().analyze('fetch("/x")', "/api/nope")
        assert result == []


class TestEngine:
    """Test engine (mock HTTP)."""

    def test_build_profile_static(self, tmp_path):
        """Profile tu JS analysis (khong probe dynamic)."""
        engine = ReverserEngine()
        engine.output_dir = tmp_path / "output"
        engine.output_dir.mkdir(parents=True)

        bundle = 'axios.post("/api/orders", {symbol: "FPT", qty: 100})'
        with patch.object(engine, "_fetch_text", return_value=bundle):
            profile = engine._build_profile(
                "/api/orders",
                {"url": "/api/orders", "source": "https://x.com/bundle.js",
                 "evidence": "axios.post", "dynamic": False},
                "https://x.com/", {},
            )

        assert profile["method"] == "POST"
        assert profile["body_schema"]["symbol"]["type"] == "string"
        # Khong co confidence field
        assert "confidence" not in profile

    def test_profile_no_confidence_field(self, tmp_path):
        """Tuyet doi khong co confidence."""
        engine = ReverserEngine()
        engine.output_dir = tmp_path / "output"
        engine.output_dir.mkdir(parents=True)
        with patch.object(engine, "_fetch_text", return_value='fetch("/api/x")'):
            profile = engine._build_profile(
                "/api/x",
                {"url": "/api/x", "source": "https://x.com/b.js", "dynamic": False},
                "https://x.com/", {},
            )
        assert "confidence" not in profile

    def test_probe_401_marks_auth(self, tmp_path):
        """Probe 401 -> authentication.required=true."""
        engine = ReverserEngine()
        engine.output_dir = tmp_path / "output"
        engine.output_dir.mkdir(parents=True)
        engine._bundle_cache["https://x.com/b.js"] = ""

        with patch.object(engine.probe, "probe", return_value={
            "status": 401, "content_type": "text/html",
            "body_sample": "unauthorized", "truncated": False, "headers": {},
        }):
            profile = engine._build_profile(
                "/api/private",
                {"url": "/api/private", "source": "https://x.com/b.js",
                 "evidence": "fetch", "dynamic": False},
                "https://x.com/", {},
            )
        assert profile["authentication"]["required"] is True
        assert profile["sample_response"]["status"] == 401

    def test_dynamic_not_probed(self, tmp_path):
        """Dynamic url khong probe (khong GET)."""
        engine = ReverserEngine()
        engine.output_dir = tmp_path / "output"
        engine.output_dir.mkdir(parents=True)
        with patch.object(engine, "_fetch_text", return_value=""):
            with patch.object(engine.probe, "probe") as mock_probe:
                profile = engine._build_profile(
                    "/api/quote/${symbol}",
                    {"url": "/api/quote/${symbol}", "source": "u", "dynamic": True},
                    "https://x.com/", {},
                )
        mock_probe.assert_not_called()
        assert profile["sample_response"] is None

    def test_run_excludes_unsupported(self, tmp_path):
        """Endpoint unsupported (capability) khong reverse engineer."""
        engine = ReverserEngine()
        out = tmp_path / "output"
        out.mkdir(parents=True)
        (out / "enhanced_discovery_report.json").write_text(json.dumps({
            "sources": {"hose": {"endpoint_candidates": [
                {"url": "/api/old", "dynamic": False, "source": "u"},
                {"url": "/api/new", "dynamic": False, "source": "u"},
            ]}}
        }), encoding="utf-8")
        (out / "capability_report.json").write_text(json.dumps({
            "hose": {"stock_list": {"status": "unsupported",
                                     "evidence": {"url": "/api/old"}}},
        }), encoding="utf-8")
        engine.output_dir = out

        # Mock sources + fetch text
        fake_sources = [type("S", (), {"name": "HOSE", "enabled": True,
                                       "base_url": "https://example.com/",
                                       "timeout": 5, "retry": 1})()]
        with patch("reverser.engine.load_sources", return_value=fake_sources):
            with patch.object(engine, "_fetch_text", return_value=""):
                report = engine.run()

        urls = [p["url"] for p in report["sources"]["hose"]["profiles"]]
        assert "/api/new" in urls
        assert "/api/old" not in urls  # unsupported -> excluded

    def test_save_report(self, tmp_path):
        engine = ReverserEngine()
        report = {"sources": {"hose": {"profiles": []}}, "generated_at": "x"}
        saved = engine.save_report(report, tmp_path / "endpoint_profiles.json")
        assert Path(saved).exists()
        data = json.loads(Path(saved).read_text(encoding="utf-8"))
        assert data["sources"]["hose"]["profiles"] == []
