from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("autofish", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    # AutomationJob enforces its own deadline (maximum 900 seconds).  The
    # Celery limits remain a final process-level guard with enough cleanup
    # headroom for the job state to be handed off safely.
    task_time_limit=960,
    task_soft_time_limit=930,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_transport_options={"visibility_timeout": 1200},
    result_expires=86400,
    imports=("workers.tasks", "workers.market_prices"),
    beat_schedule={
        "autofish-market-prices-twice-daily": {
            "task": "workers.collect_market_prices",
            "schedule": crontab(hour="9,21", minute=0),
            "options": {"expires": 1800},
        },
        "autofish-automation-sweep": {
            "task": "workers.schedule_automation",
            "schedule": 60.0,
        },
        "autofish-expired-job-recovery": {
            "task": "workers.recover_expired_automation_jobs",
            "schedule": 60.0,
        },
    },
)
