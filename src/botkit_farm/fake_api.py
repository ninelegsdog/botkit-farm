"""Поддельный Telegram Bot API для локального стенда.

Ни одного реального вызова к api.telegram.org: бот запускается в webhook-подобном
режиме, но его исходящие запросы обслуживает этот сервер, который только записывает
их. Так в стенде работает НАСТОЯЩИЙ код бота, а не имитация поведения.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

# Метод отправки медиа → поле вложенного объекта в Message. Список закрыт: он
# обязан совпадать с `MediaKind` в контракте, иначе хост и фейковый API
# разъедутся (роадмап B2).
MEDIA_METHODS: dict[str, str] = {
    "sendPhoto": "photo",
    "sendDocument": "document",
    "sendVoice": "voice",
    "sendSticker": "sticker",
    "sendVideo": "video",
    "sendAnimation": "animation",
}


@dataclass
class Call:
    method: str
    payload: dict[str, Any]
    mid: int = 0
    ts: float = field(default_factory=time.time)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._ids = itertools.count(1000)

    def add(self, method: str, payload: dict[str, Any]) -> int:
        mid = next(self._ids)
        self.calls.append(Call(method, payload, mid))
        return mid

    def outgoing_text(self) -> list[tuple[str, dict[str, Any]]]:
        """Тексты, отправленные ботом: [(text, reply_markup)]."""
        out: list[tuple[str, dict[str, Any]]] = []
        for c in self.calls:
            if c.method == "sendMessage" or c.method == "editMessageText":
                out.append((c.payload.get("text", ""), c.payload.get("reply_markup") or {}))
        return out

    def clear(self) -> None:
        self.calls.clear()


def build_app(rec: Recorder) -> web.Application:
    app = web.Application()
    app["rec"] = rec

    async def any_method(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        if request.content_type == "application/json":
            payload = await request.json()
        else:
            payload = dict(await request.post())  # aiogram шлёт form-encoded
        if isinstance(payload.get("reply_markup"), str):
            try:
                payload["reply_markup"] = json.loads(payload["reply_markup"])
            except Exception:
                payload["reply_markup"] = {}
        mid = rec.add(method, payload)
        return web.json_response({"ok": True, "result": result_of(method, payload, mid)})

    async def guard(request: web.Request) -> web.StreamResponse:
        """Показывает реальный путь: иначе 500 от роутера нечитаем."""
        return await any_method(request)

    app.router.add_post("/bot{token}/{method}", guard)
    app.router.add_post("/{tail:.*}", guard)
    return app


def media_of(call: Call) -> tuple[str, str, str] | None:
    """Вид медиа, file_id и подпись для отправленного сообщения.

    Хост берёт отсюда факты о медиа: `file_id` синтезирован тем же правилом, что
    и в ответе (`farm-file-<mid>`), поэтому кейс и транскрипция говорят об одном
    и том же файле. Не медиа — None.
    """
    field = MEDIA_METHODS.get(call.method)
    if field is None:
        return None
    return field, _file_id(call.mid), str(call.payload.get("caption") or "")


def _file_id(mid: int) -> str:
    return f"farm-file-{mid}"


def _message(mid: int, chat_id: Any, *, text: str = "", caption: str = "") -> dict[str, Any]:
    """Базовый Message Bot API: без него aiogram падает с ClientDecodeError."""
    msg: dict[str, Any] = {
        "message_id": mid,
        "date": int(time.time()),
        "chat": {"id": chat_id, "type": "private"},
    }
    if text:
        msg["text"] = text
    if caption:
        msg["caption"] = caption
    return msg


def _media_object(field: str, fid: str) -> Any:
    """Вложенный объект Bot API для вида медиа.

    Поля обязательны ровно те, что требует aiogram при разборе ответа: без
    `duration` у Voice/Bot API-объект не собирается, и бот падает уже после
    отправки — то есть кадр обрывается на середине (ровно дефект №6 из
    [[опыт/lessons]] для документа, распространённый на все виды медиа).
    """
    if field == "photo":
        # У photo в Message — СПИСОК PhotoSize, а не один объект: aiogram ждёт
        # именно массив, и одиночный объект не разберётся.
        return [
            {"file_id": fid, "file_unique_id": fid, "width": 1280, "height": 960, "file_size": 4096}
        ]
    if field == "document":
        return {
            "file_id": fid,
            "file_unique_id": fid,
            "file_name": "document.pdf",
            "mime_type": "application/pdf",
            "file_size": 4096,
        }
    if field == "voice":
        return {"file_id": fid, "file_unique_id": fid, "duration": 5, "mime_type": "audio/ogg"}
    if field == "sticker":
        return {
            "file_id": fid,
            "file_unique_id": fid,
            "type": "regular",
            "width": 512,
            "height": 512,
            "is_animated": False,
            "is_video": False,
        }
    if field in ("video", "animation"):
        return {
            "file_id": fid,
            "file_unique_id": fid,
            "width": 1280,
            "height": 720,
            "duration": 5,
            "mime_type": "video/mp4",
            "file_size": 4096,
        }
    raise AssertionError(f"вид медиа без объекта: {field}")  # pragma: no cover


def result_of(method: str, payload: dict[str, Any], mid: int) -> Any:
    """Ответ в форме, которую ждёт aiogram для этого метода.

    aiogram разбирает result по классу возврата метода: answerCallbackQuery
    честно возвращает True, sendMessage — Message, sendDocument — Message с
    вложенным document. Пока сервер отдавал на всё `True`, бот падал с
    ClientDecodeError уже после отправки, и кадр обрывался на середине.

    Метод, для которого результат не описан, получает True: aiogram такой ответ
    не проверяет, а если начнёт — упадёт с ClientDecodeError и назовёт метод.
    Это громче, чем правдоподобный объект, придуманный наугад: выдуманный ответ
    нарисовал бы в кадре то, чего бот не отправлял.
    """
    chat_id = payload.get("chat_id") or 424242
    if method in ("sendMessage", "editMessageText"):
        return _message(mid, chat_id, text=payload.get("text") or payload.get("caption") or "")
    field = MEDIA_METHODS.get(method)
    if field is not None:
        msg = _message(mid, chat_id, caption=str(payload.get("caption") or ""))
        msg[field] = _media_object(field, _file_id(mid))
        return msg
    return True


async def serve(rec: Recorder, port: int = 8099) -> web.AppRunner:
    runner = web.AppRunner(build_app(rec), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
