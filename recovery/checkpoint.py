"""
Checkpoint restoration for interrupted workflow recovery.

When a LangGraph workflow is interrupted (worker crash, timeout, pod restart),
the checkpoint store allows resuming from the last saved state rather than
re-running the entire investigation from scratch.

Checkpoint lifecycle:
  1. Workflow node completes → state serialized to checkpoint store
  2. Worker crashes mid-workflow
  3. Recovery job detects stale "running" investigations
  4. Loads last checkpoint for the workflow
  5. Re-submits task with checkpoint_id → workflow resumes from that node

Checkpoint storage:
  - agents/persistence/checkpoints.py (LangGraph built-in persistence)
  - This module provides higher-level orchestration and admin API
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib  import Path
from typing   import Any, Dict, List, Optional

log = logging.getLogger("evidentrx.recovery.checkpoint")

_CHECKPOINT_DIR = Path("runtime_state/checkpoints")


class CheckpointRestorer:
    """
    Manages workflow checkpoint save and restore operations.
    """

    def __init__(self, checkpoint_dir: Path = _CHECKPOINT_DIR) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(
        self,
        workflow_id: str,
        case_id:     str,
        tenant_id:   str,
        node_name:   str,
        state:       Dict[str, Any],
    ) -> str:
        """
        Persist a workflow checkpoint.
        Returns checkpoint_id for later restoration.
        """
        import uuid
        checkpoint_id = str(uuid.uuid4())
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "workflow_id":   workflow_id,
            "case_id":       case_id,
            "tenant_id":     tenant_id,
            "node_name":     node_name,
            "state":         state,
            "saved_at":      datetime.now(tz=timezone.utc).isoformat(),
        }

        path = self.checkpoint_dir / f"{case_id}_{checkpoint_id}.json"
        path.write_text(json.dumps(checkpoint, default=str, indent=2))
        log.info(
            "Checkpoint saved: case=%s node=%s id=%s",
            case_id, node_name, checkpoint_id,
        )
        return checkpoint_id

    def restore_checkpoint(
        self,
        case_id:       str,
        checkpoint_id: str,
        tenant_id:     str,
    ) -> Optional[Dict[str, Any]]:
        """
        Load a saved checkpoint.
        Returns None if not found. Enforces tenant isolation.
        """
        path = self.checkpoint_dir / f"{case_id}_{checkpoint_id}.json"
        if not path.exists():
            log.warning("Checkpoint not found: case=%s id=%s", case_id, checkpoint_id)
            return None

        checkpoint = json.loads(path.read_text())

        # Tenant isolation check
        if checkpoint.get("tenant_id") != tenant_id:
            log.critical(
                "Cross-tenant checkpoint access: case=%s requester=%s owner=%s",
                case_id, tenant_id, checkpoint.get("tenant_id"),
            )
            return None

        log.info("Checkpoint restored: case=%s node=%s", case_id, checkpoint.get("node_name"))
        return checkpoint

    def list_checkpoints(self, case_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """Return all checkpoints for a case."""
        results = []
        for path in self.checkpoint_dir.glob(f"{case_id}_*.json"):
            try:
                cp = json.loads(path.read_text())
                if cp.get("tenant_id") == tenant_id:
                    results.append({
                        "checkpoint_id": cp["checkpoint_id"],
                        "node_name":     cp["node_name"],
                        "saved_at":      cp["saved_at"],
                    })
            except Exception:
                continue
        return sorted(results, key=lambda x: x["saved_at"])

    def delete_checkpoints(self, case_id: str, tenant_id: str) -> int:
        """
        Delete all checkpoints for a closed/resolved case.
        Returns count deleted.
        """
        count = 0
        for path in self.checkpoint_dir.glob(f"{case_id}_*.json"):
            try:
                cp = json.loads(path.read_text())
                if cp.get("tenant_id") == tenant_id:
                    path.unlink()
                    count += 1
            except Exception:
                continue
        log.info("Deleted %d checkpoints for case=%s", count, case_id)
        return count


checkpoint_restorer = CheckpointRestorer()
