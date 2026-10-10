"""Версионирование схемы кейса: цепочка миграций vN → vN+1.

Формат YAML — публичный контракт: кейсы живут в чужих репозиториях ([[паки/pack]]),
и изменение поля без версии сломало бы их молча. Поэтому версия хранится в самом
кейсе, отсутствие версии считается версией 1 (мягкая миграция), а **более новая**
версия — это отказ с номером, а не «попробуем как получится»: чужой кейс со
схемой 3 на ферме со схемой 1 должен получить понятную ошибку, а не странный
прогон.

Миграции — чистые функции: одна версия входит, следующая выходит. Цепочку можно
прогнать в тесте без диска, и именно поэтому удаление функции миграции обязано
ронять тест на старом кейсе.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Any

import yaml

# Текущая версия схемы кейса. Поднимается вместе с несовместимым изменением
# формата; для совместимого (новое необязательное поле) версия не меняется.
SCHEMA_VERSION = 1

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# MIGRATIONS[N] переводит документ версии N в версию N+1. Пока схема в первой
# версии, таблица пуста — механизм есть, а придумывать историю миграций заранее
# значит документировать то, чего не было.
MIGRATIONS: dict[int, Migration] = {}


class SchemaError(ValueError):
    """Версия схемы кейса непонятна ферме или цепочка миграций оборвана."""


def version_of(data: dict[str, Any]) -> int:
    """Версия схемы кейса. Отсутствие поля — версия 1."""
    raw = data.get("schema_version", 1)
    if isinstance(raw, bool):  # bool — подкласс int, но версией быть не может
        raise SchemaError(f"schema_version не может быть логическим: {raw!r}")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"schema_version должен быть числом, получено {raw!r}") from exc


def apply_migrations(
    data: dict[str, Any],
    *,
    target: int = SCHEMA_VERSION,
    migrations: dict[int, Migration] | None = None,
) -> dict[str, Any]:
    """Привести документ кейса к версии `target`.

    Таблица миграций — параметр, а не только глобальная константа: тест обязан
    уметь подставить свою цепочку и проверить, что без функции миграции старый
    кейс не переводится (критерий ПД).
    """
    table = MIGRATIONS if migrations is None else migrations
    version = version_of(data)
    if version > target:
        raise SchemaError(f"кейс со схемой {version}, ферма знает {target}")

    out = dict(data)
    while version < target:
        step = table.get(version)
        if step is None:
            raise SchemaError(f"нет миграции со схемы {version} на {version + 1}")
        out = step(out)
        version += 1
    out["schema_version"] = version
    return out


def needs_migration(data: dict[str, Any], *, target: int = SCHEMA_VERSION) -> bool:
    """Нужно ли трогать файл: версия не проставлена или не той версии.

    Отсутствие поля — это «ещё версия 1» по чтению, но на запись мы версию
    проставляем: кейс, лежащий в репозитории, должен называть свою схему сам.
    """
    return data.get("schema_version") is None or version_of(data) != target


def migrate_file(path: pathlib.Path, *, target: int = SCHEMA_VERSION) -> bool:
    """Переписать файл кейса к текущей схеме.

    Возвращает True, если файл изменён. Идемпотентно: файл с явно проставленной
    текущей версией не трогается, поэтому повторный запуск не меняет содержимое.
    """
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SchemaError(f"{path}: кейс должен быть словарём, получено {type(raw).__name__}")
    if not needs_migration(raw, target=target):
        return False
    migrated = apply_migrations(raw, target=target)
    path.write_text(yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True


__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "Migration",
    "SchemaError",
    "apply_migrations",
    "migrate_file",
    "needs_migration",
    "version_of",
]
