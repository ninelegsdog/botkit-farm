"""Проверки движка: сверка ожиданий и разбор транскрипции.

Полный прогон с живым ботом помечен `slow` и требует Redis и клона; здесь
проверяется то, что можно проверить без бота, — логика сверки. Она обязана
отличать «бот ответил не тем» от «бот не ответил», иначе баг-репорт врёт.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from botkit_farm.case.model import Case, Expect
from botkit_farm.runner import engine
from botkit_farm.runner.contract import Media, parse_turns
from botkit_farm.runner.result import CaseResult, Failure, Status, Turn


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


# ── точный отчёт: каждое поле каждого Failure ────────────────────────────────
#
# Отчёт о несовпадении — это интерфейс для человека, который будет чинить
# кейс: по нему он понимает, что просил, что получил и что делать. Значит текст
# и структура — контракт, а не косметика. Ниже проверка фиксирует Failure
# целиком: подмена любого поля, строки или среза делает отчёт врущим, и это
# обязано ловиться тестом.
#
# Мутационный прогон (роадмап B4) обходит эти ветки и требует именно такой
# строгости: «в отчёте где-то есть слово» мутант-строку не убивает.


def _media(kind: str, caption: str = "") -> Media:
    return Media(kind=kind, caption=caption)


def test_отчёт_о_болтливом_боте_при_silent_полный():
    got = engine._check(Expect(silent=True), 0, "шаг", ["Ответ"], [])
    assert got == [
        Failure(
            0,
            "шаг",
            "not_silent",
            "бот промолчал",
            "бот ответил: 'Ответ'",
            "уберите silent, если ответ ожидается",
        )
    ]


def test_silent_и_медиа_это_тоже_ответ():
    got = engine._check(
        Expect(silent=True), 0, "шаг", [], [], [_media("photo"), _media("document")]
    )
    assert got == [
        Failure(
            0,
            "шаг",
            "not_silent",
            "бот промолчал",
            "бот ответил: 'photo; document'",
            "уберите silent, если ответ ожидается",
        )
    ]


def test_silent_обрезка_первой_реплики_до_ста_двадцати():
    long = "х" * 121
    got = engine._check(Expect(silent=True), 0, "шаг", [long], [])
    assert got[0].actual == f"бот ответил: '{'х' * 120}'"


def test_silent_с_репликой_и_медиа_берёт_реплику():
    """При непустых replies отчёт показывает текст, а не список видов медиа."""
    got = engine._check(Expect(silent=True), 0, "шаг", ["Слово"], [], [_media("photo")])
    assert got[0].actual == "бот ответил: 'Слово'"


def test_отчёт_о_несовпадении_текста_полный():
    got = engine._check(Expect(text_equals="Готово"), 0, "шаг", ["Готово!"], [])
    assert got == [
        Failure(
            0,
            "шаг",
            "text_mismatch",
            "Готово",
            "Готово!",
            "сверьте текст с кодом бота (botkit-farm study)",
        )
    ]


def test_отчёт_о_пропавшем_тексте_полный():
    got = engine._check(Expect(text_contains=["Запись"]), 0, "шаг", ["Другое"], [])
    assert got == [
        Failure(
            0,
            "шаг",
            "text_missing",
            "Запись",
            "Другое",
            "текст должен быть в коде бота; сверьте инспектором",
        )
    ]


def test_отчёт_о_молчании_при_ожидании_текста():
    got = engine._check(Expect(text_contains=["Запись"]), 0, "шаг", [], [])
    assert got[0].actual == "(бот промолчал)"


def test_отчёт_о_кнопке_полный():
    got = engine._check(Expect(buttons_contains=["Купить"]), 0, "шаг", [""], ["Далее"])
    assert got == [
        Failure(
            0,
            "шаг",
            "button_missing",
            "Купить",
            "Далее",
            "подпись кнопки — та, что видит клиент",
        )
    ]


def test_отчёт_о_кнопке_при_их_отсутствии():
    got = engine._check(Expect(buttons_contains=["Купить"]), 0, "шаг", [""], [])
    assert got[0].actual == "(нет кнопок)"


def test_отчёт_о_счёте_кнопок_полный():
    got = engine._check(Expect(buttons_count=3), 0, "шаг", [""], ["а", "б"])
    assert got == [
        Failure(
            0,
            "шаг",
            "button_count",
            "ровно 3",
            "2: а; б",
            "счёт из кода или из ответа предыдущего шага",
        )
    ]


def test_отчёт_о_счёте_кнопок_обрезает_до_двадцати():
    btns = [f"к{i}" for i in range(21)]
    got = engine._check(Expect(buttons_count=99), 0, "шаг", [""], btns)
    assert got[0].actual == f"21: {'; '.join(btns[:20])}"


def test_отчёт_о_перечислении_кнопок_обрезает_до_двадцати():
    btns = [f"к{i}" for i in range(21)]
    got = engine._check(Expect(buttons_contains=["нет"]), 0, "шаг", [""], btns)
    assert got[0].actual == "; ".join(btns[:20])


def test_отчёт_о_длине_полный():
    got = engine._check(Expect(max_len=4), 0, "шаг", ["12345"], [])
    assert got == [
        Failure(
            0,
            "шаг",
            "too_long",
            "не длиннее 4",
            "5 символов",
            "возможен цикл печати в боте",
        )
    ]


def test_длина_ровно_в_предел_не_считается_превышением():
    """Граница: ответ ровно max_len символов — ещё не цикл печати. Мутант
    с `>=` вместо `>` должен падать здесь."""
    assert not engine._check(Expect(max_len=5), 0, "шаг", ["12345"], [])


def test_отчёт_о_медиа_виде_полный():
    got = engine._check(Expect(media_kind="photo"), 0, "шаг", [], [])
    assert got == [
        Failure(
            0,
            "шаг",
            "media_kind",
            "photo",
            "(медиа нет)",
            "сверьте метод отправки в коде бота (sendPhoto → photo)",
        )
    ]


def test_отчёт_о_медиа_виде_перечисляет_фактические():
    got = engine._check(
        Expect(media_kind="photo"), 0, "шаг", [], [], [_media("document"), _media("voice")]
    )
    assert got[0].actual == "document; voice"


def test_отчёт_о_счёте_медиа_полный():
    got = engine._check(
        Expect(media_count=3), 0, "шаг", [], [], [_media("photo"), _media("document")]
    )
    assert got[0].actual == "2: photo; document"
    assert got[0].expected == "ровно 3"


def test_отчёт_о_счёте_медиа_при_их_отсутствии():
    got = engine._check(Expect(media_count=2), 0, "шаг", [], [])
    assert got[0].actual == "0: "


def test_отчёт_о_подписи_полный():
    got = engine._check(
        Expect(caption_contains=["Прайс"]), 0, "шаг", [], [], [_media("photo", "Меню")]
    )
    assert got == [
        Failure(
            0,
            "шаг",
            "caption_missing",
            "Прайс",
            "Меню",
            "подпись к файлу — та, что видит клиент",
        )
    ]


def test_отчёт_о_подписи_перечисляет_все():
    got = engine._check(
        Expect(caption_contains=["Нет"]),
        0,
        "шаг",
        [],
        [],
        [_media("photo", "Меню"), _media("document", "Прайс")],
    )
    assert got[0].actual == "Меню; Прайс"


def test_отчёт_о_подписи_при_их_отсутствии():
    got = engine._check(Expect(caption_contains=["Прайс"]), 0, "шаг", [], [], [_media("photo")])
    assert got[0].actual == "(подписей нет)"


def test_склейка_реплик_через_перевод_строки():
    """Несколько реплик склеиваются ровно переводом строки: иначе точное
    сравнение текста сверялось бы с испорченным «склеенным» текстом."""
    assert not engine._check(Expect(text_equals="а\nб"), 0, "шаг", ["а", "б"], [])


def test_склейка_подписей_через_перевод_строки():
    media = [_media("photo", "Меню"), _media("document", "Прайс")]
    assert not engine._check(Expect(caption_contains=["Меню\nПрайс"]), 0, "шаг", [], [], media)


def test_отчёт_об_обрезаемом_актуальном_тексте():
    long = "з" * 301
    got = engine._check(Expect(text_equals="Готово"), 0, "шаг", [long], [])
    assert got[0].actual == long[:300]
    got2 = engine._check(Expect(text_contains=["нет"]), 0, "шаг", [long], [])
    assert got2[0].actual == long[:300]


# ── выбор адаптера: обе ветки и причина отказа ──────────────────────────────


def test_обнаруженный_builder_используется(tmp_path):
    """Если кейс не задал builder, движок обязан взять распознанный из кода.
    Ветка «builder найден» иначе не проверяется."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def create_app():\n    ...\n", encoding="utf-8")
    assert engine._adapter_for(_case(), tmp_path) == "newgen"


def test_неопознанный_репозиторий_объясняет_причину(tmp_path):
    with pytest.raises(engine.RunError, match="не удалось определить поколение сборки"):
        engine._adapter_for(_case(), tmp_path)


# ── разбор транскрипции: точное состояние результата ────────────────────────


def test_падение_бота_помечает_результат_errored():
    case = _case({"send": "/start", "expect": {"text_contains": ["x"]}})
    res = _result()
    assert engine._consume(_turns({"kind": "crash", "error": "boom"}), case, res) is Status.ERRORED
    assert res.status is Status.ERRORED
    assert res.error == "бот упал: boom"


def test_no_such_button_отчёт_полный():
    case = _case({"click": "Купить", "expect": {"buttons_contains": ["Ок"]}})
    res = _result()
    turns = _turns({"kind": "no_button", "label": "Купить", "known": ["Ок", "Далее"]})
    assert engine._consume(turns, case, res) is Status.FAILED
    assert res.status is Status.FAILED
    assert res.failures == [
        Failure(
            0,
            "нажать «Купить»",
            "no_such_button",
            "кнопка «Купить»",
            "бот предлагал: Ок; Далее",
            "подпись сверьте с инспектором или с ответом бота",
        )
    ]


def test_no_such_button_на_optional_шаге_индекс_шага_верен():
    """Optional-шаг не роняет кейс, но индекс в отчёте обязан указывать на
    настоящий шаг, а не на первый или выдуманный."""
    case = _case(
        {"send": "/start", "expect": {"silent": True}},
        {"click": "Купить", "optional": True, "expect": {"buttons_contains": ["Ок"]}},
    )
    res = _result()
    turns = _turns(
        {"kind": "step", "text": "старт", "replies": [], "messages": [], "toasts": []},
        {"kind": "no_button", "label": "Купить", "known": ["Ок"]},
        {"kind": "no_button", "label": "Купить", "known": ["Ок"]},
    )
    assert engine._consume(turns, case, res) is Status.PASSED
    assert [f.step_index for f in res.failures] == [1, 2]


def test_три_шага_индекс_растёт_ровно_на_один():
    """Если индекс шага сбивается, ожидания начинают сверяться не с тем шагом
    и кейс «проходит» или «падает» случайно. Три шага с разными ожиданиями
    делают сбой видимым."""
    case = _case(
        {"send": "а", "expect": {"text_contains": ["А"]}},
        {"send": "б", "expect": {"text_contains": ["Б"]}},
        {"send": "в", "expect": {"text_contains": ["В"]}},
    )
    res = _result()
    turns = _turns(
        _turn("написать «а»", ["А"]),
        _turn("написать «б»", ["Б"]),
        _turn("написать «в»", ["В"]),
    )
    assert engine._consume(turns, case, res) is Status.PASSED
    assert res.steps_done == 3


def test_лишний_ответ_не_сверяется_с_несуществующим_шагом():
    """Больше ответов, чем шагов: сверх шагов ответы не проверяются и не
    должны ронять кейс обращением к несуществующему шагу."""
    case = _case({"send": "а", "expect": {"text_contains": ["А"]}})
    res = _result()
    turns = _turns(_turn("написать «а»", ["А"]), _turn("написать «а»", ["Б"]))
    assert engine._consume(turns, case, res) is Status.PASSED


def test_шаг_без_ожидания_пропускается():
    """Шаг может не иметь expect — тогда сверять нечего, и движок не должен
    звать проверку с пустым ожиданием."""
    case = _case(
        {"send": "а"},
        {"send": "б", "expect": {"silent": True}},
    )
    res = _result()
    turns = _turns(_turn("написать «а»", ["шум"]), _turn("написать «б»", []))
    assert engine._consume(turns, case, res) is Status.PASSED


def test_реплика_клиента_точная():
    case = _case({"send": "ping", "expect": {"text_contains": ["Pong"]}})
    res = _result()
    engine._consume(
        _turns({"kind": "step", "text": "написать «ping»", "replies": ["Pong"]}), case, res
    )
    assert res.turns[0] == Turn(text="написать «ping»", out=True)


def test_поле_messages_имеет_приоритет_над_replies():
    """Сообщение рисуется из messages: в replies входит и тост, и брать
    последний элемент replies значило бы дублировать тост. Случай, где
    messages и replies-без-тостов расходятся, ловит это."""
    case = _case({"send": "ping", "expect": {"text_contains": ["M"]}})
    res = _result()
    engine._consume(
        _turns({"kind": "step", "text": "t", "replies": ["R"], "messages": ["M"], "toasts": []}),
        case,
        res,
    )
    bot = [t for t in res.turns if not t.out]
    assert bot == [Turn(text="M", out=False)]


def test_тост_рисуется_отдельной_репликой():
    case = _case({"click": "Далее", "expect": {"text_contains": ["Ок"]}})
    res = _result()
    engine._consume(
        _turns(
            {
                "kind": "step",
                "text": "нажать",
                "replies": ["тост", "Ок"],
                "messages": ["Ок"],
                "toasts": ["тост"],
            }
        ),
        case,
        res,
    )
    assert Turn(text="тост", out=False, is_toast=True) in res.turns


def test_медиа_сохраняется_на_реплике_бота():
    case = _case({"send": "ping", "expect": {"media_kind": "photo"}})
    res = _result()
    engine._consume(
        _turns(
            {
                "kind": "step",
                "text": "t",
                "replies": ["подпись"],
                "messages": ["подпись"],
                "toasts": [],
                "media": [{"kind": "photo", "caption": "с"}],
            }
        ),
        case,
        res,
    )
    bot = [t for t in res.turns if not t.out]
    assert bot == [Turn(text="подпись", out=False, media=[Media(kind="photo", caption="с")])]


def test_ответ_только_медиа_даёт_пузырь_с_медиа_и_кнопками():
    case = _case({"send": "ping", "expect": {"media_kind": "photo"}})
    res = _result()
    engine._consume(
        _turns(
            {
                "kind": "step",
                "text": "t",
                "replies": [],
                "messages": [],
                "toasts": [],
                "media": [{"kind": "photo"}],
                "buttons": ["Далее"],
            }
        ),
        case,
        res,
    )
    bot = [t for t in res.turns if not t.out]
    assert bot == [Turn(text="", out=False, buttons=["Далее"], media=[Media(kind="photo")])]


def test_media_count_отчёт_точный():
    got = engine._check(
        Expect(media_count=3), 0, "шаг_имя", [], [], [_media("photo"), _media("document")]
    )
    assert got[0].step_index == 0
    assert got[0].step_label == "шаг_имя"
    assert got[0].kind == "media_count"
    assert got[0].expected == "ровно 3"
    assert got[0].actual == "2: photo; document"
    assert got[0].hint == "число вложений в ответе"


def test_media_kind_отчёт_точный():
    got = engine._check(Expect(media_kind="photo"), 2, "шаг2", [], [], [_media("document")])
    assert got[0].step_index == 2
    assert got[0].step_label == "шаг2"
    assert got[0].kind == "media_kind"
    assert got[0].expected == "photo"
    assert got[0].actual == "document"
    assert got[0].hint == "сверьте метод отправки в коде бота (sendPhoto → photo)"


def test_обязательный_шаг_с_ожиданием_фиксирует_точный_индекс():
    case = _case(
        {"send": "а", "expect": {"text_contains": ["А"]}},
        {"send": "б", "expect": {"text_contains": ["Б"]}},
    )
    res = _result()
    turns = _turns(
        _turn("написать «а»", ["нет"]),
        _turn("написать «б»", ["Б"]),
    )
    assert engine._consume(turns, case, res) is Status.FAILED
    assert res.failures[0].step_index == 0
    assert res.failures[0].step_label == "написать «а»"


# ── карантин: нарушения доходят до результата кейса ────────────────────────


def _готовая_среда(tmp_path, monkeypatch, execr):
    """Подменить всё, до чего прогон не дойдёт без настоящего бота."""
    monkeypatch.setattr(engine.security, "assert_fake_token", lambda: None)
    monkeypatch.setattr(engine, "_adapter_for", lambda case, bot_dir: "newgen")
    monkeypatch.setattr(
        engine.envmod,
        "ensure_env",
        lambda bot_dir, cache: SimpleNamespace(python=tmp_path / ".venv" / "bin" / "python"),
    )
    monkeypatch.setattr(engine, "run_bot", lambda **kw: execr)


def test_нарушения_карантина_доходят_до_результата(tmp_path, monkeypatch):
    from botkit_farm.runner.host import ExecResult

    case = _case({"send": "/start", "expect": {"silent": True}})
    _готовая_среда(
        tmp_path, monkeypatch, ExecResult(turns=[], violations=["open → /home/algtro/.ssh/x"])
    )

    result = engine.run_case(
        case,
        bot_dir=tmp_path / "bot",
        cache_dir=tmp_path / "envs",
        work_dir=tmp_path / ".work",
    )

    assert result.quarantine == ["open → /home/algtro/.ssh/x"]


def test_без_нарушений_поле_карантина_пустое(tmp_path, monkeypatch):
    from botkit_farm.runner.host import ExecResult

    case = _case({"send": "/start", "expect": {"silent": True}})
    _готовая_среда(tmp_path, monkeypatch, ExecResult(turns=[]))

    result = engine.run_case(
        case,
        bot_dir=tmp_path / "bot",
        cache_dir=tmp_path / "envs",
        work_dir=tmp_path / ".work",
    )

    assert result.quarantine == []


def test_ошибка_запуска_не_теряет_нарушения(tmp_path, monkeypatch):
    """Бот упал на запрещённой записи: и причина, и попытка нужны обе."""
    from botkit_farm.runner.host import ExecResult

    case = _case({"send": "/start", "expect": {"silent": True}})
    _готовая_среда(
        tmp_path,
        monkeypatch,
        ExecResult(turns=[], error="бот не отработал", violations=["open → /etc/passwd"]),
    )

    result = engine.run_case(
        case,
        bot_dir=tmp_path / "bot",
        cache_dir=tmp_path / "envs",
        work_dir=tmp_path / ".work",
    )

    assert result.status is Status.ERRORED
    assert result.error == "бот не отработал"
    assert result.quarantine == ["open → /etc/passwd"]


def test_карантин_попадает_в_json_отчёта(tmp_path, monkeypatch):
    from botkit_farm.runner.host import ExecResult

    case = _case({"send": "/start", "expect": {"silent": True}})
    _готовая_среда(
        tmp_path, monkeypatch, ExecResult(turns=[], violations=["connect → 1.2.3.4:443"])
    )

    result = engine.run_case(
        case,
        bot_dir=tmp_path / "bot",
        cache_dir=tmp_path / "envs",
        work_dir=tmp_path / ".work",
    )

    assert result.to_dict()["quarantine"] == ["connect → 1.2.3.4:443"]
