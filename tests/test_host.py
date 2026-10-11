"""Тесты границы хост↔движок: контракт транскрипции и чтение протокола карантина.

`run_bot` без настоящего бота не запустить, но читать то, что он оставил, —
можно и нужно: именно эти файлы читает движок, когда хост упал.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

from botkit_farm.runner import host


def _write(path: pathlib.Path, payload: object) -> pathlib.Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_нарушения_читаются_списком(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path / "violations.json", ["open → /home/algtro/.ssh/x"])

    assert host._read_violations(path) == ["open → /home/algtro/.ssh/x"]


def test_отсутствующий_файл_это_отсутствие_события(tmp_path: pathlib.Path) -> None:
    """Бот мог упасть раньше, чем карантин что-то записал. Это не ошибка."""
    assert host._read_violations(tmp_path / "нет.json") == []


def test_битый_json_не_роняет_прогон(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "violations.json"
    path.write_text("{не json", encoding="utf-8")

    assert host._read_violations(path) == []


def test_не_список_игнорируется(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path / "violations.json", {"open": "/home/x"})

    assert host._read_violations(path) == []


def test_пустой_список_остаётся_пустым(tmp_path: pathlib.Path) -> None:
    assert host._read_violations(_write(tmp_path / "violations.json", [])) == []


def test_результат_хоста_по_умолчанию_без_нарушений() -> None:
    assert host.ExecResult(turns=[]).violations == []


def test_нарушения_не_делятся_между_результатами(tmp_path: pathlib.Path) -> None:
    """Список по умолчанию должен быть свой у каждого: общий бы тихо собирал
    нарушения из чужих прогонов."""
    first = host.ExecResult(turns=[])
    second = host.ExecResult(turns=[])
    first.violations.append("open → /home/x")

    assert second.violations == []


def test_хост_получает_модуль_и_фабрику_адаптера(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Ссылка на сторонний адаптер уходит в подпроцесс разобранной.

    Скрипт-хост склеивает модуль с префиксом фермы сам; если движок передаст
    сюда `spec` вместо `module`, сторонний адаптер не найдётся. Здесь это
    ловится без запуска настоящего бота: подпроцесс подменён.
    """
    seen: dict[str, object] = {}
    monkeypatch.delenv("BOTKIT_FARM_SANDBOX", raising=False)

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        seen["cmd"] = cmd
        script = pathlib.Path(cmd[-1])
        seen["script"] = script.read_text(encoding="utf-8")
        (script.parent / "result.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host.subprocess, "run", fake_run)

    res = host.run_bot(
        farm_src=tmp_path,
        bot_python=tmp_path / "py",
        bot_dir=tmp_path / "bot",
        adapter_module="mypkg.mine",
        adapter_attr="make",
        raw=False,
        mode="polling",
        port=8100,
        fixture=None,
        steps=[],
        db_path=tmp_path / "b.db",
    )

    assert "ADAPTER_MODULE = 'mypkg.mine'" in seen["script"]
    assert "ADAPTER_ATTR = 'make'" in seen["script"]
    assert "RAW = False" in seen["script"]
    assert res.error == ""
    assert res.turns == []


def test_сырой_режим_включается_в_скрипте_хоста(tmp_path: pathlib.Path, monkeypatch) -> None:
    """`raw` из кейса доходит до скрипта и включает разбор JSON, а не объекта."""
    seen: dict[str, object] = {}
    monkeypatch.delenv("BOTKIT_FARM_SANDBOX", raising=False)

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        script = pathlib.Path(cmd[-1])
        seen["script"] = script.read_text(encoding="utf-8")
        (script.parent / "result.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host.subprocess, "run", fake_run)

    host.run_bot(
        farm_src=tmp_path,
        bot_python=tmp_path / "py",
        bot_dir=tmp_path / "bot",
        adapter_module="botkit_farm.adapters.newgen",
        adapter_attr="build",
        raw=True,
        mode="polling",
        port=8101,
        fixture=None,
        steps=[],
        db_path=tmp_path / "b.db",
    )

    assert "RAW = True" in seen["script"]
    assert "feed_raw_update" in seen["script"]


def test_webhook_режим_виден_в_скрипте_хоста(tmp_path: pathlib.Path, monkeypatch) -> None:
    """`mode: webhook` доходит до скрипта и переключает его на HTTP-сервер бота."""
    seen: dict[str, object] = {}
    monkeypatch.delenv("BOTKIT_FARM_SANDBOX", raising=False)

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        script = pathlib.Path(cmd[-1])
        seen["script"] = script.read_text(encoding="utf-8")
        (script.parent / "result.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host.subprocess, "run", fake_run)

    host.run_bot(
        farm_src=tmp_path,
        bot_python=tmp_path / "py",
        bot_dir=tmp_path / "bot",
        adapter_module="botkit_farm.adapters.newgen",
        adapter_attr="build",
        raw=False,
        mode="webhook",
        port=8100,
        webhook_port=8700,
        fixture=None,
        steps=[],
        db_path=tmp_path / "b.db",
    )

    script = seen["script"]
    assert "MODE = 'webhook'" in script
    assert "WEBHOOK_PORT = 8700" in script
    assert "build_webhook_app" in script
    assert "127.0.0.1:%d/webhook" in script


def test_webhook_клиент_не_тянет_httpx(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Клиент — aiohttp (есть у любого aiogram-бота), а не httpx/uvicorn."""
    seen: dict[str, object] = {}
    monkeypatch.delenv("BOTKIT_FARM_SANDBOX", raising=False)

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        script = pathlib.Path(cmd[-1])
        seen["script"] = script.read_text(encoding="utf-8")
        (script.parent / "result.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host.subprocess, "run", fake_run)

    host.run_bot(
        farm_src=tmp_path,
        bot_python=tmp_path / "py",
        bot_dir=tmp_path / "bot",
        adapter_module="botkit_farm.adapters.newgen",
        adapter_attr="build",
        raw=False,
        mode="polling",
        port=8100,
        fixture=None,
        steps=[],
        db_path=tmp_path / "b.db",
    )

    assert "httpx" not in seen["script"]


def test_порт_webhook_по_умолчанию_рядом_с_портом_api(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Если порт webhook не задан, он не совпадает с портом подделки API."""
    seen: dict[str, object] = {}
    monkeypatch.delenv("BOTKIT_FARM_SANDBOX", raising=False)

    def fake_run(cmd: list[str], **kw: object) -> SimpleNamespace:
        script = pathlib.Path(cmd[-1])
        seen["script"] = script.read_text(encoding="utf-8")
        (script.parent / "result.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host.subprocess, "run", fake_run)

    host.run_bot(
        farm_src=tmp_path,
        bot_python=tmp_path / "py",
        bot_dir=tmp_path / "bot",
        adapter_module="botkit_farm.adapters.newgen",
        adapter_attr="build",
        raw=False,
        mode="polling",
        port=8100,
        fixture=None,
        steps=[],
        db_path=tmp_path / "b.db",
    )

    assert "PORT = 8100" in seen["script"]
    assert "WEBHOOK_PORT = 8101" in seen["script"]
