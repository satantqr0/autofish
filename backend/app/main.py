import logging
import uuid
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import (
    ai_settings,
    auth,
    automation,
    autonomous,
    clawhub,
    commercialization,
    dashboard,
    integrations,
    market,
    operations,
    products,
    sourcing,
    suppliers,
    tasks,
    xianyu,
)
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.services.seed import seed_database

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("autofish")


@asynccontextmanager
async def lifespan(_app):
    settings.assert_production_safe()
    db = SessionLocal()
    try:
        seed_database(db)
        logger.info("database seed check completed")
    finally:
        db.close()
    yield


app = FastAPI(
    title="AutoFish API",
    version="0.9.0",
    description=(
        "Productized operations with authorized adapters, controlled platform writes, "
        "and traceable browser snapshots."
    ),
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Correlation-ID",
        "X-AutoFish-Webhook-Token",
        "X-AutoFish-Bridge-Token",
        "X-AutoFish-Bridge-Id",
    ],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    origin = request.headers.get("Origin")
    browser_bridge_agent = request.url.path.startswith("/api/v1/xianyu/browser-bridge/agent/")
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and origin
        and origin not in settings.cors_origin_list
        and not browser_bridge_agent
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "origin not allowed", "correlation_id": correlation_id},
        )
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    logger.exception(
        "unhandled request error",
        extra={"correlation_id": getattr(request.state, "correlation_id", None)},
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "internal server error",
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "autofish-backend", "version": "0.9.0"}


@app.get("/ready", tags=["system"])
def ready():
    db = SessionLocal()
    cache = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
    try:
        db.execute(text("SELECT 1"))
        cache.ping()
        return {"status": "ready", "database": "ok", "redis": "ok"}
    finally:
        db.close()
        cache.close()


for router in [
    market.router,
    auth.router,
    ai_settings.router,
    autonomous.router,
    dashboard.router,
    products.router,
    suppliers.router,
    integrations.router,
    operations.router,
    sourcing.router,
    clawhub.router,
    commercialization.router,
    xianyu.router,
    automation.router,
    tasks.router,
]:
    app.include_router(router, prefix="/api/v1")
