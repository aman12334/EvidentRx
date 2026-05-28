"""
Structured logging and runtime metrics for EvidentRx.

Provides:
  - JSON-structured log handler for machine-readable logs
  - Per-run log files in logs/{run_id}.jsonl
  - RuntimeMetrics: tracks token usage, latency, stage timings across a run
  - setup_structured_logging(): call once at process startup

Log format (one JSON object per line):
  {
    "ts": "2025-01-15T10:23:45.123Z",
    "level": "INFO",
    "logger": "rules_engine.engine",
    "run_id": "...",
    "stage": "rules_engine",
    "msg": "Evaluated 5000 records",
    "extra": { ... }
  }
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# JSON log formatter
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def __init__(self, run_id: str = "", stage: str = "") -> None:
        super().__init__()
        self._run_id = run_id
        self._stage  = stage

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if self._run_id:
            obj["run_id"] = self._run_id
        if self._stage:
            obj["stage"] = self._stage

        # Copy any extra fields attached by the caller
        for key in vars(record):
            if key not in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            ):
                val = getattr(record, key)
                if not callable(val):
                    obj.setdefault("extra", {})[key] = val

        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)

        return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

_LOG_DIR = Path(os.path.dirname(__file__)).parent / "logs"


def setup_structured_logging(
    run_id: str = "",
    stage: str  = "",
    level: str  = "INFO",
    log_dir: Optional[Path] = None,
    console: bool = True,
) -> Optional[Path]:
    """
    Configures the root logger with:
      - A JSON file handler writing to logs/{run_id}.jsonl
      - An optional human-readable console handler

    Returns the log file path (or None if no run_id given).
    Call once at process startup.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplication
    root.handlers.clear()

    formatter = _JsonFormatter(run_id=run_id, stage=stage)

    # File handler (JSON)
    log_file: Optional[Path] = None
    if run_id:
        ldir = log_dir or _LOG_DIR
        ldir.mkdir(parents=True, exist_ok=True)
        log_file = ldir / f"{run_id}.jsonl"
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # Console handler (plain text)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(ch)

    return log_file


# ---------------------------------------------------------------------------
# Runtime metrics
# ---------------------------------------------------------------------------

@dataclass
class StageMetrics:
    stage:       str
    started_at:  float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None
    status:      str  = "running"   # running | completed | failed | skipped
    stats:       dict = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> Optional[float]:
        if self.finished_at is not None:
            return round(self.finished_at - self.started_at, 3)
        return None

    def complete(self, stats: Optional[dict] = None) -> None:
        self.finished_at = time.monotonic()
        self.status      = "completed"
        if stats:
            self.stats.update(stats)

    def fail(self, error: str) -> None:
        self.finished_at   = time.monotonic()
        self.status        = "failed"
        self.stats["error"] = error

    def skip(self, reason: str = "") -> None:
        self.finished_at     = time.monotonic()
        self.status          = "skipped"
        self.stats["reason"] = reason

    def to_dict(self) -> dict:
        return {
            "stage":           self.stage,
            "status":          self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "stats":           self.stats,
        }


class RuntimeMetrics:
    """
    Tracks token usage, latency, and stage timings across an entire pipeline run.
    Thread-safe for sequential stage execution.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id        = run_id
        self.started_at    = time.monotonic()
        self._stages: dict[str, StageMetrics] = {}
        self._total_input_tokens  = 0
        self._total_output_tokens = 0
        self._total_cache_tokens  = 0
        self._agent_latencies_ms: list[float] = []

    def stage(self, name: str) -> StageMetrics:
        m = StageMetrics(stage=name)
        self._stages[name] = m
        return m

    def record_llm_call(
        self,
        input_tokens:  int,
        output_tokens: int,
        cache_tokens:  int = 0,
        latency_ms:    float = 0.0,
    ) -> None:
        self._total_input_tokens  += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cache_tokens  += cache_tokens
        if latency_ms > 0:
            self._agent_latencies_ms.append(latency_ms)

    def summary(self) -> dict:
        elapsed = round(time.monotonic() - self.started_at, 3)
        avg_lat = (
            round(sum(self._agent_latencies_ms) / len(self._agent_latencies_ms), 1)
            if self._agent_latencies_ms else None
        )
        return {
            "run_id":               self.run_id,
            "total_elapsed_s":      elapsed,
            "total_input_tokens":   self._total_input_tokens,
            "total_output_tokens":  self._total_output_tokens,
            "total_cache_tokens":   self._total_cache_tokens,
            "avg_agent_latency_ms": avg_lat,
            "stages": {k: v.to_dict() for k, v in self._stages.items()},
        }

    def print_summary(self) -> None:
        s = self.summary()
        print("\n" + "─" * 60)
        print(f"  Run metrics — {self.run_id[:8]}...")
        print("─" * 60)
        for stage_name, sd in s["stages"].items():
            status_icon = {"completed": "✓", "failed": "✗", "skipped": "○", "running": "…"}.get(
                sd["status"], "?"
            )
            elapsed = f"{sd['elapsed_seconds']:.1f}s" if sd["elapsed_seconds"] else "—"
            print(f"  {status_icon}  {stage_name:<22} {elapsed:>8}  {sd['status']}")
        print("─" * 60)
        print(f"  Total elapsed : {s['total_elapsed_s']:.1f}s")
        if s["total_input_tokens"]:
            print(f"  Tokens in     : {s['total_input_tokens']:,}")
            print(f"  Tokens out    : {s['total_output_tokens']:,}")
            print(f"  Cache hits    : {s['total_cache_tokens']:,}")
        print()
