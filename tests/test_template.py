"""Шаблоны кейсов (ТЗ C6): песочница Jinja2 и белый список переменных.

Два заслона проверяются отдельно: `check` отказывает на чтении кейса, а
`render` не должен пропустить то, что `check` пропустил (иначе кейс с
`{{ ''.__class__ }}` прошёл бы загрузку и упал SecurityError в середине прогона).
"""

from __future__ import annotations

import pytest

from botkit_farm.case import template as t


def test_обычный_текст_без_шаблона() -> None:
    assert t.has_template("/start") is False
    # Проверка и рендер обычного текста — тождественны и не гоняют парсер.
    t.check("/start", t.ACTION_VARS, "шаг 1 send")
    assert t.render("/start", {}, "шаг 1 send") == "/start"


def test_шаблон_подставляется() -> None:
    assert t.render("Товар {{ i }}", {"i": 2, "n": 5}, "send") == "Товар 2"


def test_проверка_принимает_известные_переменные() -> None:
    t.check("Код {{ turns[-1] }} ({{ i }}/{{ n }})", t.EXPECT_VARS, "шаг 1")


def test_неизвестная_переменная_названа_в_ошибке() -> None:
    with pytest.raises(t.TemplateError, match="секрет"):
        t.check("{{ секрет }}", t.ACTION_VARS, "шаг 1 send")


def test_turns_запрещён_в_действии() -> None:
    """Действие рендерится до прогона, прошлых ответов ещё нет."""
    with pytest.raises(t.TemplateError, match="turns"):
        t.check("{{ turns[-1] }}", t.ACTION_VARS, "шаг 1 send")


def test_приватный_атрибут_запрещён_на_проверке() -> None:
    with pytest.raises(t.TemplateError, match="приватн"):
        t.check("{{ ''.__class__ }}", t.ACTION_VARS, "шаг 1 send")


def test_приватный_ключ_запрещён_на_проверке() -> None:
    with pytest.raises(t.TemplateError, match="приватн"):
        t.check("{{ turns['_x'] }}", t.EXPECT_VARS, "шаг 1")


def test_синтаксис_шаблона_ловится() -> None:
    with pytest.raises(t.TemplateError, match="синтаксис"):
        t.check("{{ i ", t.ACTION_VARS, "шаг 1 send")


def test_песочница_держит_и_рендер() -> None:
    """Даже если проверку обошли, рендер не выполняет приватный код."""
    with pytest.raises(t.TemplateError):
        t.render("{{ ''.__class__ }}", {}, "шаг 1 send")


def test_рендер_неизвестной_переменной_даёт_ошибку() -> None:
    with pytest.raises(t.TemplateError, match="нет_такой"):
        t.render("{{ нет_такой }}", {"i": 0}, "шаг 1 send")


def test_turns_это_список_прошлых_ответов() -> None:
    assert t.render("{{ turns[-1] }}", {"turns": ["первый", "второй"]}, "шаг 2") == "второй"
