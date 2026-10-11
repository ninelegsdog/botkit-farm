"""Исполнение кода бота — в его собственном окружении, отдельным процессом.

Почему процесс, а не импорт в текущий интерпретатор:

* у ботов разные зависимости (старый Kit v2 тянет `botkit-core` из git, новый —
  `yookassa`); общее окружение означало бы, что версия одного ломает другой;
* бот при импорте делает `os.chdir`, ставит `sys.path` и читает .env — это
  разносит состояние процесса фермы, и падение бота уносит ферму с собой;
* в процесс можно положить настоящий таймаут. Бот, ушедший в бесконечный цикл,
  не должен вешать ферму.

Обмен — через файлы, а не через pickle: диспетчер не сериализуется, да и
полагаться на pickle между процессами небезопасно.

Всё, что наружу, — текст диалога и JSON с результатом.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
from dataclasses import dataclass, field

from pydantic import ValidationError

from botkit_farm import sandbox, security
from botkit_farm.runner.contract import HostTurn, parse_turns

# Скрипт-хост: поднимает бота, кормит его Update'ами и печатает результат в JSON.
_HOST = """
import asyncio, json, os, sys

# Фейковый API и сцена — из фермы, но импортируются ДО chdir бота, потому что
# после chdir в пути бота они могут перекрыться его собственным пакетом src.
sys.path.insert(0, {farm_src!r})

from botkit_farm import fake_api, quarantine, scene, security
from botkit_farm.adapters import contract as adapter_contract

ADAPTER_MODULE = {adapter_module!r}
ADAPTER_ATTR = {adapter_attr!r}
BOT_DIR = {bot_dir!r}
RAW = {raw!r}
MODE = {mode!r}
PORT = {port!r}
WEBHOOK_PORT = {webhook_port!r}
RESULT = {result!r}
DB_PATH = {db_path!r}
VIOLATIONS = {violations!r}
ACTORS = {actors!r}
CHAT = {chat!r}

# Карантин ставится ДО импорта кода бота: пока модуль не импортирован, он не
# успел ничего написать. Запрет egress в ферме сюда не дотягивается — подпроцесс
# запускается чистым интерпретатором, и без этого запрета бот был бы единственным
# участником прогона, которому сеть разрешена.
GUARD = quarantine.arm(BOT_DIR, __import__("pathlib").Path(DB_PATH).parent, __import__("pathlib").Path(VIOLATIONS).parent)

# Окружение бота ставим здесь, в его процессе, а не в ферме: конфиг бота
# (pydantic Settings) читает переменные в момент импорта своего модуля, и если
# они не выставлены ДО сборки — бот падает на «BOT_TOKEN is not set».
# Токен всегда фейковый; реальный стенд проверен на это в движке.
os.environ.update({{
    "TELEGRAM_BOT_TOKEN": "424242:FAKE-TOKEN-FOR-LOCAL-STAND",
    "DATABASE_URL": "sqlite+aiosqlite:///" + DB_PATH,
    "DB_PATH": DB_PATH,
    "REDIS_URL": {redis_url!r},
    "ADMIN_PASSWORD": "farm",
    "ADMIN_IDS": "424242",
    "WEBHOOK_URL": "",
    "WEBHOOK_SECRET_TOKEN": "",
    "WEBHOOK_SECRET": "",
    "TELEGRAM_WEBHOOK_SECRET": "farm-webhook-secret",
    "YOOKASSA_SHOP_ID": "",
    "YOOKASSA_SECRET_KEY": "",
    "SENTRY_DSN": "",
    "LOG_LEVEL": "WARNING",
}})


def _seed_mod():
    import importlib.util
    path = {fixture!r}
    if not path:
        return None
    spec = importlib.util.spec_from_file_location("farm_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.seed


async def _start_webhook(dp, bot):
    # Универсально поднимаем webhook-сервер бота, не заглядывая в его код.
    # Контракт модуля бота — один из двух:
    #   * build_webhook_app(dp, bot, secret) -> aiohttp.Application (legacy/newgen);
    #   * app (ASGI) + set_webhook_dispatcher(dp) (например, FastAPI-бот).
    # Возвращаем URL для POST и функцию остановки сервера. Нет ни одного контракта —
    # говорим прямо, а не падаем трейсбеком из недр фреймворка.
    import importlib

    from aiohttp import web

    secret = "farm-webhook-secret"
    module = None
    for name in ("src.core.webhook", "src.core.tgwebhook"):
        try:
            module = importlib.import_module(name)
            break
        except ImportError:
            module = None
    if module is None:
        raise RuntimeError(
            "бот не поддерживает webhook: нет модуля src.core.webhook "
            "или src.core.tgwebhook с точкой сборки"
        )

    factory = getattr(module, "build_webhook_app", None)
    if factory is not None:
        app = factory(dp, bot, secret)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", WEBHOOK_PORT).start()

        async def stop_aiohttp():
            await runner.cleanup()

        return "http://127.0.0.1:%d/webhook" % WEBHOOK_PORT, stop_aiohttp

    setter = getattr(module, "set_webhook_dispatcher", None)
    asgi = getattr(module, "app", None)
    if setter is not None and asgi is not None:
        setter(dp)
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(asgi, host="127.0.0.1", port=WEBHOOK_PORT, log_level="warning")
        )
        task = asyncio.create_task(server.serve())
        for _ in range(400):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            server.should_exit = True
            raise RuntimeError("webhook-сервер бота не поднялся за 20 с")
        token_id = os.environ.get("TELEGRAM_BOT_TOKEN", "424242").split(":")[0]

        async def stop_asgi():
            server.should_exit = True
            await task

        return "http://127.0.0.1:%d/webhook/%s" % (WEBHOOK_PORT, token_id), stop_asgi

    raise RuntimeError(
        "webhook-модуль бота не предоставляет ни build_webhook_app, "
        "ни set_webhook_dispatcher+app"
    )


async def _wait_webhook(rec, before, quiet=0.2, cap=2.0):
    # Webhook-сервер бота отвечает клиенту сразу, а обработку ведёт в фоне
    # (aiogram SimpleRequestHandler по умолчанию handle_in_background=True).
    # Поэтому ждём, пока поток вызовов API устоится: иначе прочитаем ответы
    # раньше, чем бот их сделает, и решим, что он промолчал.
    import time

    started = time.monotonic()
    last = len(rec.calls)
    calm = started
    while time.monotonic() - started < cap:
        await asyncio.sleep(0.05)
        count = len(rec.calls)
        if count != last:
            last = count
            calm = time.monotonic()
        if count > before and time.monotonic() - calm >= quiet:
            break


async def main():
    rec = fake_api.Recorder()
    runner = await fake_api.serve(rec, port=PORT)
    bot = None
    use_webhook = MODE == "webhook"
    webhook_url = ""
    webhook_stop = None
    try:
        # Фабрику ищем по ссылке из кейса — встроенный адаптер это тоже
        # `botkit_farm.adapters.<имя>`. Проверка результата живёт в контракте:
        # адаптер, вернувший None, падает здесь, с понятным текстом, а не
        # позже на `dp.feed_update`.
        build = adapter_contract.load_builder(ADAPTER_MODULE, ADAPTER_ATTR)
        seeder = _seed_mod()
        built = adapter_contract.check(
            await build(bot_dir=__import__("pathlib").Path(BOT_DIR),
                        key="run", port=PORT, seed=seeder)
        )
        dp, bot = built.dispatcher, built.bot
        # Адаптер обязан не менять токен. Проверяем это после сборки, а не
        # только до: сторонний адаптер — код, который мы не писали.
        security.assert_fake_token()
        if use_webhook:
            webhook_url, webhook_stop = await _start_webhook(dp, bot)
        turns = []
        known = {{}}
        last_buttons = []
        uid = 1
        for step in json.load(open({steps_file!r}, encoding="utf-8")):
            if step.get("pause"):
                await asyncio.sleep(float(step["pause"]))
            elif step.get("gap"):
                await asyncio.sleep(float(step["gap"]))
            if step["kind"] == "msg":
                # Определить актёра и чат для шага (ТЗ C3)
                role = step.get("as")
                actor_cfg = ACTORS.get(role) if role else None
                actor = None
                if actor_cfg:
                    from botkit_farm.scene import Actor
                    actor = Actor(user_id=actor_cfg["user_id"], name=actor_cfg.get("name"), is_bot=actor_cfg.get("is_bot", False))
                chat_cfg = CHAT if CHAT else None
                chat = None
                if chat_cfg:
                    from botkit_farm.scene import ChatConfig
                    chat = ChatConfig(id=scene.FAKE_CHAT_ID, type=chat_cfg.get("type", "private"), title=chat_cfg.get("title"), thread_id=chat_cfg.get("thread_id"), bot_username=chat_cfg.get("bot_username"))
                update = scene.to_message(step["value"], uid, actor=actor, chat=chat)
            elif step["kind"] == "pick":
                # Определить актёра и чат для шага (ТЗ C3)
                role = step.get("as")
                actor_cfg = ACTORS.get(role) if role else None
                actor = None
                if actor_cfg:
                    from botkit_farm.scene import Actor
                    actor = Actor(user_id=actor_cfg["user_id"], name=actor_cfg.get("name"), is_bot=actor_cfg.get("is_bot", False))
                chat_cfg = CHAT if CHAT else None
                chat = None
                if chat_cfg:
                    from botkit_farm.scene import ChatConfig
                    chat = ChatConfig(id=scene.FAKE_CHAT_ID, type=chat_cfg.get("type", "private"), title=chat_cfg.get("title"), thread_id=chat_cfg.get("thread_id"), bot_username=chat_cfg.get("bot_username"))
                # Кнопка по позиции: бот сам сообщает, что он предлагает (даты,
                # слоты, идентификаторы), и зашивать это в кейс нельзя.
                last_btns = last_buttons or []
                if not last_btns:
                    turns.append({{"kind": "no_button", "label": step["label"],
                                   "known": []}})
                    uid += 1
                    continue
                idx = step["value"]
                pos = 0 if idx == "first" else (
                    len(last_btns) - 1 if idx == "last" else int(idx))
                if not 0 <= pos < len(last_btns):
                    turns.append({{"kind": "no_button", "label": step["label"],
                                   "known": last_btns}})
                    uid += 1
                    continue
                update = scene.to_callback(last_btns[pos], uid)
            else:
                data = step["value"]
                # Определить актёра и чат для шага (ТЗ C3)
                role = step.get("as")
                actor_cfg = ACTORS.get(role) if role else None
                actor = None
                if actor_cfg:
                    from botkit_farm.scene import Actor
                    actor = Actor(user_id=actor_cfg["user_id"], name=actor_cfg.get("name"), is_bot=actor_cfg.get("is_bot", False))
                chat_cfg = CHAT if CHAT else None
                chat = None
                if chat_cfg:
                    from botkit_farm.scene import ChatConfig
                    chat = ChatConfig(id=scene.FAKE_CHAT_ID, type=chat_cfg.get("type", "private"), title=chat_cfg.get("title"), thread_id=chat_cfg.get("thread_id"), bot_username=chat_cfg.get("bot_username"))
                cb = None
                for k, v in known.items():
                    if v == data:
                        cb = k
                        break
                if cb is None:
                    turns.append({{"kind": "no_button", "label": data,
                                   "known": sorted(set(known.values()))[:50]}})
                    uid += 1
                    continue
                update = scene.to_callback(cb, uid, actor=actor, chat=chat)
            uid += 1
            before = len(rec.calls)
            try:
                if use_webhook:
                    # Webhook-режим: POST с сырым JSON на настоящий HTTP-сервер
                    # бота — запрос проходит его middleware-стек, как в бою.
                    # Клиент — aiohttp: он есть и у фермы, и у любого aiogram-бота.
                    import aiohttp

                    headers = {{"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "farm-webhook-secret"}}
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            webhook_url,
                            json=scene.to_raw_update(update),
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status >= 400:
                                body = (await resp.text())[:200]
                                raise RuntimeError("webhook вернул %d: %s" % (resp.status, body))
                    await _wait_webhook(rec, before)
                elif RAW:
                    # Сырой режим: бот сам разбирает JSON (feed_raw_update →
                    # Update.model_validate), как при webhook. Тот же Update, но
                    # пройденный через сериализацию, — проверяется десериализация.
                    await dp.feed_raw_update(bot, scene.to_raw_update(update))
                else:
                    await dp.feed_update(bot, update)
            except Exception as exc:
                turns.append({{"kind": "crash", "error": type(exc).__name__ + ": " + str(exc)}})
                break
            fresh = rec.calls[before:]
            for c in fresh:
                for row in (c.payload.get("reply_markup") or {{}}).get("inline_keyboard") or []:
                    for btn in row:
                        if btn.get("callback_data") and btn.get("text"):
                            known[btn["callback_data"]] = btn["text"]
            spoken = [c for c in fresh if c.method in ("sendMessage", "editMessageText")]
            # Всплывающее уведомление (answerCallbackQuery) клиент в Telegram
            # видит так же, как сообщение, поэтому оно идёт в replies.
            # Раньше оно молча терялось: бот «отвечал», а кейс писал «бот молчал» —
            # и это была бы ложь в отчёте.
            toasts = [c.payload.get("text", "") for c in fresh
                      if c.method == "answerCallbackQuery"
                      and c.payload.get("text")]
            last_buttons = [b.get("callback_data", "") for c in spoken
                            for row in (c.payload.get("reply_markup") or {{}}).get("inline_keyboard") or []
                            for b in row if b.get("callback_data")]
            media = []
            for c in fresh:
                m = fake_api.media_of(c)
                if m is not None:
                    media.append({{"kind": m[0], "file_id": m[1], "caption": m[2]}})
            turns.append({{"kind": "step", "text": step["label"],
                           "replies": [c.payload.get("text", "") for c in spoken] + toasts,
                           "messages": [c.payload.get("text", "") for c in spoken],
                           "toasts": toasts,
                           "media": media,
                           "buttons": [b.get("text", "")
                                       for c in spoken
                                       for row in (c.payload.get("reply_markup") or {{}}).get("inline_keyboard") or []
                                       for b in row]}})
        json.dump(turns, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False)
    finally:
        if webhook_stop is not None:
            try:
                await webhook_stop()
            except Exception:
                pass
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass
        await runner.cleanup()
        # Нарушения пишутся последними и с карантином, снятым: иначе отчёт сам
        # оказался бы запрещённой записью, а падение на этом — потеря причины.
        GUARD.disarm()
        with open(VIOLATIONS, "w", encoding="utf-8") as fh:
            json.dump(GUARD.summary(), fh, ensure_ascii=False)


asyncio.run(main())
"""


@dataclass
class ExecResult:
    """Что вернул подпроцесс.

    `turns` типизированы контрактом: разбор JSON — это и есть проверка границы
    хост↔движок, и он делается один раз здесь, а не размазывается по движку.

    `violations` — чем карантин остановил бота. Пустой список не «всё хорошо»:
    бот мог упасть раньше, чем что-либо попытался сделать.
    """

    turns: list[HostTurn]
    error: str = ""
    violations: list[str] = field(default_factory=list)


def _contract_error(exc: ValidationError) -> str:
    """Понятная причина нарушения контракта: имя поля и что случилось."""
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "<корень>"
    return f"транскрипция хоста не соответствует контракту: поле {loc}: {first['msg']}"


def run_bot(
    *,
    farm_src: pathlib.Path,
    bot_python: pathlib.Path,
    bot_dir: pathlib.Path,
    adapter_module: str,
    adapter_attr: str,
    raw: bool,
    mode: str,
    port: int,
    fixture: pathlib.Path | None,
    steps: list[dict[str, object]],
    db_path: pathlib.Path,
    webhook_port: int | None = None,
    actors: dict[str, object] | None = None,
    chat: dict[str, object] | None = None,
    redis_url: str = "redis://127.0.0.1:6380/0",
    timeout: int = 300,
    env_root: pathlib.Path | None = None,
) -> ExecResult:
    """Запустить бота в его окружении и вернуть транскрипцию.

    Сеть на время запуска закрыта: бот ходит только на фейковый API и Redis,
    оба на loopback.

    При `BOTKIT_FARM_SANDBOX=1` процесс дополнительно оборачивается в
    bubblewrap: в namespace попадают только каталоги прогона, поэтому код бота
    физически не увидит ни `$HOME`, ни чужих репозиториев.
    """
    with tempfile.TemporaryDirectory(prefix="farm-exec-") as tmp:
        tmpdir = pathlib.Path(tmp)
        # Порт webhook-сервера бота обязан отличаться от порта подделки API:
        # оба слушают loopback в одном процессе, и совпадение — коллизия.
        resolved_webhook_port = webhook_port if webhook_port is not None else port + 1
        steps_file = tmpdir / "steps.json"
        result_file = tmpdir / "result.json"
        # Каталог прогона — корень карантина: хост пишет сюда свои файлы, и
        # делать это он должен иметь право.
        violations_file = tmpdir / "violations.json"
        steps_file.write_text(json.dumps(steps, ensure_ascii=False), encoding="utf-8")

        host = _HOST.format(
            farm_src=str(farm_src),
            adapter_module=adapter_module,
            adapter_attr=adapter_attr,
            raw=raw,
            mode=mode,
            bot_dir=str(bot_dir),
            port=port,
            webhook_port=resolved_webhook_port,
            db_path=str(db_path),
            redis_url=redis_url,
            fixture=str(fixture) if fixture else "",
            steps_file=str(steps_file),
            result=str(result_file),
            violations=str(violations_file),
            # Объекты, а не их JSON: шаблон вставляет значения через `!r`, и
            # строка `"null"` превратилась бы в литерал 'null' — truthy, из-за
            # чего `chat_cfg.get(...)` падал на каждом кейсе.
            actors=actors or {},
            chat=chat,
        )
        host_file = tmpdir / "host.py"
        host_file.write_text(host, encoding="utf-8")

        env = {**os.environ, "PYTHONPATH": str(farm_src)}
        env.pop("TELEGRAM_BOT_TOKEN", None)  # токен ставит стенд, не наследуем

        # Под песочницей видны только каталоги прогона: код бота, его venv, база,
        # протокол хоста и исходники фермы (из них хост импортирует подделку API).
        binds = [bot_dir, db_path.parent, tmpdir, farm_src]
        if env_root is not None:
            binds.append(env_root)
        cmd = sandbox.wrap_or_passthrough(
            [str(bot_python), str(host_file)], binds=binds, chdir=bot_dir
        )

        security.block_egress()
        try:
            proc = subprocess.run(  # noqa: S603 — интерпретатор и скрипт свои
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=str(farm_src.parent),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                turns=[],
                error=f"бот не ответил за {timeout} с — вероятно, ушёл в цикл или ждёт сети",
            )
        finally:
            security.allow_egress()

        violations = _read_violations(violations_file)

        if not result_file.exists():
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = "\n".join(err[-12:]) if err else "(пустой вывод)"
            return ExecResult(turns=[], error=f"бот не отработал:\n{tail}", violations=violations)

        raw = json.loads(result_file.read_text(encoding="utf-8"))
        try:
            turns = parse_turns(raw)
        except ValidationError as exc:
            return ExecResult(turns=[], error=_contract_error(exc), violations=violations)
        return ExecResult(turns=turns, violations=violations)


def _read_violations(path: pathlib.Path) -> list[str]:
    """Что карантин запретил за прогон; пустой список, если ничего не запретил.

    Файл может не появиться: бот упал раньше, чем карантин успел что-то
    записать, — и это не ошибка чтения, а отсутствие события.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


__all__ = ["ExecResult", "run_bot"]
