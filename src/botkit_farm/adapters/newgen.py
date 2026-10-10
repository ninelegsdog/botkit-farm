"""Сборка ботов нового поколения: delivery, docuflow, leadgen, pricesentry, store, support.

Их composition root другой: create_app() → config.validate() → migrate(db) →
AuthMiddleware/ThrottlingMiddleware/RetryMiddleware → register_routers(state).
Стена между поколениями проходит по bot.py: у новых он в src/bot.py, у старых в
корне репозитория.

Соответственно различаются и настройки: новые читают DB_PATH и WEBHOOK_SECRET из
окружения и создают бота сами (create_app), старые — DATABASE_URL и
WEBHOOK_SECRET_TOKEN. Поэтому окружение стенда — надмножество имён обоих
поколений, см. run.py:prepare_env.
"""

from __future__ import annotations

import pathlib
from collections.abc import Awaitable, Callable
from typing import Any

from botkit_farm import stand


async def build(
    *, bot_dir: pathlib.Path, key: str, port: int, seed: Callable[[Any], Awaitable[None]]
) -> tuple[Any, Any, Any]:
    stand.enter_bot_dir(bot_dir)

    from src.app import register_routers
    from src.core.auth import AuthMiddleware
    from src.core.bot_factory import create_app
    from src.core.errors import RetryMiddleware, register_error_handler
    from src.core.logging import LoggingMiddleware
    from src.core.metrics import UpdatesMiddleware
    from src.core.migrations import migrate
    from src.core.throttling import ThrottlingMiddleware

    await stand.wipe_state(key, _redis_url())

    state = create_app()
    state.config.validate()
    await migrate(state.db)
    await stand.seed_db(state.db, seed)

    # Подменяем адрес API у бота, которого создал create_app: так сохраняются его
    # default-свойства (parse_mode=HTML), а в api.telegram.org не уходит ничего.
    stand.retarget_bot(state.bot, port)

    state.dp.message.middleware(AuthMiddleware(state.db))
    state.dp.message.middleware(ThrottlingMiddleware(redis_url=state.config.redis_url))
    state.dp.callback_query.middleware(AuthMiddleware(state.db))
    state.dp.update.outer_middleware(LoggingMiddleware())
    state.dp.update.outer_middleware(UpdatesMiddleware())
    state.dp.message.middleware(RetryMiddleware())
    register_error_handler(state.dp)
    register_routers(state)
    return state.dp, state.bot, state.db


def _redis_url() -> str:
    import os

    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6380/0")
