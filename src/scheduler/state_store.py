"""
State Store - Quan ly trang thai pipeline + lich su chay.
Task 14: Doc/ghi state/pipeline_state.json, tinh checksum, luu history.
Khong HTTP. Khong AI. Khong sua logic task khac.
"""
import json
import hashlib
import logging
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional


class StateStore:
    """Doc/ghi state, tinh checksum, archive reports."""

    STATE_FILENAME = "pipeline_state.json"
    # Reports can archive moi lan chay
    ARCHIVE_FILES = [
        "connectivity_report.json",
        "discovery_report.json",
        "capability_report.json",
        "index_pages.json",
        "endpoint_plan.json",
        "quality_report.json",
        "final_report.json",
    ]
    # Reports dung de tinh checksum (quyet dinh skip)
    CHECKSUM_FILES = [
        "discovery_report.json",
        "enhanced_discovery_report.json",
        "capability_report.json",
        "endpoint_plan.json",
        "endpoint_profiles.json",
    ]

    def __init__(self, logger: logging.Logger = None, base_dir: Path = None):
        self.logger = logger or logging.getLogger("state_store")
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        self.base_dir = Path(base_dir)
        self.state_dir = self.base_dir / "state"
        self.history_dir = self.base_dir / "history"
        self.output_dir = self.base_dir / "output"

    # ---------- State ----------

    def load_state(self) -> Dict[str, Any]:
        """Doc state, tra {} neu thieu."""
        path = self.state_dir / self.STATE_FILENAME
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.warning(f"Loi doc state {path}: {e}")
            return {}

    def save_state(self, state: Dict[str, Any]) -> str:
        """Ghi state, tao thu muc neu can."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / self.STATE_FILENAME
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Da luu state: {path}")
        return str(path)

    # ---------- Checksum ----------

    def compute_checksum(self, filename: str) -> Optional[str]:
        """sha256 bytes tho cua report trong output/. None neu thieu."""
        path = self.output_dir / filename
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            return f"sha256:{digest}"
        except Exception as e:
            self.logger.warning(f"Loi tinh checksum {path}: {e}")
            return None

    def compute_all_checksums(self) -> Dict[str, Optional[str]]:
        """Checksum cua tat ca report quyet dinh skip."""
        return {name: self.compute_checksum(name) for name in self.CHECKSUM_FILES}

    # ---------- History ----------

    def archive_run(self) -> str:
        """Snapshot reports hien tai vao history/{timestamp}/. Tra timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_dir = self.history_dir / timestamp
        run_dir.mkdir(parents=True, exist_ok=True)
        archived = 0
        for name in self.ARCHIVE_FILES:
            src = self.output_dir / name
            if src.exists():
                shutil.copy2(src, run_dir / name)
                archived += 1
        # Luu state vao run history
        state_src = self.state_dir / self.STATE_FILENAME
        if state_src.exists():
            shutil.copy2(state_src, run_dir / state_src.name)
        self.logger.info(f"Da archive {archived} reports vao {run_dir}")
        return timestamp
