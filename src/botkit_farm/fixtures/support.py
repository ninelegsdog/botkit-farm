"""Фикстуры SupportBot: один закрытый тикет клиента.

Сценарий сам создаёт тикет через бота, поэтому засеивать нужно ровно одно:
чтобы «📋 Мои тикеты» показал не единственную свежую карточку, а историю —
в кадре так виден и статус «new», и «closed».

Менеджеров намеренно не сеем: `confirm_ticket` рассылает карточку нового тикета
всем активным менеджерам, и эта рассылка попала бы в транскрипцию клиента —
в кадре появилось бы сообщение, адресованное кому-то другому.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

# Тикет, закрытый накануне: категория и тема вымышленные, часовой пояс не пишем —
# opened_at в схеме хранится текстом, а бот только сортирует по нему.
CLOSED_TICKET = {
    "category": "delivery",
    "subject": "Курьер не привёз заказ №7742",
    "text": "Курьер не привёз заказ №7742, поддержка сказала «ждём до вечера», прошло три дня.",
}


async def seed(db: Any) -> None:
    from sqlalchemy import text

    from botkit_farm import scene

    opened = (dt.date.today() - dt.timedelta(days=1)).isoformat() + " 11:20:00"
    async with db.session() as conn:
        res = await conn.execute(
            text(
                "insert into tickets (user_id, category, subject, status, opened_at, closed_at) "
                "values (:uid, :cat, :subj, 'closed', :opened, :opened)"
            ),
            {
                "uid": scene.FAKE_USER_ID,
                "cat": CLOSED_TICKET["category"],
                "subj": CLOSED_TICKET["subject"],
                "opened": opened,
            },
        )
        await conn.execute(
            text(
                "insert into ticket_messages (ticket_id, sender_id, sender_role, text) "
                "values (:tid, :sid, 'client', :txt)"
            ),
            {"tid": res.lastrowid, "sid": scene.FAKE_USER_ID, "txt": CLOSED_TICKET["text"]},
        )
        await conn.execute(
            text(
                "insert into ticket_messages (ticket_id, sender_id, sender_role, text) "
                "values (:tid, 0, 'system', 'Тикет закрыт')"
            ),
            {"tid": res.lastrowid},
        )
        await conn.commit()
