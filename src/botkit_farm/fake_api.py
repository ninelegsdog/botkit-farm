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


@dataclass
class Call:
    method: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._ids = itertools.count(1000)

    def add(self, method: str, payload: dict[str, Any]) -> int:
        self.calls.append(Call(method, payload))
        return next(self._ids)

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


def result_of(method: str, payload: dict[str, Any], mid: int) -> Any:
    """Ответ в форме, которую ждёт aiogram для этого метода.

    aiogram разбирает result по классу возврата метода: answerCallbackQuery
    честно возвращает True, sendMessage — Message, sendDocument — Document. Пока
    сервер отдавал на всё `True`, docuflow падал с ClientDecodeError уже после
    того, как документ был «отправлен», и кадр обрывался на середине.

    Метод, для которого результат не описан, получает True: aiogram такой ответ
    не проверяет, а если начнёт — упадёт с ClientDecodeError и назовёт метод.
    Это громче, чем правдоподобный объект, придуманный наугад: выдуманный ответ
    нарисовал бы в кадре то, чего бот не отправлял.
    """
    chat_id = payload.get("chat_id") or 424242
    if method in ("sendMessage", "editMessageText"):
        return {
            "message_id": mid,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "text": payload.get("text") or payload.get("caption") or "",
        }
    if method == "sendDocument":
        # Единственный не-текстовый метод во всех девяти ботах: docuflow отдаёт PDF
        # через answer_document. aiogram ждёт от него Message с вложенным document,
        # а не сам Document — на этом стенд и падал в ClientDecodeError.
        return {
            "message_id": mid,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "caption": payload.get("caption") or "",
            "document": {
                "file_id": f"farm-file-{mid}",
                "file_unique_id": f"farm-file-{mid}",
                "file_name": "document.pdf",
                "mime_type": "application/pdf",
                "file_size": 4096,
            },
        }
    return True


async def serve(rec: Recorder, port: int = 8099) -> web.AppRunner:
    runner = web.AppRunner(build_app(rec), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
