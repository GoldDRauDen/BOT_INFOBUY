"""
Scheduler - Quyet dinh task nao chay, task nao skip.
Task 14: Khong sua logic task. Chi quyet dinh run/skip/failed
dua tren checksum + config + state lan chay truoc.
Khong HTTP. Khong AI. Deterministic.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from .state_store import StateStore

try:
    from ..utils.config_loader import load_settings
except ImportError:
    from utils.config_loader import load_settings


class Scheduler:
    """Quyet dinh RUN/SKIP/FAILED cho tung task."""

    # Toan bo task theo thu tu pipeline
    TASKS = [
        "connectivity", "discovery", "enhancer", "capability", "reverser",
        "crawler", "url_selector", "fetcher", "schema_validator",
        "quality_gate", "master_report", "extraction",
    ]
    # Task luon chay (re, offline, can cap nhat)
    ALWAYS_RUN = {"connectivity", "discovery", "master_report"}

    def __init__(self, logger: logging.Logger = None, state_store: StateStore = None,
                 config: Dict[str, Any] = None):
        self.logger = logger or logging.getLogger("scheduler")
        self.state_store = state_store or StateStore(logger=self.logger)

        # Config scheduler tu settings.yaml (hoac truyen truc tiep)
        if config is None:
            try:
                settings = load_settings()
                config = settings.get("scheduler", {})
            except Exception:
                config = {}
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.interval_minutes = int(self.config.get("interval_minutes", 60))
        self.full_scan_every = int(self.config.get("full_scan_every", 1440))
        self.force_refresh = bool(self.config.get("force_refresh", False))

    # ---------- Helpers ----------

    def _checksum_unchanged(self, report_name: str, state: Dict[str, Any],
                            current_checksums: Dict[str, Any]) -> bool:
        """Checksum report co giong lan chay truoc khong."""
        if not state:
            return False  # lan dau chay -> luon chay
        old = state.get("checksums", {}).get(report_name)
        new = current_checksums.get(report_name)
        if old is None or new is None:
            return False  # thieu checksum -> chay lai de an toan
        return old == new

    def _full_scan_due(self, state: Dict[str, Any]) -> bool:
        """Da den luc full scan (full_scan_every phut)?."""
        if self.full_scan_every <= 0:
            return False
        last_run = state.get("last_run")
        if not last_run:
            return False
        try:
            last_dt = datetime.fromisoformat(last_run)
        except (ValueError, TypeError):
            return False
        due = last_dt + timedelta(minutes=self.full_scan_every)
        return datetime.now() >= due

    # ---------- Decide ----------

    def decide(self) -> Dict[str, str]:
        """Quyet dinh trang thai cho tung task."""
        state = self.state_store.load_state()
        checksums = self.state_store.compute_all_checksums()

        decisions = {task: "run" for task in self.TASKS}

        if not self.enabled:
            return decisions  # scheduler tat -> tat ca run

        force = self.force_refresh
        full_scan = self._full_scan_due(state)
        if full_scan:
            self.logger.info(f"Den luc full scan (sau {self.full_scan_every} phut) - chay toan bo")

        # Skip rules theo dependency chain
        # discovery khong doi -> skip enhancer + capability + crawler
        discovery_same = (self._checksum_unchanged("discovery_report.json", state, checksums)
                          and not force and not full_scan)
        if discovery_same:
            decisions["enhancer"] = "skip"
            decisions["capability"] = "skip"
            decisions["crawler"] = "skip"

        # capability khong doi (va enhanced khong doi) -> skip url_selector
        enhanced_same = (self._checksum_unchanged("enhanced_discovery_report.json", state, checksums)
                         and not force and not full_scan)
        capability_same = (self._checksum_unchanged("capability_report.json", state, checksums)
                           and not force and not full_scan)
        if capability_same and enhanced_same:
            decisions["url_selector"] = "skip"

        # endpoint_plan khong doi (va profiles khong doi) -> skip fetcher + downstream
        profiles_same = (self._checksum_unchanged("endpoint_profiles.json", state, checksums)
                         and not force and not full_scan)
        plan_same = (self._checksum_unchanged("endpoint_plan.json", state, checksums)
                     and not force and not full_scan)
        if plan_same and profiles_same:
            decisions["fetcher"] = "skip"
            decisions["schema_validator"] = "skip"
            decisions["quality_gate"] = "skip"
            decisions["extraction"] = "skip"

        # Neu fetcher chay lai -> downstream chay lai
        if decisions["fetcher"] == "run":
            decisions["schema_validator"] = "run"
            decisions["quality_gate"] = "run"
            decisions["extraction"] = "run"

        # Task luon chay
        for task in self.ALWAYS_RUN:
            decisions[task] = "run"

        return decisions

    # ---------- Save ----------

    def save_state(self, decisions: Dict[str, str]) -> str:
        """Ghi state sau khi chay pipeline."""
        checksums = self.state_store.compute_all_checksums()
        state = {
            "last_run": datetime.now().isoformat(),
            "tasks": decisions,
            "checksums": checksums,
            "config_used": {
                "enabled": self.enabled,
                "interval_minutes": self.interval_minutes,
                "full_scan_every": self.full_scan_every,
                "force_refresh": self.force_refresh,
            },
        }
        return self.state_store.save_state(state)
