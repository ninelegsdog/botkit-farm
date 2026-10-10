"""Фикстуры DocuFlow: два активных шаблона документов с полями.

Сценарию нужно ровно три вещи из базы: категория, шаблон в ней и поля шаблона.
Больше клиентский путь (`/start` → категория → шаблон → поля → сводка) из базы
ничего не читает. Платим мы здесь только за форму документа: все реквизиты
вымышлены, реальных данных нет.

Правила, которые нельзя нарушать (те же, что в fixtures_bookingbot):

* Писать только через API бота (`db.session()`), а не sqlite3 напрямую: схема
  принадлежит миграциям бота, обход их — источник расхождений.
* Идентификатор шаблона берём у самой базы (`lastrowid`), а не считаем
  «первый вставился — значит 1»: AUTOINCREMENT не обязан начинаться с единицы.
* Ни строки в `users`, ни в `documents`: их создаёт сам бот по ходу диалога.
  Заранее засеянный пользователь замаскировал бы, что `/start` отработал.

Про платежи: в боте оплата идёт через Telegram Stars (`/buy_docs` → `sendInvoice`),
в стенде она выключена, поэтому в сценарии её нет и в фикстурах `payments`/`subscriptions`
мы тоже не трогаем — пустые таблицы честнее таблиц с правдоподобными записями.
"""

from __future__ import annotations

from typing import Any

# Шаблон = (название, категория, тело документа, поля).
# Поле = (имя, подпись для клиента, тип, пример, порядок).
# Типы поля ограничены validate_field_value: text | number | date | phone,
# поэтому пример обязан проходить валидацию своего типа — иначе сценарий
# упрётся в «⚠️ Введите число» вместо следующего поля.
TEMPLATES: list[tuple[str, str, str, list[tuple[str, str, str, str, int]]]] = [
    (
        "Договор оказания услуг",
        "Договоры",
        "ДОГОВОР ОКАЗАНИЯ УСЛУГ\n"
        "\n"
        "Исполнитель: {{client}}\n"
        "Стоимость услуг: {{sum}} руб.\n"
        "\n"
        "Срок действия: с даты подписания\n"
        "Подпись: ______________",
        [
            ("client", "Наименование исполнителя", "text", "ООО «Ромашка»", 0),
            ("sum", "Стоимость по договору", "number", "45000", 1),
        ],
    ),
    (
        "Справка об обучении",
        "Справки",
        "СПРАВКА ОБ ОБУЧЕНИИ\n"
        "\n"
        "Выдан(а): {{person}}\n"
        "Программа: «Основы работы в Telegram»\n"
        "Объём: 16 академических часов",
        [
            ("person", "ФИО обучающегося", "text", "Иван П.", 0),
        ],
    ),
]


async def seed(db: Any) -> None:
    from sqlalchemy import text

    async with db.session() as conn:
        for name, category, body, fields in TEMPLATES:
            res = await conn.execute(
                text("insert into templates (name, category, body, is_active) values (:n,:c,:b,1)"),
                {"n": name, "c": category, "b": body},
            )
            tpl_id = res.lastrowid
            for fname, label, ftype, example, order in fields:
                await conn.execute(
                    text(
                        "insert into template_fields "
                        "(template_id, name, label, field_type, required, example, field_order) "
                        "values (:tid,:fn,:lb,:ft,1,:ex,:o)"
                    ),
                    {
                        "tid": tpl_id,
                        "fn": fname,
                        "lb": label,
                        "ft": ftype,
                        "ex": example,
                        "o": order,
                    },
                )
        await conn.commit()
