from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("a-strong-test-password")
    second = hash_password("a-strong-test-password")

    assert first != second
    assert verify_password("a-strong-test-password", first)
    assert not verify_password("wrong-password", first)
    assert not verify_password("a-strong-test-password", "malformed")


def test_access_token_contains_session_version_for_revocation():
    token = create_access_token(7, "admin", session_version=3)
    payload = decode_access_token(token)

    assert payload["sub"] == "7"
    assert payload["sv"] == 3
