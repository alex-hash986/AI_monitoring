from celery import Celery

from config import get_settings

settings = get_settings()

app = Celery(
    "competitor_monitoring",
    broker=str(settings.celery_broker_url),
    backend=str(settings.celery_result_backend),
    include=["src.tasks.monitoring"],
)
app.conf.update(
    broker_connection_retry_on_startup=True,
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.celery_worker_concurrency,
    beat_schedule={
        "monitor-competitors-every-30-minutes": {
            "task": "src.tasks.monitoring.run_full_monitoring",
            "schedule": 30 * 60,
            "options": {"expires": 30 * 60},
        },
    },
)
