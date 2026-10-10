"""Сценарии пользователя для стенда.

Сценарий — это последовательность действий «обычного клиента в Telegram»:
написать текст или нажать inline-кнопку. Каждое действие превращается в Update,
который отдаётся реальному диспетчеру бота.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any


@dataclass
class Step:
    kind: str  # "msg" | "cb"
    payload: str  # текст сообщения или callback_data
    label: str = ""  # что показывать в рендере как «действие пользователя»


def msg(text: str, label: str = "") -> Step:
    return Step("msg", text, label or text)


def cb(data: str, label: str = "") -> Step:
    return Step("cb", data, label or data)


FAKE_USER_ID = 424242
FAKE_CHAT_ID = 424242
FAKE_NAME = "Иван П."
FAKE_PHONE = "+7 (999) 000-00-00"


def _chat() -> Any:
    from aiogram.types import Chat

    return Chat(id=FAKE_CHAT_ID, type="private")


def _user() -> Any:
    from aiogram.types import User

    return User(id=FAKE_USER_ID, is_bot=False, first_name="Иван", last_name="П.")


def _incoming_message(text: str, mid: int) -> Any:
    from aiogram.types import Chat, Message, User

    now = dt.datetime.now(tz=dt.UTC)
    return Message(
        message_id=mid,
        date=now,
        chat=Chat(id=FAKE_CHAT_ID, type="private"),
        from_user=User(id=FAKE_USER_ID, is_bot=False, first_name="Иван", last_name="П."),
        text=text,
    )


def to_update(step: Step, update_id: int, bot_message_id: int = 1) -> Any:
    from aiogram.types import CallbackQuery, Update

    if step.kind == "msg":
        return Update(
            update_id=update_id,
            message=_incoming_message(step.payload, 100 + update_id),
        )
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(5000 + update_id),
            from_user=_user(),
            chat_instance="farm",
            data=step.payload,
            message=_incoming_message("", bot_message_id),
        ),
    )


# ── Тонкие обёртки для раннера новой фермы ───────────────────────────────────
# Старый Step — dataclass с kind/payload; новый кейс о нём не знает. Эти две
# функции принимают готовые значения и отдают тот же Update, поэтому раннер не
# подстраивает кейс под сцену, а сцена остаётся ровно той, что проверена годами.


def to_message(text: str, update_id: int) -> Any:
    """Update с текстовым сообщением от клиента."""
    return to_update(Step("msg", text), update_id)


def to_callback(data: str, update_id: int, bot_message_id: int = 1) -> Any:
    """Update с нажатием inline-кнопки (callback_data)."""
    return to_update(Step("cb", data), update_id, bot_message_id)
