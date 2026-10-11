"""Тесты границы хост↔движок: контракт транскрипции и чтение протокола карантина.

`run_bot` без настоящего бота не запустить, но читать то, что он оставил, —
можно и нужно: именно эти файлы читает движок, когда хост упал.
"""

from __future__ import annotations

import json
import pathlib

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
