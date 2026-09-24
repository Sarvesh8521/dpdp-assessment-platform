"""Celery application configuration."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "dpdp_scanner",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
)

# Configure Celery Beat schedule
celery_app.conf.beat_schedule = {
    "daily-retention-purge": {
        "task": "run_daily_retention_purge",
        "schedule": 86400.0, # Run daily (every 24 hours). Using a simple interval for testing/consistency, or can use crontab.
    },
}
