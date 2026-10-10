"""Сборка ботов старого поколения: bookingbot, membership.

Их composition root — BotKit Kit v2: AdminGate + collect_routers(gate, nav, db) +
build_dispatcher(storage, throttling) + MigrationRegistry, миграции из
src.<бот>.models.register_migrations. Порядок и набор миддлваров повторяют их
bot.py:main() до последней строки.

Остальные семь ботов собираются иначе: см. adapter_newgen.py и adapter_reminder.py.
"""

from __future__ import annotations

import importlib
import pathlib
import pkgutil
from collections.abc import Awaitable, Callable
from typing import Any

from botkit_farm import stand


async def build(
    *, bot_dir: pathlib.Path, key: str, port: int, seed: Callable[[Any], Awaitable[None]]
) -> tuple[Any, Any, Any]:
    stand.enter_bot_dir(bot_dir)

    from src.core.auth import AdminGate
    from src.core.bot_factory import build_dispatcher, create_bot
    from src.core.config import Settings
    from src.core.database import Database
    from src.core.errors import RetryMiddleware, register_error_handler
    from src.core.logging import LoggingMiddleware
    from src.core.metrics import UpdatesMiddleware
    from src.core.migrations import MigrationRegistry
    from src.core.navigation import NavRegistry
    from src.core.storage import create_storage
    from src.core.throttling import ThrottlingMiddleware

    settings = Settings()
    registry = MigrationRegistry()
    migrations = _find_migrations_module()
    migrations.register_migrations(registry)

    nav = NavRegistry()
    db = Database(settings.database_url)
    await db.init_database(registry)
    await stand.seed_db(db, seed)

    from src.app import collect_routers

    gate = AdminGate(settings.admin_password, settings.admin_ids)
    routers = collect_routers(gate=gate, nav=nav, db=db)
    dp = build_dispatcher(
        routers=routers,
        storage=create_storage(settings.redis_url),
        throttling=ThrottlingMiddleware(
            redis_url=settings.redis_url, rate_limit=2.0, max_idle=60.0
        ),
    )
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(UpdatesMiddleware())
    dp.message.middleware(RetryMiddleware())
    register_error_handler(dp)

    bot = create_bot(settings.bot_token)
    stand.retarget_bot(bot, port)
    return dp, bot, db


def _find_migrations_module() -> Any:
    """Модуль с register_migrations у каждого бота свой (src.booking.models и т.п.)."""
    import src

    for m in pkgutil.iter_modules(src.__path__):
        try:
            mod = importlib.import_module(f"src.{m.name}.models")
        except Exception:
            continue
        if hasattr(mod, "register_migrations"):
            return mod
    raise RuntimeError(f"{pathlib.Path.cwd().name}: не найден модуль с register_migrations")
