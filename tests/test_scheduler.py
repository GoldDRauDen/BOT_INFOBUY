"""
Unit tests cho Scheduler & State Store (Task 14).
Offline, deterministic, khong network.
"""
import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scheduler.scheduler import Scheduler
from scheduler.state_store import StateStore


def _write_report(out_dir: Path, name: str, data=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = data if data is not None else {"hose": {"stock_list": {"status": "unknown"}}}
    with open(out_dir / name, "w", encoding="utf-8") as f:
        json.dump(payload, f)


class TestStateStore:
    """Test StateStore."""

    def test_load_missing_state(self, tmp_path):
        store = StateStore(base_dir=tmp_path)
        assert store.load_state() == {}

    def test_save_and_load(self, tmp_path):
        store = StateStore(base_dir=tmp_path)
        state = {"last_run": "2026-01-01", "tasks": {"connectivity": "run"}}
        path = store.save_state(state)
        assert Path(path).exists()
        assert store.load_state() == state

    def test_checksum(self, tmp_path):
        store = StateStore(base_dir=tmp_path)
        _write_report(tmp_path / "output", "discovery_report.json", {"a": 1})
        cs = store.compute_checksum("discovery_report.json")
        assert cs.startswith("sha256:")
        assert store.compute_checksum("khong_co.json") is None

    def test_checksum_changes_with_content(self, tmp_path):
        store = StateStore(base_dir=tmp_path)
        _write_report(tmp_path / "output", "capability_report.json", {"a": 1})
        cs1 = store.compute_checksum("capability_report.json")
        _write_report(tmp_path / "output", "capability_report.json", {"a": 2})
        cs2 = store.compute_checksum("capability_report.json")
        assert cs1 != cs2

    def test_archive_run(self, tmp_path):
        store = StateStore(base_dir=tmp_path)
        out = tmp_path / "output"
        for name in StateStore.ARCHIVE_FILES:
            _write_report(out, name)
        timestamp = store.archive_run()
        run_dir = tmp_path / "history" / timestamp
        assert run_dir.exists()
        for name in StateStore.ARCHIVE_FILES:
            assert (run_dir / name).exists()


class TestScheduler:
    """Test Scheduler.decide()."""

    def _make_scheduler(self, tmp_path, config=None):
        store = StateStore(base_dir=tmp_path)
        return Scheduler(state_store=store, config=config or {"enabled": True})

    def test_first_run_all_run(self, tmp_path):
        """Khong co state -> tat ca task run."""
        sched = self._make_scheduler(tmp_path)
        decisions = sched.decide()
        assert all(v == "run" for v in decisions.values())
        assert len(decisions) == 12  # 11 tasks + reverser (Task 16)

    def test_scheduler_disabled_all_run(self, tmp_path):
        sched = self._make_scheduler(tmp_path, {"enabled": False})
        decisions = sched.decide()
        assert all(v == "run" for v in decisions.values())

    def test_skip_chain_unchanged_checksums(self, tmp_path):
        """Checksums khong doi -> capability/crawler/selector/fetcher skip."""
        store = StateStore(base_dir=tmp_path)
        out = tmp_path / "output"
        for name in StateStore.CHECKSUM_FILES:
            _write_report(out, name)
        checksums = store.compute_all_checksums()
        # Ghi state lan chay truoc voi cung checksums (last_run gan day)
        store.save_state({
            "last_run": datetime.now().isoformat(),
            "tasks": {},
            "checksums": checksums,
        })
        sched = Scheduler(state_store=store, config={"enabled": True})
        decisions = sched.decide()

        assert decisions["capability"] == "skip"
        assert decisions["crawler"] == "skip"
        assert decisions["url_selector"] == "skip"
        assert decisions["fetcher"] == "skip"
        assert decisions["schema_validator"] == "skip"
        assert decisions["quality_gate"] == "skip"
        assert decisions["extraction"] == "skip"
        # Luon chay
        assert decisions["connectivity"] == "run"
        assert decisions["discovery"] == "run"
        assert decisions["master_report"] == "run"

    def test_discovery_changed_reruns_capability(self, tmp_path):
        """Discovery doi -> capability + crawler chay lai."""
        store = StateStore(base_dir=tmp_path)
        out = tmp_path / "output"
        # State cu: discovery checksum A
        _write_report(out, "discovery_report.json", {"v": 1})
        _write_report(out, "enhanced_discovery_report.json", {"v": 1})
        _write_report(out, "capability_report.json", {"v": 1})
        _write_report(out, "endpoint_plan.json", {"v": 1})
        old_checksums = store.compute_all_checksums()
        store.save_state({
            "last_run": datetime.now().isoformat(),
            "tasks": {},
            "checksums": old_checksums,
        })
        # Discovery doi
        _write_report(out, "discovery_report.json", {"v": 2})
        sched = Scheduler(state_store=store, config={"enabled": True})
        decisions = sched.decide()

        assert decisions["capability"] == "run"
        assert decisions["crawler"] == "run"
        # Enhanced chua co checksum -> enhancer chay lai
        assert decisions["enhancer"] == "run"
        # Capability chua chay -> url_selector skip (capability checksum cu)
        assert decisions["url_selector"] == "skip"

    def test_force_refresh_runs_all(self, tmp_path):
        """force_refresh=true -> tat ca chay."""
        store = StateStore(base_dir=tmp_path)
        out = tmp_path / "output"
        for name in StateStore.CHECKSUM_FILES:
            _write_report(out, name)
        checksums = store.compute_all_checksums()
        store.save_state({
            "last_run": datetime.now().isoformat(),
            "tasks": {},
            "checksums": checksums,
        })
        sched = Scheduler(state_store=store,
                          config={"enabled": True, "force_refresh": True})
        decisions = sched.decide()
        assert all(v == "run" for v in decisions.values())

    def test_full_scan_due(self, tmp_path):
        """full_scan_every nho -> den luc full scan -> tat ca chay."""
        store = StateStore(base_dir=tmp_path)
        out = tmp_path / "output"
        for name in StateStore.CHECKSUM_FILES:
            _write_report(out, name)
        checksums = store.compute_all_checksums()
        store.save_state({
            "last_run": "2020-01-01T00:00:00",  # rat cu
            "tasks": {},
            "checksums": checksums,
        })
        sched = Scheduler(state_store=store,
                          config={"enabled": True, "full_scan_every": 1})
        decisions = sched.decide()
        assert all(v == "run" for v in decisions.values())

    def test_save_state_schema(self, tmp_path):
        """save_state ghi day du schema."""
        store = StateStore(base_dir=tmp_path)
        out = tmp_path / "output"
        for name in StateStore.CHECKSUM_FILES:
            _write_report(out, name)
        sched = Scheduler(state_store=store, config={"enabled": True})
        decisions = sched.decide()
        path = sched.save_state(decisions)

        state = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "last_run" in state
        assert "tasks" in state
        assert "checksums" in state
        assert "config_used" in state
        assert set(state["tasks"].keys()) == set(Scheduler.TASKS)
        assert len(state["tasks"]) == 12
        assert set(state["checksums"].keys()) == {
            "discovery_report.json", "enhanced_discovery_report.json",
            "capability_report.json", "endpoint_plan.json",
            "endpoint_profiles.json"}
