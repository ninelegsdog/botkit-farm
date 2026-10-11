"""Сценарии пользователя для стенда.

Сценарий — это последовательность действий «обычного клиента в Telegram»:
написать текст или нажать inline-кнопку. Каждое действие превращается в Update,
который отдаётся реальному диспетчеру бота.

Поддержка мультипользовательских кейсов (ТЗ C3): можно задать несколько
акторов (ролей) с разными user_id и чат (private/group/supergroup). Если
акторы не заданы, используется классический один FAKE_USER_ID.
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


@dataclass
class Actor:
    """Участник мультипользовательского кейса.

    Используется в scene для построения Update с нужным from_user и chat.
    """

    user_id: int
    first_name: str = "Иван"
    last_name: str = "П."
    is_bot: bool = False
    username: str | None = None

    def to_aiogram_user(self) -> Any:
        """Convert to aiogram User."""
        from aiogram.types import User

        return User(
            id=self.user_id,
            is_bot=self.is_bot,
            first_name=self.first_name,
            last_name=self.last_name,
            username=self.username,
        )


@dataclass
class ChatConfig:
    """Настройки чата для Update.

    type: private | group | supergroup
    title: название чата (для групп)
    thread_id: ID топика (для форумов)
    bot_username: username бота (для упоминаний)
    """

    id: int = FAKE_CHAT_ID
    type: str = "private"
    title: str | None = None
    thread_id: int | None = None
    bot_username: str | None = None

    def to_aiogram_chat(self) -> Any:
        """Convert to aiogram Chat."""
        from aiogram.types import Chat

        return Chat(id=self.id, type=self.type, title=self.title)


def _default_actor() -> Actor:
    return Actor(user_id=FAKE_USER_ID, first_name="Иван", last_name="П.")


def _default_chat() -> ChatConfig:
    return ChatConfig(id=FAKE_CHAT_ID, type="private")


def _incoming_message(
    text: str,
    mid: int,
    *,
    actor: Actor | None = None,
    chat: ChatConfig | None = None,
) -> Any:
    from aiogram.types import Message

    actor = actor or _default_actor()
    chat = chat or _default_chat()
    now = dt.datetime.now(tz=dt.UTC)
    return Message(
        message_id=mid,
        date=now,
        chat=chat.to_aiogram_chat(),
        from_user=actor.to_aiogram_user(),
        text=text,
    )


def to_message(
    text: str,
    update_id: int,
    *,
    actor: Actor | None = None,
    chat: ChatConfig | None = None,
) -> Any:
    """Update с текстовым сообщением от клиента."""
    from aiogram.types import Update

    return Update(
        update_id=update_id,
        message=_incoming_message(text, 100 + update_id, actor=actor, chat=chat),
    )


def to_callback(
    data: str,
    update_id: int,
    bot_message_id: int = 1,
    *,
    actor: Actor | None = None,
    chat: ChatConfig | None = None,
) -> Any:
    """Update с нажатием inline-кнопки (callback_data)."""
    from aiogram.types import CallbackQuery, Update

    actor = actor or _default_actor()
    chat = chat or _default_chat()
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(5000 + update_id),
            from_user=actor.to_aiogram_user(),
            chat_instance="farm",
            data=data,
            message=_incoming_message("", bot_message_id, actor=actor, chat=chat),
        ),
    )


def to_raw_update(update: Any) -> dict[str, Any]:
    """Update → JSON-словарь в том виде, в каком его присылает Telegram.

    Режим `raw` (ТЗ C2): вместо готового объекта `Update` диспетчеру отдаётся
    словарь, и бот сам его разбирает (`feed_raw_update` → `Update.model_validate`).
    Это дороже, но проверяет ровно тот путь, каким Update приходит в проде с
    webhook или из polling: десериализацию. Самый частый класс багов — бот
    полагается на поле, которого в сыром JSON нет.

    `model_dump(mode="json")` даёт ровно Telegram-представление: даты — числом
    (unix-время), вложенные объекты — словарями. `exclude_none` убирает
    отсутствующие поля, как их не присылает Telegram; оставлять `null` значило
    бы проверять не тот JSON, что приходит в жизни.
    """
    # `update` типизирован Any (это объект aiogram из чужого окружения), поэтому
    # результат dump тоже Any; аннотация возврата держит границу типизированной.
    dumped: dict[str, Any] = update.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


__all__ = [
    "Actor",
    "ChatConfig",
    "FAKE_USER_ID",
    "FAKE_CHAT_ID",
    "FAKE_NAME",
    "FAKE_PHONE",
    "Step",
    "cb",
    "msg",
    "to_callback",
    "to_message",
    "to_raw_update",
]
