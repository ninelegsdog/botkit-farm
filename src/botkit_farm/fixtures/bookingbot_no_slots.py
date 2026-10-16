"""Фикстуры BookingBot без свободных слотов: один сервис, все слоты заняты.

Сценарий `booking-no-free-slots`: клиент выбирает услугу, а бот сообщает, что
свободного нет, и не показывает пустой список. Данные вымышленные; слоты
считаются от `date.today()` на 14 дней вперёд, как в основной фикстуре,
но уже занятые — `is_booked = 1`.
"""

from __future__ import annotations

from typing import Any

SERVICES = [
    ("Стрижка и укладка", 60, 2200),
]

TIMES = [("10:00", "11:00"), ("12:00", "13:00"), ("15:00", "16:00"), ("17:00", "18:00")]


async def seed(db: Any) -> None:
    import datetime as dt

    from sqlalchemy import text

    stmts: list[tuple[str, dict]] = []
    for name, dur, price in SERVICES:
        stmts.append(
            (
                "insert into services (name, duration_min, price, is_active) values (:n,:d,:p,1)",
                {"n": name, "d": dur, "p": price},
            )
        )
    for dow in range(6):
        stmts.append(
            (
                "insert into weekly_schedule (day_of_week, start_time, end_time) values (:dow,'10:00','18:00')",
                {"dow": dow},
            )
        )
    today = dt.date.today()
    for offset in range(1, 15):
        d = today + dt.timedelta(days=offset)
        if d.weekday() > 5:
            continue
        for sid in range(1, len(SERVICES) + 1):
            for start, end in TIMES:
                stmts.append(
                    (
                        "insert into slots (service_id, date, start_time, end_time, is_booked) "
                        "values (:sid,:date,:s,:e,1)",
                        {"sid": sid, "date": d.isoformat(), "s": start, "e": end},
                    )
                )
    async with db.session() as conn:
        for sql, params in stmts:
            await conn.execute(text(sql), params)
        await conn.commit()
