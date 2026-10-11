"""Контракт адаптера: что обязан вернуть `build()` и как это проверить.

Адаптер — единственный код, который сторонний автор добавляет к ферме. Ошибка в
нём (вернул `None`, перепутал порядок) не должна выглядеть как сбой бота в
отчёте — она обязана падать здесь, с именем поля.

Контракт описан pydantic-моделью, а не докстрингом: `BuildResult` объявляет
три части результата, проверяет их структурой и документируется схемой. Объекты
бота не сериализуемы и не импортируемы из процесса фермы, поэтому проверка —
duck-typing: у диспетчера должен быть `feed_update(bot, update)`, у бота —
`session` (её хост закрывает в конце). Это ровно те два метода, которые хост
действительно вызывает.

Фабрика ищется по ссылке `module:attr` в окружении бота — `load_builder`
вызывается из подпроцесса, уже внутри venv бота ([[роадмап/tz-production]] C1).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class AdapterContractError(Exception):
    """Адаптер нарушил контракт: не тот возврат или нет фабрики."""


class BuildResult(BaseModel):
    """Результат `build()` — диспетчер, бот и (необязательно) база для фикстуры.

    `db` необязателен: бот без состояния законен, и адаптер вправе вернуть
    `None`. А вот диспетчер и бот нужны прогону всегда — их проверяют валидаторы.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dispatcher: Any
    bot: Any
    db: Any = None

    @field_validator("dispatcher")
    @classmethod
    def _диспетчер_умеет_feed_update(cls, v: Any) -> Any:
        if not callable(getattr(v, "feed_update", None)):
            raise ValueError(
                "у диспетчера нет feed_update(bot, update) — хост не сможет скормить ему Update"
            )
        return v

    @field_validator("bot")
    @classmethod
    def _бот_имеет_session(cls, v: Any) -> Any:
        if getattr(v, "session", None) is None:
            raise ValueError("у бота нет session — хост не сможет её закрыть")
        return v


def _describe(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "<корень>"
    return f"адаптер нарушил контракт: поле {loc}: {first['msg']}"


def check(raw: object) -> BuildResult:
    """Проверить возврат `build()` и вернуть типизированный результат.

    Порядок проверок важен для текста отказа: сначала форма (кортеж из трёх),
    потом содержимое. Иначе `None` вместо кортежа давал бы «поле dispatcher»
    вместо понятного «вернул None`.
    """
    if not isinstance(raw, tuple) or len(raw) != 3:
        raise AdapterContractError(
            f"build() обязан вернуть кортеж (диспетчер, бот, база), а вернул {type(raw).__name__}"
        )
    dispatcher, bot, db = raw
    try:
        return BuildResult(dispatcher=dispatcher, bot=bot, db=db)
    except ValidationError as exc:
        raise AdapterContractError(_describe(exc)) from exc


def load_builder(module: str, attr: str) -> Callable[..., Awaitable[object]]:
    """Импортировать фабрику адаптера из окружения бота.

    Импорт идёт в подпроцессе бота, поэтому модуль может тянуть зависимости,
    которых нет у фермы. Отсутствие модуля или фабрики — понятная ошибка с
    именами, а не ImportError из недр aiogram.
    """
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:
        raise AdapterContractError(
            f"модуль адаптера {module!r} не найден в окружении бота: {exc}"
        ) from exc
    builder = getattr(mod, attr, None)
    if not callable(builder):
        raise AdapterContractError(f"в модуле {module!r} нет вызываемой фабрики {attr!r}")
    return builder  # type: ignore[no-any-return]


__all__ = ["AdapterContractError", "BuildResult", "check", "load_builder"]
