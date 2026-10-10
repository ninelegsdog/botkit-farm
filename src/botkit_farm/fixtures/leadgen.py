"""Фикстуры LeadGenBot: поля заявки. Менеджеров намеренно нет.

Образец для остальных ботов. Правила, которые нельзя нарушать:

* Писать только через API бота (`db.session()`), а не sqlite3 напрямую: схема
  принадлежит миграциям бота, и обход их — источник расхождений.
* Данные вымышленные: ни одного реального имени, телефона или адреса.
* Минимум данных, который нужен сценарию. Лишние строки не помогают, зато
  мешают заметить, что сценарий пошёл не по той ветке.

Про этот бот: `migrate()` в `src/core/migrations.py` объявляет таблицы одним
схлопнутым SCHEMA (а не реестром миграций, как у bookingbot/membership), а
`src/leadgen/models.py` в репозитории нет — смётка таблиц живёт только там.

Таблица `managers` остаётся пустой намеренно. `confirm_lead` рассылает карточку
лида каждому активному менеджеру, и стенд записывает все sendMessage в одну
транскрипцию: если бы в `managers` была строка, в кадр клиента попала бы
админская карточка, которой он не видел. Пустой `managers` даёт честный кадр
пути клиента; заодно он не проверяет ветку уведомления менеджеров.
"""

from __future__ import annotations

from typing import Any

# Поля заявки: (ключ, подпись на кнопке, порядок). Ключ попадает в заявку как
# есть — бот показывает клиенту `field_values["Интерес"]`, то есть slug.
FIELDS = [
    ("landing", "Лендинг под ключ", 1),
    ("chatbot", "Чат-бот для отдела продаж", 2),
    ("audit", "Аудит отдела продаж", 3),
]


async def seed(db: Any) -> None:
    from sqlalchemy import text

    stmts: list[tuple[str, dict]] = [
        (
            "insert into lead_fields (key, label, kind, field_order, is_active) "
            "values (:k,:l,'text',:o,1)",
            {"k": key, "l": label, "o": order},
        )
        for key, label, order in FIELDS
    ]
    async with db.session() as conn:
        for sql, params in stmts:
            await conn.execute(text(sql), params)
        await conn.commit()
