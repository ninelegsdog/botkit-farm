"""Проверки версионирования схемы кейса.

Схема — публичный контракт: кейсы живут в чужих репозиториях ([[паки/pack]]), и
изменение формата без версии сломало бы их молча. Здесь проверяется цепочка
миграций, отказ на более новой схеме и идемпотентность `migrate`.

Отдельно — критерий ПД: без функции миграции старый кейс **не** переводится, а
падает с номером версии. Это доказывает, что цепочка реально исполняется, а не
существует в виде комментария.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from botkit_farm.case.loader import CaseError, load_file
from botkit_farm.case.migration import (
    SCHEMA_VERSION,
    SchemaError,
    apply_migrations,
    migrate_file,
    needs_migration,
    version_of,
)


def _case_file(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "sample.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


VALID_CASE = (
    "id: sample-case\n"
    "title: Пример кейса\n"
    "source: ./bot\n"
    "steps:\n"
    '  - send: "/start"\n'
    "    expect:\n"
    '      text_contains: ["ok"]\n'
)


# ── версия ────────────────────────────────────────────────────────────────────


def test_отсутствие_версии_читается_как_первая():
    assert version_of({}) == 1


def test_строка_с_версией_приводится_к_числу():
    assert version_of({"schema_version": "1"}) == 1


def test_логическая_версия_отвергается():
    with pytest.raises(SchemaError, match="логическим"):
        version_of({"schema_version": True})


def test_нечисловая_версия_отвергается():
    with pytest.raises(SchemaError, match="должен быть числом"):
        version_of({"schema_version": "первая"})


# ── цепочка миграций ──────────────────────────────────────────────────────────


def test_будущая_схема_отвергается_с_номером():
    with pytest.raises(SchemaError, match="кейс со схемой 3, ферма знает 1"):
        apply_migrations({"schema_version": 3})


def test_цепочка_переводит_старый_кейс():
    """ПД: миграция существует и реально переводит документ на следующую версию."""

    def v1_to_v2(data: dict) -> dict:
        out = dict(data)
        out["added"] = True
        return out

    out = apply_migrations({"schema_version": 1}, target=2, migrations={1: v1_to_v2})
    assert out["schema_version"] == 2
    assert out["added"] is True


def test_без_функции_миграции_старый_кейс_не_переводится():
    """ПД наоборот: удалили функцию миграции — старый кейс падает с номером схемы,
    а не проходит как ни в чём не бывало."""
    with pytest.raises(SchemaError, match="нет миграции со схемы 1 на 2"):
        apply_migrations({"schema_version": 1}, target=2, migrations={})


def test_текущая_версия_проставляется_явно():
    out = apply_migrations({"id": "x"})
    assert out["schema_version"] == SCHEMA_VERSION


# ── migrate: запись на диск и идемпотентность ─────────────────────────────────


def test_migrate_проставляет_версию_и_идемпотентен(tmp_path):
    path = _case_file(tmp_path, VALID_CASE)
    assert needs_migration({"id": "x"}) is True

    assert migrate_file(path) is True, "версия не была проставлена — файл должен измениться"
    body = path.read_text(encoding="utf-8")
    assert f"schema_version: {SCHEMA_VERSION}" in body

    assert migrate_file(path) is False, "повторный запуск обязан ничего не делать"


def test_migrate_не_трогает_файл_текущей_версии(tmp_path):
    path = _case_file(tmp_path, "schema_version: 1\n" + VALID_CASE)
    before = path.read_text(encoding="utf-8")
    assert migrate_file(path) is False
    assert path.read_text(encoding="utf-8") == before


# ── загрузка кейса ────────────────────────────────────────────────────────────


def test_старый_кейс_без_версии_загружается(tmp_path):
    """Мягкая миграция: кейс без schema_version читается как версия 1."""
    case = load_file(_case_file(tmp_path, VALID_CASE))
    assert case.schema_version == SCHEMA_VERSION


def test_кейс_будущей_версии_отвергается_с_номером(tmp_path):
    path = _case_file(tmp_path, "schema_version: 9\n" + VALID_CASE)
    with pytest.raises(CaseError, match="схемой 9, ферма знает 1"):
        load_file(path)
