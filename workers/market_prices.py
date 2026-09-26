import asyncio

from app.core.database import SessionLocal
from app.services.market_prices import collect
from app.worker import celery_app


@celery_app.task(
    name="workers.collect_market_prices",
    max_retries=0,
    soft_time_limit=840,
    time_limit=900,
)
def collect_market_prices(retry=False):
    with SessionLocal() as db:
        return asyncio.run(collect(db, retry=retry))
