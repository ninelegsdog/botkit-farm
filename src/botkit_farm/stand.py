"""Общие детали сборки стенда: подделка API, чистка состояния, фикстуры.

Стенд не меняет код ботов. Он меняет три вещи вокруг него: адрес API (на
поддельный сервер), состояние (пустая БД и пустой Redis) и данные (фикстуры).
Всё, что внутри бота, остаётся настоящим — иначе кадр ничего не доказывает.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Awaitable, Callable
from typing import Any


def enter_bot_dir(bot_dir: pathlib.Path) -> None:
    """Встать в каталог бота и положить его первым в sys.path.

    Так делает сам бот: `python bot.py` из своего каталога. Смена каталога важна не
    для красоты — config с fallback-путями (data/store.db, ./data/reminder.db)
    и .env читаются относительно него.
    """
    os.chdir(bot_dir)
    sys.path.insert(0, str(bot_dir))


def retarget_bot(bot: Any, port: int) -> None:
    """Перевести настоящий экземпляр Bot на поддельный API.

    Меняется адрес у уже созданного бота, а не создаётся новый: у бота есть
    default-свойства (parse_mode и прочее), и подмена их молча изменила бы то,
    как бот формирует исходящие сообщения, — то есть сам кадр.
    """
    from aiogram.client.telegram import TelegramAPIServer

    bot.session.api = TelegramAPIServer(
        base=f"http://127.0.0.1:{port}/bot{{token}}/{{method}}",
        file=f"http://127.0.0.1:{port}/file/bot{{token}}/{{path}}",
        is_local=False,
    )


async def wipe_state(key: str, redis_url: str) -> list[str]:
    """Стереть FSM и троттлинг прошлого прогона из Redis.

    Redis переживает прогон, база — нет. Без этого второй запуск продолжил бы
    диалог с середины: FSM от предыдущего кадра, троттлинг от последнего
    сообщения, и сценарий уезжал бы в другую ветку без единой ошибки.
    """
    import redis.asyncio as aioredis

    patterns = (f"fsm:{key}:*", f"throttle:{key}:*")
    removed: list[str] = []
    r = aioredis.from_url(redis_url, decode_responses=True)
    try:
        for pattern in patterns:
            async for k in r.scan_iter(match=pattern, count=500):
                await r.delete(k)
                removed.append(k)
    finally:
        await r.aclose()
    return removed


async def seed_db(db: Any, seed: Callable[[Any], Awaitable[None]]) -> None:
    """Заполнить свежую базу данными фикстуры бота."""
    await seed(db)
