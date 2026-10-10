"""Фикстуры MembershipBot: тарифы, подписка участника и разделы с контента.

Образец для остальных ботов. Правила, которые нельзя нарушать:

* Писать только через API бота (`db.session()`), а не sqlite3 напрямую: схема
  принадлежит миграциям бота, и обход их — источник расхождений.
* Данные вымышленные: ни одного реального имени, телефона или адреса.
* Минимум данных, который нужен сценарию. Лишние строки не помогают, зато
  мешают заметить, что сценарий пошёл не по той ветке.
* Никаких зависимостей от текущего времени, кроме границ подписки: дата в кадре
  должна быть осмысленной («до 24.10.2026»), иначе кадр нельзя воспроизвести
  завтра.

Про этот бот: `mem:content` и `mem:my` смотрят не на строку в subscribers, а на
активную подписку (service.is_subscriber → get_subscription). Подписчика без
подписки хватает для приветствия, но разделы контента и карточка «Моя подписка»
останутся заблокированными.
"""

from __future__ import annotations

from typing import Any

from botkit_farm import scene

# Тарифы. Порядок в кадре задаёт бот: get_active_plans сортирует по цене.
PLANS = [
    ("Месячный", 590, 30),
    ("Квартальный", 1590, 90),
    ("Годовой", 4990, 365),
]

# Разделы клуба: (заголовок, [(тип, содержимое)]). Нумерация разделов идёт по
# порядку вставки — бот сортирует по position и ничего не перенумеровывает.
SECTIONS = [
    (
        "Старт клуба",
        [
            ("text", "Как проходит первый месяц и что уже доступно"),
            ("video", "Вводная лекция · 12 минут"),
        ],
    ),
    (
        "Отчётность",
        [
            ("text", "Ежемесячный разбор в цифрах: рост и что с этим делать"),
        ],
    ),
]


async def seed(db: Any) -> None:
    import datetime as dt

    from sqlalchemy import text

    uid = scene.FAKE_USER_ID
    today = dt.date.today()

    stmts: list[tuple[str, dict]] = []
    for name, price, days in PLANS:
        stmts.append(
            (
                "insert into plans (name, price, period_days, is_active) values (:n,:p,:d,1)",
                {"n": name, "p": price, "d": days},
            )
        )
    # Участник клуба. /start вызывает ensure_subscriber и обновляет эту строку по
    # настоящему, поэтому здесь только то, что нужно для проверки прав.
    stmts.append(
        (
            "insert into subscribers (user_id, name, username, is_active) values (:uid,:n,:un,1)",
            {"uid": uid, "n": scene.FAKE_NAME, "un": ""},
        )
    )
    # Активная подписка на первый тариф из списка: он же самый дешёвый, и бот
    # показывает его первым в разделе «Тарифы».
    stmts.append(
        (
            "insert into subscriptions (user_id, plan_id, start_at, end_at, auto_renew, status) "
            "values (:uid,1,:s,:e,1,'active')",
            {
                "uid": uid,
                "s": (today - dt.timedelta(days=9)).isoformat(),
                "e": (today + dt.timedelta(days=21)).isoformat(),
            },
        )
    )
    for position, (title, items) in enumerate(SECTIONS, start=1):
        stmts.append(
            (
                "insert into content_sections (title, position, is_active) values (:t,:p,1)",
                {"t": title, "p": position},
            )
        )
        for kind, payload in items:
            stmts.append(
                (
                    "insert into content_items (section_id, type, payload, is_active) "
                    "values (:sid,:ty,:pl,1)",
                    {"sid": position, "ty": kind, "pl": payload},
                )
            )
    async with db.session() as conn:
        for sql, params in stmts:
            await conn.execute(text(sql), params)
        await conn.commit()
