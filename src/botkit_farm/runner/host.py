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
from dataclasses import dataclass
from typing import Any

from botkit_farm import security

# Скрипт-хост: поднимает бота, кормит его Update'ами и печатает результат в JSON.
_HOST = """
import asyncio, json, os, sys

# Фейковый API и сцена — из фермы, но импортируются ДО chdir бота, потому что
# после chdir в пути бота они могут перекрыться его собственным пакетом src.
sys.path.insert(0, {farm_src!r})

from botkit_farm import fake_api, scene, stand

ADAPTER = 'botkit_farm.adapters.' + {adapter!r}
BOT_DIR = {bot_dir!r}
PORT = {port!r}
RESULT = {result!r}
DB_PATH = {db_path!r}

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


async def main():
    import importlib
    rec = fake_api.Recorder()
    runner = await fake_api.serve(rec, port=PORT)
    bot = None
    try:
        mod = importlib.import_module(ADAPTER)
        seeder = _seed_mod()
        dp, bot, _db = await mod.build(bot_dir=__import__("pathlib").Path(BOT_DIR),
                                       key="run", port=PORT, seed=seeder)
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
                update = scene.to_message(step["value"], uid)
            elif step["kind"] == "pick":
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
                update = scene.to_callback(cb, uid)
            uid += 1
            before = len(rec.calls)
            try:
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
            last_buttons = [b.get("callback_data", "") for c in spoken
                            for row in (c.payload.get("reply_markup") or {{}}).get("inline_keyboard") or []
                            for b in row if b.get("callback_data")]
            turns.append({{"kind": "step", "text": step["label"],
                           "replies": [c.payload.get("text", "") for c in spoken],
                           "buttons": [b.get("text", "")
                                       for c in spoken
                                       for row in (c.payload.get("reply_markup") or {{}}).get("inline_keyboard") or []
                                       for b in row]}})
        json.dump(turns, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False)
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass
        await runner.cleanup()


asyncio.run(main())
"""


@dataclass
class ExecResult:
    """Что вернул подпроцесс."""

    turns: list[dict[str, Any]]
    error: str = ""


def run_bot(
    *,
    farm_src: pathlib.Path,
    bot_python: pathlib.Path,
    bot_dir: pathlib.Path,
    adapter: str,
    port: int,
    fixture: pathlib.Path | None,
    steps: list[dict[str, Any]],
    db_path: pathlib.Path,
    redis_url: str = "redis://127.0.0.1:6380/0",
    timeout: int = 300,
) -> ExecResult:
    """Запустить бота в его окружении и вернуть транскрипцию.

    Сеть на время запуска закрыта: бот ходит только на фейковый API и Redis,
    оба на loopback.
    """
    with tempfile.TemporaryDirectory(prefix="farm-exec-") as tmp:
        tmpdir = pathlib.Path(tmp)
        steps_file = tmpdir / "steps.json"
        result_file = tmpdir / "result.json"
        steps_file.write_text(json.dumps(steps, ensure_ascii=False), encoding="utf-8")

        host = _HOST.format(
            farm_src=str(farm_src),
            adapter=adapter,
            bot_dir=str(bot_dir),
            port=port,
            db_path=str(db_path),
            redis_url=redis_url,
            fixture=str(fixture) if fixture else "",
            steps_file=str(steps_file),
            result=str(result_file),
        )
        host_file = tmpdir / "host.py"
        host_file.write_text(host, encoding="utf-8")

        env = {**os.environ, "PYTHONPATH": str(farm_src)}
        env.pop("TELEGRAM_BOT_TOKEN", None)  # токен ставит стенд, не наследуем

        security.block_egress()
        try:
            proc = subprocess.run(  # noqa: S603 — интерпретатор и скрипт свои
                [str(bot_python), str(host_file)],
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

        if not result_file.exists():
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = "\n".join(err[-12:]) if err else "(пустой вывод)"
            return ExecResult(turns=[], error=f"бот не отработал:\n{tail}")

        return ExecResult(turns=json.loads(result_file.read_text(encoding="utf-8")))


__all__ = ["ExecResult", "run_bot"]
