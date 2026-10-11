"""Изоляция параллельных прогонов (ТЗ D1).

Под `pytest-xdist` тесты идут в нескольких процессах-воркерах (`gw0`, `gw1`, …)
одновременно. Кейсы на одном боте делят один Redis, и без изоляции состояние
одного прогона перетрёт состояние другого: бот вернёт уже пройденный экран, и
кейс упадёт не из-за своего кода. Поэтому каждый воркер получает **свою**
логическую БД Redis.

Вне xdist (обычный `pytest`, `botkit-farm run`) индекс воркера — 0, и адрес
Redis остаётся ровно тем, что задан `BOTKIT_FARM_REDIS` или значением по
умолчанию.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

# Адрес Redis по умолчанию: не системный 6379, а отдельный 6380 — чтобы ферма
# не трогала чужой Redis на машине разработчика.
DEFAULT_REDIS = "redis://127.0.0.1:6380/0"

# `redis.conf` по умолчанию поднимает 16 логических БД (`databases 16`).
REDIS_DATABASES = 16


def worker_index(environ: Mapping[str, str] | None = None) -> int:
    """Номер текущего xdist-воркера; вне xdist — 0.

    pytest-xdist кладёт в `PYTEST_XDIST_WORKER` имя вида `gw0`, `gw7`. Цифры
    имени — это индекс. Отсутствие переменной означает не-parallel прогон.
    """
    env = os.environ if environ is None else environ
    name = env.get("PYTEST_XDIST_WORKER", "")
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else 0


def redis_url(
    base: str | None = None,
    *,
    index: int | None = None,
    databases: int = REDIS_DATABASES,
) -> str:
    """Redis-URL с номером БД, изолированным по воркеру.

    `base` по умолчанию берётся из `BOTKIT_FARM_REDIS`, иначе — `DEFAULT_REDIS`.
    Host и порт сохраняются, меняется только номер БД: `index % databases`.
    Взять остаток, а не сам индекс, нужно потому, что при `-n auto` воркеров
    может оказаться больше, чем логических БД в `redis.conf`.
    """
    if base is None:
        base = os.environ.get("BOTKIT_FARM_REDIS", DEFAULT_REDIS)
    if index is None:
        index = worker_index()
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, f"/{index % databases}", parts.query, ""))


__all__ = ["DEFAULT_REDIS", "REDIS_DATABASES", "redis_url", "worker_index"]
