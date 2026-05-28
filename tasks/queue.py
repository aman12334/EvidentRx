"""
Celery task queue configuration.

Broker:  Redis (or in-memory for development)
Backend: Redis (or in-memory cache for development)

Queue routing:
  default      — general API-triggered tasks (low latency)
  monitoring   — scheduled monitoring pipeline runs (medium latency)
  agents       — LangGraph agent workflow runs (high latency, long timeout)
  archival     — investigation archival jobs (background, low priority)
  dead_letter  — failed tasks requiring human review

Worker scaling:
  - default queue:    2-8 workers (auto-scale on queue depth)
  - agents queue:     1-4 workers (concurrency limited by LLM rate limits)
  - monitoring queue: 1-2 workers (cron-like, scheduled)
"""

from __future__ import annotations

import logging

from config.settings import settings

log = logging.getLogger("evidentrx.tasks")

try:
    from celery import Celery
    from celery.utils.log import get_task_logger

    celery_app = Celery(
        "evidentrx",
        broker=settings.celery_broker,
        backend=settings.celery_backend,
        include=[
            "tasks.investigation",
        ],
    )

    celery_app.conf.update(
        # Serialization
        task_serializer      = "json",
        result_serializer    = "json",
        accept_content       = ["json"],

        # Timeouts
        task_soft_time_limit = 600,     # 10 min soft limit → SoftTimeLimitExceeded
        task_time_limit      = 900,     # 15 min hard kill
        task_acks_late       = True,    # ack after task completion (not on receipt)

        # Retry defaults
        task_max_retries            = settings.task_max_retries,
        task_default_retry_delay    = settings.task_retry_backoff,

        # Queue routing
        task_default_queue = "default",
        task_queues        = {
            "default":    {"exchange": "default",    "routing_key": "default"},
            "monitoring": {"exchange": "monitoring", "routing_key": "monitoring"},
            "agents":     {"exchange": "agents",     "routing_key": "agents"},
            "archival":   {"exchange": "archival",   "routing_key": "archival"},
            "dead_letter":{"exchange": "dead_letter","routing_key": "dead_letter"},
        },
        task_routes = {
            "tasks.investigation.*": {"queue": "agents"},
            "tasks.monitoring.*":    {"queue": "monitoring"},
        },

        # Result expiry
        result_expires = 86400,     # 24 hours

        # Visibility
        worker_send_task_events  = True,
        task_send_sent_event     = True,

        # Beat schedule (periodic tasks)
        beat_schedule = {
            "monitoring-every-5h": {
                "task":     "tasks.investigation.run_monitoring_pipeline",
                "schedule": 18000,      # every 5 hours
            },
            "archive-sweep-daily": {
                "task":     "tasks.investigation.run_archive_sweep",
                "schedule": 86400,      # daily
            },
        },
    )

    log.info("Celery configured: broker=%s", settings.celery_broker)

except ImportError:
    log.warning("Celery not installed — async tasks disabled. Install celery[redis].")

    # Stub celery_app for import safety
    class _StubCelery:
        def task(self, *a, **kw):
            def decorator(fn):
                return fn
            return decorator

        def send_task(self, *a, **kw):
            log.warning("Task queue not available (Celery not installed)")

    celery_app = _StubCelery()  # type: ignore[assignment]
