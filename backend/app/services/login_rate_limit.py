import hashlib

import redis

from app.core.config import get_settings


def _key(username, ip_address):
    digest = hashlib.sha256(f"{username.casefold()}|{ip_address or 'unknown'}".encode()).hexdigest()
    return f"autofish:login:{digest}"


def _client():
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=1, decode_responses=True)


def login_blocked(username, ip_address):
    try:
        count = int(_client().get(_key(username, ip_address)) or 0)
        return count >= get_settings().login_max_attempts
    except redis.RedisError:
        return False


def record_login_failure(username, ip_address):
    try:
        client = _client()
        key = _key(username, ip_address)
        count = client.incr(key)
        if count == 1:
            client.expire(key, get_settings().login_window_seconds)
    except redis.RedisError:
        return


def clear_login_failures(username, ip_address):
    try:
        _client().delete(_key(username, ip_address))
    except redis.RedisError:
        return
