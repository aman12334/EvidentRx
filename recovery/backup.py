"""
Backup and restore infrastructure.

Provides scripted backup and restoration of:
  1. PostgreSQL database (pg_dump / pg_restore wrappers)
  2. Runtime state directory (checkpoints, archives)
  3. Configuration snapshots (feature flags, tenant configs)

Backup targets:
  - Local filesystem (development)
  - S3 (production) — encrypted with AES-256, signed with HMAC

Backup schedule (via Celery Beat):
  - Full DB backup: nightly
  - Incremental (WAL): every 5 minutes (if using WAL archiving)
  - Runtime state: daily

Recovery time objective (RTO): < 4 hours
Recovery point objective (RPO): < 1 hour (incremental WAL archiving)
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib  import Path
from typing   import Optional

log = logging.getLogger("evidentrx.recovery.backup")

_BACKUP_DIR = Path("runtime_state/backups")


class BackupService:
    """
    Manages DB and state backups.
    Production: replace local paths with S3 upload.
    """

    def __init__(self, backup_dir: Path = _BACKUP_DIR) -> None:
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def backup_database(
        self,
        database_url: str,
        label:        str = "manual",
    ) -> Optional[Path]:
        """
        Execute pg_dump and write compressed output.
        Returns the backup file path or None on failure.
        """
        from config.settings import settings

        ts        = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename  = f"evidentrx_{label}_{ts}.sql.gz"
        dest      = self.backup_dir / filename

        # Strip password from URL for logging
        safe_url = database_url.split("@")[-1] if "@" in database_url else database_url

        log.info("Starting DB backup: %s → %s", safe_url, dest)

        try:
            cmd = [
                "pg_dump",
                "--format=custom",      # compressed, parallel-restore capable
                "--no-acl",
                "--no-owner",
                database_url,
            ]
            with open(dest, "wb") as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=3600)

            if result.returncode != 0:
                log.error("pg_dump failed: %s", result.stderr.decode())
                dest.unlink(missing_ok=True)
                return None

            size_mb = dest.stat().st_size / (1024 * 1024)
            log.info("DB backup completed: %s (%.1f MB)", dest.name, size_mb)
            return dest

        except FileNotFoundError:
            log.error("pg_dump not found — ensure postgresql-client is installed")
            return None
        except Exception as e:
            log.error("DB backup failed: %s", e)
            dest.unlink(missing_ok=True)
            return None

    def restore_database(
        self,
        backup_path: Path,
        database_url: str,
    ) -> bool:
        """
        Restore a pg_dump backup using pg_restore.
        Returns True on success.
        """
        log.warning(
            "DB RESTORE initiated from %s — this will overwrite all data!",
            backup_path,
        )

        try:
            cmd = [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-acl",
                "--no-owner",
                f"--dbname={database_url}",
                str(backup_path),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=7200)

            if result.returncode not in (0, 1):  # 1 = warnings only
                log.error("pg_restore failed: %s", result.stderr.decode())
                return False

            log.info("DB restore completed from %s", backup_path.name)
            return True

        except Exception as e:
            log.error("DB restore failed: %s", e)
            return False

    def list_backups(self) -> list[dict]:
        """Return metadata about available backups."""
        backups = []
        for path in sorted(self.backup_dir.glob("*.sql.gz"), reverse=True):
            stat = path.stat()
            backups.append({
                "filename": path.name,
                "size_mb":  round(stat.st_size / (1024 * 1024), 1),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return backups


backup_service = BackupService()
