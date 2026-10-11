"""Изоляция параллельных прогонов (ТЗ D1).

Проверки точные: мутация `index` → `index + 1` или потеря остатка по модулю
обязана падать здесь, иначе два воркера молча сядут в одну БД Redis и кейсы
начнут падать не из-за кода бота.
"""

from __future__ import annotations

import pytest

from botkit_farm.runner import parallel

# ── worker_index ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "index"),
    [("gw0", 0), ("gw1", 1), ("gw7", 7), ("gw12", 12), ("", 0), ("master", 0)],
)
def test_индекс_воркера_читается_из_окружения(name: str, index: int) -> None:
    assert parallel.worker_index({"PYTEST_XDIST_WORKER": name}) == index


def test_вне_xdist_индекс_нулевой() -> None:
    assert parallel.worker_index({}) == 0


# ── redis_url ───────────────────────────────────────────────────────────────


def test_номер_бд_заменяется_на_индекс_воркера() -> None:
    assert parallel.redis_url("redis://127.0.0.1:6380/0", index=3) == "redis://127.0.0.1:6380/3"


def test_номер_бд_старой_базы_не_важен_индекс_перекрывает() -> None:
    # База из исходного URL — это лишь точка отсчёта, воркер задаёт свою.
    assert parallel.redis_url("redis://127.0.0.1:6380/9", index=1) == "redis://127.0.0.1:6380/1"


def test_после_15_идёт_второй_круг_бд() -> None:
    # Воркеров может быть больше, чем логических БД (16): берём остаток.
    assert parallel.redis_url("redis://127.0.0.1:6380/0", index=16) == "redis://127.0.0.1:6380/0"
    assert parallel.redis_url("redis://127.0.0.1:6380/0", index=17) == "redis://127.0.0.1:6380/1"


def test_host_и_порт_сохраняются() -> None:
    assert parallel.redis_url("redis://redis-host:6399/2", index=5) == ("redis://redis-host:6399/5")


def test_база_берётся_из_переменной_окружения(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOTKIT_FARM_REDIS", "redis://127.0.0.1:7000/4")
    assert parallel.redis_url(index=2) == "redis://127.0.0.1:7000/2"


def test_без_переменной_окружения_берётся_значение_по_умолчанию(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BOTKIT_FARM_REDIS", raising=False)
    assert parallel.redis_url(index=0) == parallel.DEFAULT_REDIS


def test_индекс_по_умолчанию_берётся_из_воркера(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw5")
    assert parallel.redis_url("redis://127.0.0.1:6380/0") == "redis://127.0.0.1:6380/5"
