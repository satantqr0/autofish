from app.core.config import Settings
from app.core.database import engine_options


def test_postgres_engine_has_bounded_pool_and_server_timeouts():
    settings = Settings(
        database_url="postgresql+psycopg://user:password@database:5432/autofish",
        database_pool_size=7,
        database_max_overflow=3,
        database_statement_timeout_ms=90000,
        database_lock_timeout_ms=4000,
        database_idle_transaction_timeout_ms=45000,
    )

    options = engine_options(settings)

    assert options["pool_size"] == 7
    assert options["max_overflow"] == 3
    server_options = options["connect_args"]["options"]
    assert "statement_timeout=90000" in server_options
    assert "lock_timeout=4000" in server_options
    assert "idle_in_transaction_session_timeout=45000" in server_options


def test_sqlite_engine_does_not_receive_postgres_connection_options():
    options = engine_options(Settings(database_url="sqlite://"))

    assert "connect_args" not in options
