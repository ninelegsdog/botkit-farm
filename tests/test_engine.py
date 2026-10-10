"""Проверки движка: сверка ожиданий и разбор транскрипции.

Полный прогон с живым ботом помечен `slow` и требует Redis и клона; здесь
проверяется то, что можно проверить без бота, — логика сверки. Она обязана
отличать «бот ответил не тем» от «бот не ответил», иначе баг-репорт врёт.
"""

from __future__ import annotations

from typing import Any

import pytest

from botkit_farm.case.model import Case, Expect
from botkit_farm.runner import engine
from botkit_farm.runner.contract import parse_turns
from botkit_farm.runner.result import CaseResult, Status


def _turns(*raw: dict[str, Any]) -> list[Any]:
    """Собрать транскрипцию через настоящий контракт, а не подделку-словарь."""
    return parse_turns(list(raw))


def _case(*steps: Any, **kw: Any) -> Case:
    payload = {
        "id": "test-case",
        "title": "Тестовый кейс",
        "source": ".",
        "steps": list(steps) or [{"send": "/start", "expect": {"silent": True}}],
    }
    payload.update(kw)
    return Case.model_validate(payload)


def _check(
    exp: dict[str, Any], replies: list[str], buttons: list[str], idx: int = 0, label: str = "шаг"
) -> list[Any]:
    return engine._check(Expect.model_validate(exp), idx, label, replies, buttons)


# ── text_contains ───────────────────────────────────────────────────────────


def test_совпавший_текст_не_даёт_ошибок():
    # Регистр значим: «Запись» и «запись» — разные строки, и подмена одного на
    # другой была бы недоказанным «успехом».
    assert not _check({"text_contains": ["Запись"]}, ["Запись создана"], [])


def test_отсутствующий_текст_даёт_ошибку():
    fails = _check({"text_contains": ["Запись"]}, ["Добро пожаловать"], [])
    assert len(fails) == 1
    assert fails[0].kind == "text_missing"
    assert "Добро пожаловать" in fails[0].actual


# ── text_equals ─────────────────────────────────────────────────────────────


def test_точное_совпадение_проходит():
    assert not _check({"text_equals": "Готово"}, ["  Готово  "], [])


def test_точное_несовпадение_даёт_ошибку():
    fails = _check({"text_equals": "Готово"}, ["Готово!"], [])
    assert [f.kind for f in fails] == ["text_mismatch"]


# ── кнопки ──────────────────────────────────────────────────────────────────


def test_кнопка_найдена():
    assert not _check(
        {"buttons_contains": ["Далее"]},
        [
            "",
        ],
        ["Далее", "Назад"],
    )


def test_кнопка_отсутствует():
    fails = _check(
        {"buttons_contains": ["Купить"]},
        [
            "",
        ],
        ["Далее"],
    )
    assert [f.kind for f in fails] == ["button_missing"]
    assert "Далее" in fails[0].actual, "в отчёте должно быть видно, что бот предложил"


def test_счёт_кнопок():
    assert not _check(
        {"buttons_count": 2},
        [
            "",
        ],
        ["а", "б"],
    )
    fails = _check(
        {"buttons_count": 3},
        [
            "",
        ],
        ["а", "б"],
    )
    assert [f.kind for f in fails] == ["button_count"]


# ── silent ──────────────────────────────────────────────────────────────────


def test_silent_проходит_при_молчании():
    assert not _check({"silent": True}, [], [])


def test_silent_падает_при_ответе():
    fails = _check({"silent": True}, ["Бот ответил"], [])
    assert [f.kind for f in fails] == ["not_silent"]


# ── max_len ─────────────────────────────────────────────────────────────────


def test_слишком_длинный_ответ_ловится():
    """Защита от бота, ушедшего в цикл печати: он ответит, но неконечно."""
    fails = _check({"max_len": 10}, ["х" * 500], [])
    assert [f.kind for f in fails] == ["too_long"]


# ── несколько ошибок сразу ──────────────────────────────────────────────────


def test_несколько_несовпадений_собираются():
    fails = _check({"text_contains": ["а", "б"], "buttons_contains": ["в"]}, ["ничего"], ["г"])
    assert {f.kind for f in fails} == {"text_missing", "button_missing"}
    assert len(fails) == 3


# ── разбор транскрипции ─────────────────────────────────────────────────────


def _result() -> CaseResult:
    return CaseResult(case_id="c", title="t", status=Status.PASSED, steps_total=3)


def _turn(
    text: str,
    replies: list[str],
    buttons: list[str] | None = None,
    toasts: list[str] | None = None,
) -> dict[str, Any]:
    """Повторяет формат настоящего хоста: replies (всё), messages и toasts."""
    toasts = toasts or []
    messages = [r for r in replies if r not in toasts]
    return {
        "kind": "step",
        "text": text,
        "replies": replies,
        "messages": messages,
        "toasts": toasts,
        "buttons": buttons or [],
    }


def test_все_ожидания_сошлись():
    case = _case(
        {"send": "/start", "expect": {"text_contains": ["Запись"]}},
        {"click": "Далее", "expect": {"buttons_contains": ["Готово"]}},
    )
    turns = _turns(
        _turn("написать «/start»", ["Запись"]), _turn("нажать «Далее»", ["Ок"], ["Готово"])
    )
    res = _result()
    assert engine._consume(turns, case, res) is Status.PASSED
    assert res.ok


def test_несовпадение_даёт_failed():
    case = _case({"send": "/start", "expect": {"text_contains": ["Запись"]}})
    res = _result()
    assert engine._consume(_turns(_turn("x", ["Другое"])), case, res) is Status.FAILED
    assert res.status is Status.FAILED
    assert res.failures


def test_падение_бота_даёт_errored():
    case = _case({"send": "/start", "expect": {"text_contains": ["x"]}})
    res = _result()
    turns = _turns({"kind": "crash", "error": "AttributeError: NoneType"})
    assert engine._consume(turns, case, res) is Status.ERRORED
    assert "AttributeError" in (res.error or "")


def test_отсутствующая_кнопка_даёт_подсказку():
    case = _case({"click": "Купить", "expect": {"buttons_contains": ["Ок"]}})
    res = _result()
    turns = _turns({"kind": "no_button", "label": "нажать «Купить»", "known": ["Ок", "Далее"]})
    assert engine._consume(turns, case, res) is Status.FAILED
    assert "Далее" in res.failures[0].actual


def test_optional_шаг_не_роняет_кейс():
    """optional означает «шаг может не совпасть без провала кейса»."""
    case = _case(
        {"click": "Купить", "optional": True, "expect": {"text_contains": ["x"]}},
        {"send": "/done", "expect": {"text_contains": ["Готово"]}},
    )
    res = _result()
    turns = _turns(_turn("нажать «Купить»", ["Другое"]), _turn("написать «/done»", ["Готово"]))
    assert engine._consume(turns, case, res) is Status.PASSED
    assert res.failures, "несовпадение должно быть записано, даже если шаг optional"


def test_диалог_собирается_для_кадра():
    case = _case({"send": "/start", "expect": {"silent": True}})
    res = _result()
    engine._consume(_turns(_turn("написать «/start»", ["Привет"], ["Далее"])), case, res)
    kinds = [t.out for t in res.turns]
    assert kinds == [True, False], "в кадре нужны и реплика клиента, и ответ бота"
    assert res.turns[1].buttons == ["Далее"]


# ── выбор адаптера ──────────────────────────────────────────────────────────


def test_builder_из_кейса_используется(tmp_path):
    case = _case(builder="newgen")
    assert engine._adapter_for(case, tmp_path) == "newgen"


def test_неизвестный_builder_отвергается(tmp_path):
    with pytest.raises(engine.RunError):
        engine._adapter_for(_case(), tmp_path)


# ── всплывающие уведомления (answerCallbackQuery) ──────────────────────────


def test_тост_попадает_в_кадр_отдельно():
    """answerCallbackQuery — это ответ бота, который клиент видит. Раньше он
    молча терялся: кейс писал «бот промолчал», и это была ложь в отчёте."""
    case = _case({"click": "В корзину", "expect": {"text_contains": ["Добавлено"]}})
    res = _result()
    turns = _turns(
        {
            "kind": "step",
            "text": "нажать",
            "replies": ["Добавлено в корзину!"],
            "messages": [],
            "toasts": ["Добавлено в корзину!"],
        }
    )
    assert engine._consume(turns, case, res) is Status.PASSED
    assert [t.is_toast for t in res.turns] == [False, True], "тост должен быть помечен"


def test_тост_не_дублируется_как_сообщение():
    """Регресс: тост и сообщение рисовались двумя репликами с одинаковым текстом.
    В кадре это выглядит как «бот ответил дважды»."""
    case = _case({"click": "В корзину", "expect": {"text_contains": ["Добавлено"]}})
    res = _result()
    turns = _turns(
        {
            "kind": "step",
            "text": "нажать",
            "replies": ["Добавлено в корзину!"],
            "messages": [],
            "toasts": ["Добавлено в корзину!"],
        }
    )
    engine._consume(turns, case, res)
    bot_texts = [t.text for t in res.turns if not t.out]
    assert bot_texts == ["Добавлено в корзину!"], f"тост продублирован: {bot_texts}"


def test_сообщение_и_тост_вместе_рисуются_оба():
    case = _case({"click": "Далее", "expect": {"text_contains": ["Ок"]}})
    res = _result()
    turns = _turns(
        {
            "kind": "step",
            "text": "нажать",
            "replies": ["Заказ создан", "Сохранено"],
            "messages": ["Заказ создан"],
            "toasts": ["Сохранено"],
        }
    )
    engine._consume(turns, case, res)
    texts = [t.text for t in res.turns if not t.out]
    assert texts == ["Сохранено", "Заказ создан"]


def test_пустой_ответ_не_рисуется():
    case = _case({"send": "ping", "expect": {"silent": True}})
    res = _result()
    turns = _turns({"kind": "step", "text": "ping", "replies": [], "messages": [], "toasts": []})
    assert engine._consume(turns, case, res) is Status.PASSED
    assert len(res.turns) == 1, "ответов не было — рисуется только реплика клиента"


def test_старый_хост_без_messages_не_ломает_как():
    """Хост ранних версий не отдаёт поле messages — тогда берём последний ответ
    из replies, и прогон не падает на несовпадении формата."""
    case = _case({"send": "ping", "expect": {"text_contains": ["Ок"]}})
    res = _result()
    turns = _turns({"kind": "step", "text": "ping", "replies": ["Ок"]})
    assert engine._consume(turns, case, res) is Status.PASSED
    assert any(t.text == "Ок" for t in res.turns)
