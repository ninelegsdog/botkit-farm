"""Фикстура seed для botkit-farm админ-кейсов.

Создаёт минимальный набор данных: категорию, товар с ключами и оплаченный заказ.
Вызывается фермой через `fixture: admin_seed` + `seed: true`.
"""

from __future__ import annotations

from src.store import service


async def seed(db) -> None:
    """Заполнить БД тестовыми данными для админ-потока."""
    # Категория
    cat_id = await service.create_category(db, "Электроника")
    await service.update_category(db, cat_id, position=1)

    # Товар с ключами
    prod_id = await service.create_product(
        db,
        category_id=cat_id,
        name="Сертификат на 100 Stars",
        description="Тестовый цифровой товар",
        price=100,
        delivery_type="text",
        delivery_payload="",
        photo_file_id="",
    )
    await service.add_keys(db, prod_id, ["KEY-001", "KEY-002", "KEY-003"])

    # Заказ в статусе pending_payment → оплачиваем
    order_id = await service.create_order(db, user_id=111, product_id=prod_id, amount=100)
    await service.mark_paid(db, order_id, "payment_stars_123")

    # Второй заказ для отмены (не оплачен)
    await service.create_order(db, user_id=222, product_id=prod_id, amount=50)
