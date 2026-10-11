"""Загрузка кейсов из YAML: файлы → валидированные объекты.

Отдельный модуль, потому что у загрузки две обязанности, которые нельзя смешивать
с исполнением: превратить произвольный текст в объект Case и отказать в файле,
который не должен попасть в прогон. Второе — про безопасность: кейс читает
файлы с диска, и путь в нём не должен уводить за пределы проекта.
"""

from __future__ import annotations

import pathlib
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from botkit_farm.case import template as tmpl
from botkit_farm.case.migration import SchemaError, apply_migrations
from botkit_farm.case.model import Case, find_placeholders

# Каталог пака → происхождение кейса. Словарь даёт и проверку входа, и
# типизированное значение: `path.parent.name` — обычная строка, а поле `origin`
# в модели — литерал.
_ORIGINS: dict[str, Literal["manual", "generated"]] = {
    "manual": "manual",
    "generated": "generated",
}


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

    # Схема — публичный контракт с чужим репозиторием: сначала приводим кейс к
    # текущей версии, и только потом валидируем. Кейс с более новой схемой обязан
    # отказать с номером версии ([[опыт/lessons]] про молчаливую потерю данных).
    try:
        raw = apply_migrations(raw)
    except SchemaError as exc:
        raise CaseError(f"{path}: {exc}") from exc

    try:
        case = Case.model_validate(raw)
    except ValidationError as exc:
        raise CaseError(_where(path, exc)) from exc

    # Происхождение кейса: если автор не указал явно, а файл лежит в каталоге
    # пака `cases/manual` или `cases/generated`, — берём его из расположения.
    # Так отчёт различает кейс человека и кейс агента, не требуя поля в YAML.
    origin = _ORIGINS.get(path.parent.name)
    if case.origin is None and origin is not None:
        case.origin = origin

    # Шаблоны и заглушки ловим здесь, до запуска бота: недопустимый кейс обязан
    # падать на чтении, иначе он снесёт состояние (базу, FSM) и лишь затем
    # покажет, что проверять нечего. Неизвестная переменная шаблона — отказ с
    # её именем (ТЗ C6), а не пустая строка в отчёте.
    _check_templates(case, path)
    body = path.read_text(encoding="utf-8")
    stubs = find_placeholders(body)
    if stubs:
        raise CaseError(f"{path}: найдены слова-заглушки {sorted(set(stubs))} — кейс не дописан")
    return case


def _check_templates(case: Case, path: pathlib.Path) -> None:
    """Проверить все шаблонные строки кейса до запуска бота.

    Действия видят только индекс повтора (`i`, `n`); ожидания — ещё и прошлые
    ответы (`turns`). Разные наборы переменных не случайны: `send` рендерится до
    прогона, когда ответов ещё нет, а `expect` — после, когда они есть.
    """
    for n, step in enumerate(case.steps, start=1):
        for field, text in (("send", step.send), ("click", step.click), ("pick", step.pick)):
            if text is not None:
                _check_one(text, tmpl.ACTION_VARS, f"{path}: шаг {n} {field}")
        for j, branch in enumerate(step.choice or [], start=1):
            for field, text in (
                ("send", branch.send),
                ("click", branch.click),
                ("pick", branch.pick),
            ):
                if text is not None:
                    _check_one(text, tmpl.ACTION_VARS, f"{path}: шаг {n} choice {j} {field}")
        exp = step.expect
        if exp is None:
            continue
        for field, value in (("text_equals", exp.text_equals),):
            if value is not None:
                _check_one(value, tmpl.EXPECT_VARS, f"{path}: шаг {n} expect.{field}")
        for field, values in (
            ("text_contains", exp.text_contains),
            ("buttons_contains", exp.buttons_contains),
            ("caption_contains", exp.caption_contains),
        ):
            for j, value in enumerate(values, start=1):
                _check_one(value, tmpl.EXPECT_VARS, f"{path}: шаг {n} expect.{field}[{j}]")


def _check_one(text: str, allowed: frozenset[str], where: str) -> None:
    try:
        tmpl.check(text, allowed, where)
    except tmpl.TemplateError as exc:
        raise CaseError(str(exc)) from exc


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
