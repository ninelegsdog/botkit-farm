"""Проверки модели и загрузки кейсов.

Модель — контракт между человеком и раннером, и большинство её проверок
отрицательные: «этот кейс обязан быть отвергнут». Положительных случаев мало
потому, что хороший кейс — это просто YAML без сюрпризов.
"""

from __future__ import annotations

import pathlib

import pytest

from botkit_farm.case.loader import CaseError, load_dir, load_file
from botkit_farm.case.model import Case

GOOD = """
id: booking-start
title: Стартовое меню
source: ./bots/botkit-bookingbot
steps:
  - send: /start
    expect:
      text_contains: ["Запись"]
"""


def _write(tmp_path: pathlib.Path, text: str, name: str = "c.yaml") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── модель ──────────────────────────────────────────────────────────────────


def test_хороший_кейс_читается(tmp_path):
    case = load_file(_write(tmp_path, GOOD))
    assert case.id == "booking-start"
    assert len(case.steps) == 1


def test_пустое_ожидание_отвергается():
    """Кейс без ожиданий не упадёт, а значит, ничего не проверяет.

    Шаг с пустым expect:{} ловится на уровне самого ожидания, кейс целиком без
    ожиданий — на уровне Case (см. следующий тест).
    """
    with pytest.raises(ValueError, match="ожидание пустое"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Пустое ожидание",
                "source": ".",
                "steps": [{"send": "/start", "expect": {}}],
            }
        )


def test_лишнее_поле_отвергается():
    """Опечатка в поле должна падать, а не молча выключать проверку."""
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Опечатка",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
                "expct": ["опечатка"],
            }
        )


def test_оба_действия_в_шаге_отвергаются():
    with pytest.raises(ValueError, match="ровно один из send, click, pick"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Оба",
                "source": ".",
                "steps": [{"send": "/start", "click": "Далее", "expect": {"silent": True}}],
            }
        )


def test_ни_одного_действия_отвергается():
    with pytest.raises(ValueError, match="ровно один из send, click, pick"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Пусто",
                "source": ".",
                "steps": [{"expect": {"silent": True}}],
            }
        )


def test_equals_и_contains_вместе_отвергаются():
    with pytest.raises(ValueError, match="text_equals и text_contains"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "И то и другое",
                "source": ".",
                "steps": [
                    {"send": "/start", "expect": {"text_equals": "a", "text_contains": ["b"]}}
                ],
            }
        )


def test_ни_одного_ожидания_во_всем_кейсе_отвергается():
    with pytest.raises(ValueError, match="ни на одном шаге"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Пустой кейс",
                "source": ".",
                "steps": [{"send": "/start"}, {"send": "/help"}],
            }
        )


def test_неизвестный_тег_отвергается():
    with pytest.raises(ValueError, match="неизвестные теги"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Тег",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
                "tags": ["нетакого"],
            }
        )


@pytest.mark.parametrize("bad_id", ["Плохой", "../escape", "with space", "UPPER"])
def test_ид_с_небезопасными_символами_отвергается(bad_id):
    """id попадает в имя файла отчёта и путь кадра — с ../ он бы ушёл из каталога."""
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": bad_id,
                "title": "Плохой id",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
            }
        )


# ── загрузчик ───────────────────────────────────────────────────────────────


def test_незаполненный_слот_ловится_до_запуска_бота(tmp_path):
    text = GOOD.replace("Запись", "{{текст_меню}}")
    with pytest.raises(CaseError, match="незаполненные слоты"):
        load_file(_write(tmp_path, text))


def test_слово_заглушка_ловится(tmp_path):
    text = GOOD.replace("Запись", "TODO: взять из кода")
    with pytest.raises(CaseError, match="заглушки"):
        load_file(_write(tmp_path, text))


def test_битый_yaml_даёт_понятную_ошибку(tmp_path):
    with pytest.raises(CaseError, match="невалидный YAML"):
        load_file(_write(tmp_path, "id: [unclosed\n"))


def test_ошибка_валидации_называет_поле(tmp_path):
    text = GOOD.replace("source: ./bots/botkit-bookingbot", "")
    with pytest.raises(CaseError, match="поле source"):
        load_file(_write(tmp_path, text))


def test_один_битый_файл_не_мешает_остальным(tmp_path):
    (tmp_path / "a.yaml").write_text(GOOD, encoding="utf-8")
    (tmp_path / "b.yaml").write_text("id: битый\n", encoding="utf-8")
    cases, errors = load_dir(tmp_path)
    assert len(cases) == 1
    assert len(errors) == 1


def test_отсутствующий_каталог_не_травит(tmp_path):
    cases, errors = load_dir(tmp_path / "нет-такого")
    assert cases == [] and errors


# ── модель результата ───────────────────────────────────────────────────────


def test_кейс_с_одной_проверкой_и_шагом_без_ожидания_проходит():
    """Не каждый шаг обязан что-то проверять: /start может быть навигационным,
    а проверка — на следующем шаге. Требовать ожидание на каждом шаге было бы
    запретом нормального кейса.
    """
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Один шаг с проверкой",
            "source": ".",
            "steps": [
                {"send": "/start"},
                {"send": "/help", "expect": {"text_contains": ["Справка"]}},
            ],
        }
    )
    assert len(case.steps) == 2


# ── pick: кнопка по позиции ────────────────────────────────────────────────


def test_pick_принимается_вместо_click():
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Динамическая дата",
            "source": ".",
            "steps": [{"pick": "first", "expect": {"text_contains": ["Время"]}}],
        }
    )
    assert case.steps[0].pick == "first"
    assert "first" in case.steps[0].label()


@pytest.mark.parametrize("pick", ["first", "last", "0", "3"])
def test_допустимые_значения_pick(pick):
    Case.model_validate(
        {
            "id": "x-y",
            "title": "Pick",
            "source": ".",
            "steps": [{"pick": pick, "expect": {"silent": True}}],
        }
    )


@pytest.mark.parametrize("pick", ["средняя", "-1", "1.5", ""])
def test_мусорный_pick_отвергается(pick):
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Pick",
                "source": ".",
                "steps": [{"pick": pick, "expect": {"silent": True}}],
            }
        )


def test_pick_и_click_вместе_отвергаются():
    with pytest.raises(ValueError, match="ровно один"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Оба",
                "source": ".",
                "steps": [{"click": "Далее", "pick": "first", "expect": {"silent": True}}],
            }
        )
