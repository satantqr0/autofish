import pytest

from app.core.config import Settings


def test_data_mode_tracks_demo_ready_and_read_only_live():
    assert Settings(seed_demo=True, adapters_enabled=False).data_mode == "DEMO"
    assert Settings(seed_demo=False, adapters_enabled=False).data_mode == "LIVE_READY"
    assert Settings(seed_demo=False, adapters_enabled=True).data_mode == "READ_ONLY_LIVE"


def test_production_configuration_rejects_placeholders_and_demo_mode():
    settings = Settings(environment="production", seed_demo=True)

    with pytest.raises(RuntimeError, match="AUTOFISH_JWT_SECRET") as raised:
        settings.assert_production_safe()

    message = str(raised.value)
    assert "AUTOFISH_ADMIN_PASSWORD" in message
    assert "AUTOFISH_DATABASE_URL" in message
    assert "AUTOFISH_SEED_DEMO" in message


def test_non_production_configuration_allows_local_defaults():
    settings = Settings(environment="development")

    settings.assert_production_safe()
    assert settings.xianyu_browser_customer_service_enabled is False
