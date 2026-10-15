"""Тесты сцены: Update'ы клиента и их сырой JSON (режим `raw`, ТЗ C2).

Сцена — источник всех действий клиента. Ошибка здесь не «видна глазом»: бот
получит не тот Update, и вердикт кейса станет неправдой. Отдельно проверяется
сырой режим: он обязан быть тем же Update, только прошедшим сериализацию.
"""

from __future__ import annotations

import pytest
from aiogram.types import Update
from pydantic import ValidationError

from botkit_farm import scene

# ── обычный режим ───────────────────────────────────────────────────────────


def test_сообщение_становится_update() -> None:
    update = scene.to_message("привет", 7)

    assert update.update_id == 7
    assert update.message is not None
    assert update.message.text == "привет"
    assert update.message.from_user is not None
    assert update.message.from_user.id == scene.FAKE_USER_ID


def test_нажатие_становится_callback() -> None:
    update = scene.to_callback("booking:5", 9)

    assert update.update_id == 9
    assert update.callback_query is not None
    assert update.callback_query.data == "booking:5"
    assert update.callback_query.from_user.id == scene.FAKE_USER_ID


# ── сырой режим: тот же Update, но через JSON ───────────────────────────────


def test_сырой_update_это_словарь() -> None:
    raw = scene.to_raw_update(scene.to_message("привет", 3))

    assert isinstance(raw, dict)
    assert raw["update_id"] == 3
    assert raw["message"]["text"] == "привет"


def test_дата_в_сыром_виде_числом_как_у_telegram() -> None:
    """Telegram присылает дату unix-временем, а не строкой. Если сцена отдаст
    строку, разбор пройдёт, но проверяется уже не прод-путь."""
    raw = scene.to_raw_update(scene.to_message("привет", 3))

    assert isinstance(raw["message"]["date"], int)


def test_отсутствующие_поля_не_зануляются() -> None:
    """Telegram не присылает `null` для неподставленных полей — и сцена не должна."""
    raw = scene.to_raw_update(scene.to_message("привет", 3))

    assert "photo" not in raw["message"]


def test_сырой_совпадает_с_объектом() -> None:
    """Ключевая гарантия C2: тот же диалог, разница только в пути Update."""
    obj = scene.to_message("привет", 5)
    raw = scene.to_raw_update(obj)

    again = Update.model_validate(raw)

    assert again.update_id == obj.update_id
    assert again.message is not None and obj.message is not None
    assert again.message.text == obj.message.text
    assert again.message.from_user == obj.message.from_user


def test_сырое_нажатие_сохраняет_data() -> None:
    obj = scene.to_callback("booking:5", 9)
    again = Update.model_validate(scene.to_raw_update(obj))

    assert again.callback_query is not None
    assert again.callback_query.data == "booking:5"


def test_пропавшая_дата_ломает_разбор() -> None:
    """Проверка обязана уметь падать: без даты `Update.model_validate` не пройдёт.

    Это и есть цена сырого режима — он ловит то, что готовый объект прощает.
    """
    raw = scene.to_raw_update(scene.to_message("привет", 3))
    del raw["message"]["date"]

    with pytest.raises(ValidationError):
        Update.model_validate(raw)


def test_сырой_update_не_делит_состояние_с_объектом() -> None:
    """`model_dump` не должен возвращать ссылки на изменяемые поля исходника."""
    obj = scene.to_message("привет", 3)
    raw = scene.to_raw_update(obj)
    raw["message"]["text"] = "подменено"

    assert obj.message is not None
    assert obj.message.text == "привет"


# ── актёры: мост кейс → сцена (ТЗ C3) ───────────────────────────────────────


def test_actor_из_конфига_кейса() -> None:
    """`name` модели кейса становится `first_name` сцены.

    Регресс: раньше хост строил `scene.Actor(user_id=..., name=...)`, но у сцены
    нет поля `name` — любой мультиактёрный кейс падал на запуске с TypeError.
    Мост живёт в `actor_from_config`, и этот тест держит стык.
    """
    a = scene.actor_from_config({"user_id": 1001, "name": "Client", "is_bot": False})

    assert a.user_id == 1001
    assert a.is_bot is False
    assert a.to_aiogram_user().first_name == "Client"


def test_actor_без_имени_получает_запасное() -> None:
    """aiogram требует непустой first_name: пустой name не должен ронять Update."""
    a = scene.actor_from_config({"user_id": 7})

    assert a.to_aiogram_user().first_name
