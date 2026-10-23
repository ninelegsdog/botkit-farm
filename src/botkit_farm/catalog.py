"""Каталог регрессов: манифест и проверка целостности (ТЗ C5).

Каталог — это корпус `cases/` с кейсами (включая регрессы против публичных
ботов) и манифестом `catalog.json`. Зачем отдельный манифест, а не «просто
папка с кейсами»: он
хранит обещание — какие регрессы обязаны существовать. Без него удаление кейса
прошло бы незамеченным, а регресс, который удалили, хуже отсутствующего: о нём
думают, что он есть.

Проверка сверяет манифест и файлы в обе стороны:

* кейс из манифеста обязан существовать — иначе «регресс пропал»;
* каждый YAML-кейс в каталоге обязан быть в манифесте — иначе «кейс вне каталога»;
* `id` уникальны и совпадают с `id` внутри файла;
* сам кейс валиден по схеме (иначе он не регресс, а мёртвый файл).

Манифест намеренно JSON, а не YAML: загрузчик кейсов обходит `*.y*ml`, и
`catalog.yaml` попал бы в прогон как «битый кейс». Формат без YAML-расширения
держит манифест в стороне от кейсов без специальных исключений в загрузчике.
"""

from __future__ import annotations

import json
import pathlib

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from botkit_farm.case.loader import CaseError, load_file

MANIFEST = "catalog.json"


class CatalogError(Exception):
    """Манифест каталога не читается или не соответствует схеме."""


class _Strict(BaseModel):
    """Лишнее поле манифеста — ошибка: иначе опечатка в ключе молчит."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Entry(_Strict):
    """Один регресс каталога.

    `why` — зачем кейс существует: ссылка на issue, на дефект из [[опыт/lessons]]
    или на покрываемый путь. Пустая строка недопустима: регресс без причины — это
    кейс, который завтра удалят как «никому не нужный».
    """

    id: str = Field(min_length=1, max_length=64)
    case: str = Field(min_length=1, max_length=256)
    why: str = Field(min_length=1, max_length=300)


class Catalog(_Strict):
    """Манифест каталога регрессов."""

    schema_version: int = Field(ge=1)
    regressions: list[Entry] = Field(min_length=1)


def _where(error: ValidationError) -> str:
    """Первая ошибка схемы манифеста в человекочитаемом виде."""
    first = error.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "<корень>"
    return f"поле {loc}: {first['msg']}"


def load(catalog_dir: pathlib.Path) -> Catalog:
    """Прочитать манифест каталога.

    Бросает CatalogError с понятным сообщением: вызывающий (CLI) печатает его
    как есть, а не разбирает, откуда пришла ошибка — из файла или из схемы.
    """
    manifest = catalog_dir / MANIFEST
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"нет манифеста каталога: {manifest}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{manifest}: невалидный JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"{manifest}: манифест должен быть объектом, не {type(raw).__name__}")
    try:
        return Catalog.model_validate(raw)
    except ValidationError as exc:
        raise CatalogError(f"{manifest}: {_where(exc)}") from exc


def check(catalog_dir: pathlib.Path) -> list[str]:
    """Сверить манифест с кейсами на диске. Пустой список — каталог цел.

    Возвращает список проблем, а не падает на первой: человеку нужен весь список,
    чтобы починить каталог за один проход.
    """
    try:
        catalog = load(catalog_dir)
    except CatalogError as exc:
        return [str(exc)]

    problems: list[str] = []
    seen: set[str] = set()
    listed: set[str] = set()

    for entry in catalog.regressions:
        if entry.id in seen:
            problems.append(f"{MANIFEST}: id регресса повторяется: {entry.id}")
        seen.add(entry.id)
        listed.add(entry.case)

        path = catalog_dir / entry.case
        try:
            case = load_file(path)
        except CaseError as exc:
            if not path.exists():
                problems.append(f"кейс регресса пропал: {entry.case} (манифест его требует)")
            else:
                problems.append(str(exc))
            continue
        if case.id != entry.id:
            problems.append(f"{path}: id кейса {case.id!r} не совпадает с манифестом {entry.id!r}")

    for path in sorted(catalog_dir.glob("*.y*ml")):
        if path.name not in listed:
            problems.append(f"кейс не в каталоге: {path.name}")

    return problems


__all__ = ["MANIFEST", "Catalog", "CatalogError", "Entry", "check", "load"]
