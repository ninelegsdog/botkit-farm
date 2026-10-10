"""Загрузка кейсов из YAML: файлы → валидированные объекты.

Отдельный модуль, потому что у загрузки две обязанности, которые нельзя смешивать
с исполнением: превратить произвольный текст в объект Case и отказать в файле,
который не должен попасть в прогон. Второе — про безопасность: кейс читает
файлы с диска, и путь в нём не должен уводить за пределы проекта.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
from pydantic import ValidationError

from botkit_farm.case.model import Case, find_placeholders, find_slots


class CaseError(Exception):
    """Кейс не прошёл загрузку.

    Сообщение — то, что увидит человек: номер строки и понятная причина, а не
    стек pydantic.
    """


def _where(path: pathlib.Path, error: ValidationError) -> str:
    """Первая ошибка в человекочитаемом виде: файл, поле, причина."""
    first = error.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "<корень>"
    return f"{path}: поле {loc}: {first['msg']}"


def load_file(path: pathlib.Path) -> Case:
    """Прочитать один YAML-файл кейса.

    Бросает CaseError, а не ValidationError/YAMLError: вызывающий код должен
    ловить одно исключение и печатать понятное сообщение, а не разбирать, откуда
    оно пришло — из YAML или из схемы.
    """
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaseError(f"{path}: невалидный YAML: {exc}") from exc
    except OSError as exc:
        raise CaseError(f"{path}: не читается: {exc}") from exc

    if not isinstance(raw, dict):
        raise CaseError(f"{path}: кейс должен быть словарём, получено {type(raw).__name__}")

    try:
        case = Case.model_validate(raw)
    except ValidationError as exc:
        raise CaseError(_where(path, exc)) from exc

    # Слоты и заглушки ловим здесь, до запуска бота: незаполненный кейс обязан
    # падать на чтении, иначе он снесёт состояние (базу, FSM) и лишь затем
    # покажет, что проверять нечего.
    body = path.read_text(encoding="utf-8")
    slots = find_slots(body)
    if slots:
        raise CaseError(f"{path}: незаполненные слоты {sorted(set(slots))}")
    stubs = find_placeholders(body)
    if stubs:
        raise CaseError(f"{path}: найдены слова-заглушки {sorted(set(stubs))} — кейс не дописан")
    return case


def load_dir(root: pathlib.Path) -> tuple[list[Case], list[str]]:
    """Загрузить все кейсы из каталога рекурсивно.

    Возвращает (кейсы, ошибки), а не падает целиком: один битый файл не должен
    лишать возможности прогнать остальные — но и не должен молчать, поэтому
    ошибки возвращаются вызывающему для показа.
    """
    if not root.exists():
        return [], [f"каталог кейсов не найден: {root}"]
    cases: list[Case] = []
    errors: list[str] = []
    for path in sorted(root.rglob("*.y*ml")):
        try:
            cases.append(load_file(path))
        except CaseError as exc:
            errors.append(str(exc))
    return cases, errors


__all__ = ["CaseError", "load_dir", "load_file"]
