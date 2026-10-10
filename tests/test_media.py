"""Проверки медиа: фейковый API, контракт и движок.

Три места, где медиа может «потеряться» молча, и каждое закрыто здесь:

1. фейковый API отдаёт не тот объект → aiogram падает с ClientDecodeError уже
   после отправки, и кадр обрывается (дефект из [[опыт/lessons]]);
2. контракт границы не описывает медиа → движок его не видит;
3. движок видит, но не проверяет → кейс с `media_kind` проходит всегда.

Поэтому проверяется весь путь: ответ API разбирается настоящим aiogram, хост
отдаёт факты, движок их сверяет.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from aiogram.types import Message

from botkit_farm import fake_api
from botkit_farm.case.model import Case, Expect, MediaKind
from botkit_farm.runner import engine
from botkit_farm.runner.contract import Media, parse_turns
from botkit_farm.runner.result import CaseResult, Status

# ── фейковый API: ответ обязан быть настоящим объектом Bot API ───────────────


def test_список_видов_совпадает_с_контрактом():
    """MEDIA_METHODS и MediaKind — один и тот же закрытый список."""
    assert set(fake_api.MEDIA_METHODS.values()) == set(get_args(MediaKind))


@pytest.mark.parametrize("method,field", sorted(fake_api.MEDIA_METHODS.items()))
def test_ответ_медиа_разбирается_aiogram(method: str, field: str):
    """Настоящий aiogram разбирает ответ: не «похоже на правду», а реально
    проходит `Message.model_validate`. Иначе бот падает на ClientDecodeError."""
    res = fake_api.result_of(method, {"chat_id": 7, "caption": "подпись"}, 42)
    msg = Message.model_validate(res)
    value = getattr(msg, field)
    nested = value[0] if isinstance(value, list) else value
    assert nested is not None, f"{method}: aiogram не увидел {field}"
    assert nested.file_id == "farm-file-42"
    assert msg.caption == "подпись"


def test_photo_приходит_списком():
    """У photo в Message — массив PhotoSize; одиночный объект aiogram не примет."""
    msg = Message.model_validate(fake_api.result_of("sendPhoto", {}, 1))
    assert isinstance(msg.photo, list) and len(msg.photo) == 1


def test_неизвестный_метод_по_прежнему_true():
    assert fake_api.result_of("deleteWebhook", {}, 1) is True


# ── media_of: факты для транскрипции ────────────────────────────────────────


def test_media_of_достаёт_факты():
    rec = fake_api.Recorder()
    mid = rec.add("sendDocument", {"chat_id": 1, "caption": "Прайс"})
    call = rec.calls[-1]
    assert fake_api.media_of(call) == ("document", f"farm-file-{mid}", "Прайс")


def test_media_of_для_текста_ничего_не_возвращает():
    rec = fake_api.Recorder()
    rec.add("sendMessage", {"text": "привет"})
    assert fake_api.media_of(rec.calls[-1]) is None


# ── движок: проверки медиа ──────────────────────────────────────────────────


def _check(exp: dict[str, Any], media: list[Media]) -> list[Any]:
    return engine._check(Expect.model_validate(exp), 0, "шаг", [], [], media)


def test_media_kind_сходится():
    assert not _check({"media_kind": "photo"}, [Media(kind="photo")])


def test_media_kind_не_то_даёт_ошибку():
    fails = _check({"media_kind": "photo"}, [Media(kind="document")])
    assert fails and fails[0].kind == "media_kind"


def test_media_count_считает_вложения():
    media = [Media(kind="photo"), Media(kind="photo")]
    assert not _check({"media_count": 2}, media)
    fails = _check({"media_count": 1}, media)
    assert fails and fails[0].kind == "media_count"


def test_caption_contains_сверяет_подписи():
    media = [Media(kind="document", caption="Прайс-лист на 2026")]
    assert not _check({"caption_contains": ["Прайс-лист"]}, media)
    fails = _check({"caption_contains": ["Каталог"]}, media)
    assert fails and fails[0].kind == "caption_missing"


def test_медиа_не_считается_молчанием():
    """silent: true при присланном фото — провал: бот ответил, пусть и вложением."""
    fails = _check({"silent": True}, [Media(kind="photo")])
    assert fails and fails[0].kind == "not_silent"


def test_сообщение_с_медиа_получает_реплику_и_медиа():
    """Медиа без текста обязан создать реплику бота, иначе кадр пуст и проверки
    media_* не увидят ни одного вложения."""
    case = Case.model_validate(
        {
            "id": "media-case",
            "title": "Медиа-кейс",
            "source": ".",
            "steps": [
                {
                    "send": "/price",
                    "expect": {"media_kind": "document", "caption_contains": ["Прайс"]},
                }
            ],
        }
    )
    turns = parse_turns(
        [
            {
                "kind": "step",
                "text": "написать «/price»",
                "replies": [],
                "messages": [],
                "toasts": [],
                "media": [{"kind": "document", "file_id": "farm-file-1", "caption": "Прайс"}],
                "buttons": [],
            }
        ]
    )
    res = CaseResult(case_id=case.id, title=case.title, status=Status.ERRORED)
    assert engine._consume(turns, case, res) is Status.PASSED
    bot_turns = [t for t in res.turns if not t.out]
    assert bot_turns and bot_turns[-1].media[0].kind == "document"


# ── рендер: медиа превращается в <img> ──────────────────────────────────────


def test_медиа_рисуется_тегом_img():
    from botkit_farm import render

    html = render.page_html(
        [
            {
                "text": "",
                "out": False,
                "buttons": [],
                "media": [{"kind": "photo", "caption": "Кадр"}],
            }
        ],
        title="Бот",
        handle="@bot",
        stamp="01.01.2026",
    )
    assert "<img" in html and "Кадр" in html
