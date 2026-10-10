"""Фикстуры PriceSentry: один отслеживаемый товар, история цен и уведомление.

Сценарий только читает базу (`get_user_items`, `hist:*`), поэтому сид — это ровно
то, что накопил бы планировщик бота за несколько проверок: цена до целевой,
три записи в price_history и одно сработавшее уведомление.

Словом сообщения уведомления взят текст самого бота из
src/scheduler/loop.py — `f"Цена снизилась: {name}"`, — иначе на кадре рядом с
настоящей репликой стояла бы выдуманная.

Сеть не нужна: fetcher (httpx) сценарий не трогает, а `price_check_loop` на
стенде не запускается — цены в базе те, что засеяны здесь.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

ITEM = {
    "sku": "WB-88213",
    "name": "Кроссовки Runner Lite",
    "marketplace": "wb",
    "current_price": 4990.0,
    "target_price": 5200.0,
    "url": "https://www.wildberries.ru/catalog/0/detail.aspx?nm=88213",
}

# (сколько дней назад, цена) — так кадр не привязан к конкретной дате съёмки.
HISTORY = [(2, 6190.0), (1, 5490.0), (0, 4990.0)]


async def seed(db: Any) -> None:
    from sqlalchemy import text

    from botkit_farm import scene

    today = dt.date.today()

    def stamp(days_ago: int, hhmm: str) -> str:
        day = (today - dt.timedelta(days=days_ago)).isoformat()
        return f"{day} {hhmm}:00"

    created = stamp(3, "10:05:00")
    async with db.session() as conn:
        res = await conn.execute(
            text(
                "insert into monitored_items "
                "(sku, name, marketplace, current_price, target_price, url, is_active, "
                " owner_id, created_at, updated_at) "
                "values (:sku, :name, :mp, :cur, :target, :url, 1, :owner, :created, :created)"
            ),
            {
                "sku": ITEM["sku"],
                "name": ITEM["name"],
                "mp": ITEM["marketplace"],
                "cur": ITEM["current_price"],
                "target": ITEM["target_price"],
                "url": ITEM["url"],
                "owner": scene.FAKE_USER_ID,
                "created": created,
            },
        )
        item_id = res.lastrowid
        for hours, price in HISTORY:
            await conn.execute(
                text(
                    "insert into price_history (item_id, price, checked_at) values (:item, :p, :at)"
                ),
                {"item": item_id, "p": price, "at": stamp(hours, "09:15" if hours else "07:55")},
            )
        await conn.execute(
            text(
                "insert into alerts (item_id, user_id, message, old_price, new_price, sent_at) "
                "values (:item, :user, :msg, :old, :new, :at)"
            ),
            {
                "item": item_id,
                "user": scene.FAKE_USER_ID,
                "msg": f"Цена снизилась: {ITEM['name']}",
                "old": 5490.0,
                "new": 4990.0,
                "at": stamp(0, "07:55"),
            },
        )
        await conn.commit()
