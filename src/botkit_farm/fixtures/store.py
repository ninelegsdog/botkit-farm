"""Фикстуры StoreBot: категории, товары и один уже выданный заказ.

Образец для остальных ботов. Правила, которые нельзя нарушать:

* Писать только через API бота (`db.session()`), а не sqlite3 напрямую: схема
  принадлежит миграциям бота, и обход их — источник расхождений.
* Данные вымышленные: ни одного реального имени, телефона или адреса.
* Минимум данных, который нужен сценарию. Лишние строки не помогают, зато
  мешают заметить, что сценарий пошёл не по той ветке.

Про этот бот: `migrate()` в `src/core/migrations.py` объявляет таблицы одним
схлопнутым SCHEMA (а не реестром миграций, как у bookingbot/membership), а
`src/store/models.py` в репозитории нет — смётка таблиц живёт только там.
"""

from __future__ import annotations

from typing import Any

from botkit_farm import scene

# Категории. Порядок в кадре задаёт бот: get_categories сортирует по position.
CATEGORIES = [
    ("Шаблоны", 1),
    ("Иконки", 2),
]

# Товары: категория (1-based), имя, описание, цена в Stars, тип и содержимое выдачи.
PRODUCTS = [
    (
        1,
        "Шаблон презентации",
        "Пятьдесят слайдов, тёмная тема",
        150,
        "text",
        "Ссылка на шаблон: storage.example.invalid/demo-pack",
    ),
    (
        1,
        "Чек-лист запуска",
        "PDF на 12 страниц",
        90,
        "text",
        "Ключ доступа: DEMO-0000-1111-2222-3333",
    ),
    (
        2,
        "Набор иконок",
        "120 иконок в трёх стилях",
        75,
        "text",
        "Архив: storage.example.invalid/demo-icons",
    ),
]


async def seed(db: Any) -> None:
    from sqlalchemy import text

    uid = scene.FAKE_USER_ID
    # Второй товар первой категории — тот, что уже куплен и выдан. Заказ нужен
    # для ветки «Мои покупки» → «Получить товар»: без product_deliveries хендлер
    # store_redeliver отвечает только «Товар ещё не выдан».
    bought_product_id = 2

    stmts: list[tuple[str, dict]] = []
    for name, position in CATEGORIES:
        stmts.append(
            (
                "insert into categories (name, position, is_active) values (:n,:p,1)",
                {"n": name, "p": position},
            )
        )
    for category_id, name, desc, price, delivery_type, payload in PRODUCTS:
        stmts.append(
            (
                "insert into products (category_id, name, description, price, delivery_type, "
                "delivery_payload, is_active) values (:cid,:n,:d,:p,:dt,:dp,1)",
                {
                    "cid": category_id,
                    "n": name,
                    "d": desc,
                    "p": price,
                    "dt": delivery_type,
                    "dp": payload,
                },
            )
        )
    # Заказ со статусом delivered и приложенной выдачей: карточка заказа в
    # «Моих покупках» рисует статус, а «Получить товар» достаёт payload.
    stmts.append(
        (
            "insert into orders (user_id, product_id, amount, payment_id, delivered_at, status) "
            "values (:uid,:pid,90,'demo-stars-0001',datetime('now','-2 days'),'delivered')",
            {"uid": uid, "pid": bought_product_id},
        )
    )
    stmts.append(
        (
            "insert into product_deliveries (order_id, payload) values (1, :p)",
            {"p": "Ключ доступа: DEMO-0000-1111-2222-3333"},
        )
    )
    async with db.session() as conn:
        for sql, params in stmts:
            await conn.execute(text(sql), params)
        await conn.commit()
