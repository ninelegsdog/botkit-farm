"""Фикстуры ReminderBot: одно напоминание, созданное раньше.

Сценарий создаёт напоминание сам, а последним шагом открывает «⏰ Мои напоминания»
— там нужен не одинокий список из одной строки, а две: у клиента к моменту съёмки
уже было одно активное напоминание.

Пишем через собственный API бота: сессия `db.async_session` и ORM-модель Reminder,
те же, что в src/reminder/handlers.py. sqlite3 напрямую не трогаем — схема
принадлежит моделям бота.

Таблицы создаёт не фикстура, а сборка бота (`Base.metadata.create_all`, ровно как
в его bot.py), поэтому здесь только данные.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

# Одноразовое напоминание на завтра: в списке у клиента оно встанет над новым.
PRIOR: dict[str, Any] = {
    "text": "Позвонить по закаку №7742 — спросить доставку",
    "hour": 18,
    "minute": 0,
}


async def seed(db: Any) -> None:
    from src.reminder.models import Reminder, ReminderType

    from botkit_farm import scene

    fire_at = dt.datetime.combine(
        dt.date.today() + dt.timedelta(days=1),
        dt.time(hour=PRIOR["hour"], minute=PRIOR["minute"]),
        tzinfo=dt.UTC,
    )
    async with db.async_session() as session:
        session.add(
            Reminder(
                creator_id=scene.FAKE_USER_ID,
                type=ReminderType.once,
                fire_at=fire_at,
                text=PRIOR["text"],
            )
        )
        await session.commit()
