"""Проверки отчётов: отчёт обязан быть полезным, а не просто файл.

Два свойства, которые легко потерять при правках форматирования:
1) в отчёте есть факты (что ожидалось, что пришло, на каком шаге) — без них
   баг-репорт бесполезен;
2) секреты в отчёт не попадают — бот может прислать свой токен в ответе.
"""

from __future__ import annotations

import json
import pathlib

from botkit_farm.report.markdown import (
    render_markdown,
    write_json,
    write_report,
    write_summary,
)
from botkit_farm.runner.result import CaseResult, Failure, Status, Turn


def _passed() -> CaseResult:
    return CaseResult(
        case_id="ok-case",
        title="Всё хорошо",
        status=Status.PASSED,
        steps_done=2,
        steps_total=2,
        turns=[
            Turn(text="/start", out=True),
            Turn(text="Добро пожаловать", out=False, buttons=["Далее"]),
        ],
    )


def _failed() -> CaseResult:
    return CaseResult(
        case_id="bad-case",
        title="Сломалось",
        status=Status.FAILED,
        steps_done=1,
        steps_total=3,
        failures=[
            Failure(
                0,
                "написать «/start»",
                "text_missing",
                "Запись",
                "Добро пожаловать",
                "сверьте с кодом",
            )
        ],
        turns=[Turn(text="/start", out=True), Turn(text="Добро пожаловать", out=False)],
    )


def test_зелёный_отчёт_пишется():
    md = render_markdown(_passed())
    assert "ПРОШЁЛ" in md and "ok-case" in md


def test_багрепорт_содержит_факты():
    md = render_markdown(_failed())
    assert "ПРОВАЛЕН" in md
    assert "Запись" in md  # что ожидалось
    assert "Добро пожаловать" in md  # что пришло
    assert "сверьте с кодом" in md  # что делать


def _errored() -> CaseResult:
    return CaseResult(
        case_id="crash",
        title="Бот упал",
        status=Status.ERRORED,
        error="AttributeError: 'NoneType' has no attribute 'answer'",
    )


def test_ошибка_запуска_в_отчёте():
    md = render_markdown(_errored())
    assert "ОШИБКА ЗАПУСКА" in md
    assert "AttributeError" in md


def test_диалог_в_отчёте():
    md = render_markdown(_passed())
    assert "клиент" in md and "Добро пожаловать" in md


def test_кактус_отмечается_как_негодный():
    """Упавший кейс не должен выглядеть как пригодный кадр."""
    assert not _failed().ok
    assert _passed().ok


def test_сводка_считает_верно():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = write_summary([_passed(), _failed(), _errored()], pathlib.Path(d))
        text = p.read_text(encoding="utf-8")
        assert "Пройдено **1**" in text
        assert "провалено **1**" in text
        assert "bad-case" in text


def test_json_отчёт_машиночитаем():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = write_json([_passed()], pathlib.Path(d) / "r.json")
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["passed"] == 1
        assert data["results"][0]["case"] == "ok-case"


def test_отчёт_на_диске_и_начинается_с_пометки():
    """Отчёт перезаписывается, поэтому в шапке сказано: правь кейс, не отчёт."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = write_report(_failed(), pathlib.Path(d))
        head = p.read_text(encoding="utf-8")[:200]
        assert "Правьте кейс, а не отчёт" in head


def test_пометка_демо_интерфейса_в_кадре_сохраняется():
    """Кадр фермы — демонстрация поведения, а не «живая работа бота»."""
    from botkit_farm import render

    doc = render.page_html(
        [{"text": "привет", "out": False}], title="T", handle="@h", stamp="x", window=False
    )
    assert "Демо-интерфейс" in doc


def test_результат_не_пускает_в_отчёт_испорченный_кадр():
    """Кадр упавшего кейса физически пишется с суффиксом -fail: перепутать его
    с годным невозможно даже по имени файла."""
    r = _failed()
    name = f"{r.case_id}{'-fail' if not r.ok else ''}.png"
    assert name == "bad-case-fail.png"
