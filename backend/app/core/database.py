from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()


def engine_options(runtime_settings):
    options = {
        "pool_pre_ping": True,
        "future": True,
    }
    if runtime_settings.database_url.startswith("postgresql"):
        options.update(
            pool_size=runtime_settings.database_pool_size,
            max_overflow=runtime_settings.database_max_overflow,
            pool_timeout=runtime_settings.database_pool_timeout_seconds,
            pool_recycle=runtime_settings.database_pool_recycle_seconds,
        )
        statement_timeout = runtime_settings.database_statement_timeout_ms
        lock_timeout = runtime_settings.database_lock_timeout_ms
        idle_timeout = runtime_settings.database_idle_transaction_timeout_ms
        options["connect_args"] = {
            "options": (
                f"-c statement_timeout={statement_timeout} "
                f"-c lock_timeout={lock_timeout} "
                f"-c idle_in_transaction_session_timeout={idle_timeout}"
            )
        }
    return options


engine = create_engine(settings.database_url, **engine_options(settings))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
