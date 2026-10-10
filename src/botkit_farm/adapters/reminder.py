"""Сборка ReminderBot: девятый бот, отдельный от обоих поколений.

У него нет ни create_app из нового ядра, ни Kit v2: composition root в корне
репозитория (bot.py), состояние — модульный синглтон src.core.bot_factory.state,
настройки — pydantic-сеттингс src.core.config.settings с обязательным
telegram_webhook_secret, схема — SQLAlchemy Base.metadata.create_all на модульном
engine, а не миграции.

ОТДЕЛЕНИЕ ОТ ПРОД-КАК ЕСТЬ: src/core/throttling.py написан по конвенции aiogram 2
(`__call__(self, handler, event, **data)` и `handler(event, **data)`), тогда как
проект на aiogram 3 и вызывает middleware как `middleware(handler, event, data)`.
Каждое текстовое сообщение reminder падает с TypeError — в стенде это и было
найдено. Ниже `_middleware_shim` повторяет вызов бота в его собственной
конвенции; логика троттлинга остаётся ботовская, меняется только способ вызова.
 Правка исчезает сама, как только бот перейдёт на aiogram 3 (см. _needs_shim).
"""

from __future__ import annotations

import inspect
import pathlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from botkit_farm import stand


async def build(
    *, bot_dir: pathlib.Path, key: str, port: int, seed: Callable[[Any], Awaitable[None]]
) -> tuple[Any, Any, Any]:
    stand.enter_bot_dir(bot_dir)

    from src.core.bot_factory import create_bot
    from src.core.config import settings
    from src.core.database import Base, engine
    from src.core.errors import register_error_handler
    from src.core.logging import LoggingMiddleware
    from src.core.throttling import ThrottlingMiddleware
    from src.core.tracing import TracingMiddleware

    await stand.wipe_state(key, settings.redis_url)
    settings.validate_on_startup()

    state = create_bot()
    stand.retarget_bot(state.bot, port)

    # Схема reminder создаётся не миграциями: Base.metadata.create_all на
    # модульном engine, ровно как в его _run_polling. Таблицы появляются в
    # metadata только после импорта моделей: в проде он транзитивный, из
    # scheduler (а тот тянет apscheduler, которого в стенде нет и не нужно).
    import src.reminder.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    state.dp.update.outer_middleware(LoggingMiddleware())
    state.dp.update.outer_middleware(TracingMiddleware())
    state.dp.message.middleware(
        _middleware_shim(ThrottlingMiddleware(redis_url=settings.redis_url))
    )
    register_error_handler(state.dp)

    from src.reminder import register_routers

    register_routers(state)

    await stand.seed_db(state.db, seed)
    return state.dp, state.bot, state.db


def _context_param(mw_class: type) -> str:
    """Как middleware принимает контекст: позиционным data или распаковкой kwargs."""
    params = list(inspect.signature(mw_class.__call__).parameters.values())
    tail = [p for p in params if p.name not in ("self", "handler", "event")]
    if not tail:
        return "**kwargs"
    first = tail[0]
    if first.kind is inspect.Parameter.VAR_KEYWORD:
        return "**kwargs"
    if first.name == "data" and first.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
        return "data"
    raise RuntimeError(
        f"{mw_class.__module__}.{mw_class.__name__}: нераспознанный параметр контекста "
        f"{first.name!r} ({first.kind}) — стенд не умеет подстраиваться под неё молча"
    )


def _calls_handler_aiogram2(mw_class: type) -> bool:
    """Вызывает ли middleware следующий обработчик по конвенции aiogram 2.

    По сигнатуре это не определить: у reminder __call__ уже принимает
    `data` позиционно (как в aiogram 3), а внутри зовёт `handler(event, **data)`,
    и именно на этом падает — раскрытый контекст уходит в обёртку, которая
    ждёт (event, kwargs). Поэтому смотрим на тело, а не на объявление.
    Неразбираемый исходник — это отказ, а не догадка.
    """
    try:
        source = inspect.getsource(mw_class.__call__)
    except (OSError, TypeError) as exc:
        raise RuntimeError(
            f"{mw_class.__module__}.{mw_class.__name__}: исходник __call__ недоступен "
            f"({type(exc).__name__}: {exc}); стенд не станет угадывать конвенцию вызова"
        ) from exc
    return bool(re.search(r"handler\([^)]*\*\*", source, re.DOTALL))


def _middleware_shim(mw: Any) -> Any:
    """Перевести middleware бота с конвенции aiogram 2 на конвенцию aiogram 3.

    Расхождение в двух независимых местах, и ломается именно вызов, а не
    объявление:

    * aiogram 3 собирает цепочку как `middleware(handler, event, data)`, где
      handler — функция `(event, kwargs)`, а reminder зовёт её как
      `handler(event, **data)` и падает с TypeError (в тексте ошибки видно
      `CallableObject.call()` — functools.wraps сохраняет имя обёртки);
    * контекст reminder объявляет как `**data`, aiogram передаёт его одним dict.

    Shim вызывает middleware бота по-настоящему — иначе троттлинг был бы молча
    выключен, а кадр показывал бы бота, которого нет. Меняется только передача:
    боту — та форма, которую он ждёт, дальше по цепочке — та, которую ждёт aiogram.
    Shim исчезает сам, когда бот перейдёт на aiogram 3 (см. _calls_handler_aiogram2).
    """
    from aiogram.dispatcher.middlewares.base import BaseMiddleware

    if not _calls_handler_aiogram2(type(mw)):
        return mw
    wants_kwargs_context = _context_param(type(mw)) == "**kwargs"

    class _Shim(BaseMiddleware):
        async def __call__(
            self, handler: Callable[..., Awaitable[Any]], event: Any, data: Any
        ) -> Any:
            async def next_handler(event_: Any, **data_: Any) -> Any:
                return await handler(event_, data_)

            if wants_kwargs_context:
                return await mw(next_handler, event, **data)
            return await mw(next_handler, event, data)

    return _Shim()
