"""Отбор кейсов бота по манифесту покрытия — единственный источник правды.

Зачем отдельный модуль. CI выбирал кейсы глобом по префиксу имени файла, а
ботов CI зовёт коротким именем (`bookingbot`), тогда как кейсы фермы лежат с
префиксом потока (`booking-…`). Глоб молча выбрасывал всё, что не начиналось с
короткого имени бота: три кейса бота bookingbot не исполнялись ни в одном CI,
а гейт покрытия считал поток закрытым по обязательству на один из них. «Зелёный
CI» в этом случае означал «не проверяли».

Теперь единственный источник правды о том, что гоняется, — манифест
`cases/coverage.json`: набор бота = его обязательства + его `extras`. Модуль
читает манифест через API гейта (`coverage.load`) и не заводит второго
читателя: два читателя одного манифеста — это ровно тот контракт в двух домах,
который гейт покрытия и должен закрывать.

Правила отбора:

* имя бота нормализуется — `bookingbot` и `botkit-bookingbot` это один и тот
  же бот;
* порядок выдачи стабилен (по имени файла): CI получает один и тот же список
  при каждом прогоне;
* кейс, объявленный в манифесте, но отсутствующий на диске, — **ошибка**, а не
  пропуск: иначе опечатка в манифесте снова даст «зелёный» CI без проверки.
"""

from __future__ import annotations

import pathlib

from botkit_farm import coverage

#: Префикс, которым манифест и реестр флота пишут имя бота.
BOT_PREFIX = "botkit-"


class SelectionError(Exception):
    """Отбор невозможен: манифест не читается или объявленный кейс не найден."""


def bot_key(name: str) -> str:
    """Ключ сопоставления имён ботов.

    Короткое имя (`bookingbot`) и полное (`botkit-bookingbot`) — один и тот же
    бот: различие в написании не должно решать, чей это кейс. Ключ сравнивает
    только короткую часть имени; регистр и пробелы вокруг тоже не различают —
    CI приходит из YAML, где имя может быть обёрнуто пробелами.
    """
    cleaned = name.strip().lower()
    if cleaned.startswith(BOT_PREFIX):
        cleaned = cleaned[len(BOT_PREFIX) :]
    return cleaned


def cases_for_bot(bot: str, cases_dir: pathlib.Path) -> list[pathlib.Path]:
    """Файлы кейсов бота по манифесту: обязательства + `extras`, без дублей.

    Возвращает пути в порядке по имени файла. Кейс, объявленный в манифесте,
    но отсутствующий на диске, — `SelectionError` с именем файла: пропуск был бы
    тем же молчанием, из-за которого кейс не исполнялся никогда.
    """
    try:
        manifest = coverage.load(cases_dir)
    except coverage.CoverageError as exc:
        raise SelectionError(str(exc)) from exc

    key = bot_key(bot)
    names = {ob.case for ob in manifest.obligations if bot_key(ob.bot) == key}
    names |= {extra.case for extra in manifest.extras if bot_key(extra.bot) == key}

    ordered = sorted(names)
    missing = [name for name in ordered if not (cases_dir / name).is_file()]
    if missing:
        raise SelectionError(
            "объявленные в манифесте кейсы не найдены на диске: " + ", ".join(missing)
        )
    return [cases_dir / name for name in ordered]


__all__ = ["BOT_PREFIX", "SelectionError", "bot_key", "cases_for_bot"]
