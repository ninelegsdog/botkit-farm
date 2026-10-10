"""Фикстуры DeliveryBot: один заказ с историей переходов.

Клиентский путь бота (`/start` → проверка заказа → подписка) читает из базы
ровно две вещи: заказ по номеру и его историю. Статусы и переходы создаёт
`src/core/migrations.py` сам (четыре статуса «Принят → В работе → Готов → Выдан»
и три связи между ними), поэтому мы их не дублируем — только опираемся на них.

Что здесь важно:

* Номер заказа и его id в сценарии не пишутся руками: бот ищет заказ по номеру,
  а кнопки оператора зовут `next:<id>`. Оба значения берутся из базы.
* Статусы адресуем **именами**, а не id: id выдаёт AUTOINCREMENT миграции, и
  жёстко зашитое `status_id=2` — это будущая ветка «Заказ не найден».
* Заказ кладём в статус «В работе», а не в первый: тогда кнопка «▶️ Следующий»
  в кадре ведёт в «Готов», и сценарий показывает смену статуса, а не «Финальный
  статус достигнут».

Подписку (`subscriptions`) намеренно не сеем: она нужна только чтобы оператор
при смене статуса отправил push клиенту, а этот push идёт через `app_state.bot`
— в стенде это настоящий `Bot`, собранный внутри `create_app()`, и подменить
его на поддельный API стенд не может (см. отчёт). Лишняя запись в базе без
видимого в кадре эффекта — просто мусор.
"""

from __future__ import annotations

from typing import Any

ORDER_NUMBER = "DL-2041"
ORDER_TITLE = "Демонстрационная посылка"

# История заказа: (статус, комментарий, смещение в днях от «сейчас»).
# Даты relative, а не зашитые: кадр должен быть воспроизводим завтра.
HISTORY = [
    ("Принят", "Заказ создан", "-2 days"),
    ("В работе", "Передан в сборку", "-1 day"),
]


async def seed(db: Any) -> None:
    from sqlalchemy import text

    async with db.session() as conn:
        # Статусы приходят из миграций. Если их нет — падать здесь, с понятным
        # текстом, а не в обработчике с «❌ Заказ не найден».
        history_rows = []
        for name, comment, shift in HISTORY:
            found = await conn.execute(text("select id from statuses where name = :n"), {"n": name})
            row = found.fetchone()
            if row is None:
                raise RuntimeError(
                    f"статус {name!r} не создан миграциями — проверь src/core/migrations.py"
                )
            history_rows.append((int(row[0]), comment, shift))

        res = await conn.execute(
            text("insert into orders (number, title, status_id) values (:num,:t,:sid)"),
            {"num": ORDER_NUMBER, "t": ORDER_TITLE, "sid": history_rows[-1][0]},
        )
        order_id = res.lastrowid
        for status_id, comment, shift in history_rows:
            await conn.execute(
                text(
                    "insert into order_status_history (order_id, status_id, comment, changed_at) "
                    "values (:oid,:sid,:c, datetime('now', :shift))"
                ),
                {"oid": order_id, "sid": status_id, "c": comment, "shift": shift},
            )
        await conn.commit()
