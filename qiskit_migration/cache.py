"""Best-effort result cache keyed by code + target version.

Migrations are deterministic-ish (temperature 0) and expensive, so identical
submissions can return a cached result. Cache failures never break a job — every
backend degrades to a miss.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

from qiskit_migration.config import get_settings

logger = logging.getLogger(__name__)


def cache_key(code: str, target_version: str) -> str:
    digest = hashlib.sha256(f"{target_version}\n{code}".encode()).hexdigest()
    return f"migration:result:{digest}"


class ResultCache(Protocol):
    def get(self, code: str, target_version: str) -> str | None: ...
    def set(self, code: str, target_version: str, value: str) -> None: ...


class NoOpCache:
    def get(self, code: str, target_version: str) -> str | None:
        return None

    def set(self, code: str, target_version: str, value: str) -> None:
        return None


class InMemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, code: str, target_version: str) -> str | None:
        return self._store.get(cache_key(code, target_version))

    def set(self, code: str, target_version: str, value: str) -> None:
        self._store[cache_key(code, target_version)] = value


class RedisCache:
    def __init__(self, redis_url: str | None = None, ttl_s: int | None = None) -> None:
        import redis

        settings = get_settings()
        self._redis = redis.from_url(redis_url or settings.redis_url)
        self._ttl = ttl_s or settings.cache_ttl_s

    def get(self, code: str, target_version: str) -> str | None:
        try:
            value = self._redis.get(cache_key(code, target_version))
            return value.decode() if value else None
        except Exception as e:  # noqa: BLE001 - cache is best-effort
            logger.warning("cache get failed: %s", e)
            return None

    def set(self, code: str, target_version: str, value: str) -> None:
        try:
            self._redis.set(cache_key(code, target_version), value, ex=self._ttl)
        except Exception as e:  # noqa: BLE001 - cache is best-effort
            logger.warning("cache set failed: %s", e)


def get_result_cache() -> ResultCache:
    settings = get_settings()
    if not settings.cache_enabled:
        return NoOpCache()
    try:
        return RedisCache()
    except Exception as e:  # noqa: BLE001 - never let cache init break the worker
        logger.warning("Redis cache unavailable, using no-op cache: %s", e)
        return NoOpCache()
