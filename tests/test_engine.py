"""Проверки движка: сверка ожиданий и разбор транскрипции.

Полный прогон с живым ботом помечен `slow` и требует Redis и клона; здесь
проверяется то, что можно проверить без бота, — логика сверки. Она обязана
отличать «бот ответил не тем» от «бот не ответил», иначе баг-репорт врёт.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any

import pytest

from botkit_farm.adapters.registry import resolve as _resolve
from botkit_farm.case import template as tmpl
from botkit_farm.case.model import Case, Expect
from botkit_farm.runner import engine
from botkit_farm.runner.contract import Media, parse_turns
from botkit_farm.runner.result import CaseResult, Failure, Status, Turn


def _ref(builder: str):
    """Ссылка на адаптер так, как её вернул бы движок."""
    return _resolve(builder)


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


def test_начальный_результат_несёт_source_кейса():
    """Поле source отчёта обязано браться из кейса, а не подменяться.

    Мутанты в `_начальный_результат` меняли getattr/имя атрибута/default —
    без этого теста они проходили, и отчёт врал об источнике прогона.
    """
    case = _case(source="https://example.invalid/bot")
    res = engine._начальный_результат(case, pathlib.Path("bot-dir"))
    assert res.source == "https://example.invalid/bot"


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
    ref = engine._adapter_for(case, tmp_path)
    assert ref.spec == "newgen"
    assert ref.module == "botkit_farm.adapters.newgen"
    assert ref.attr == "build"


def test_сторонний_builder_разбирается_на_модуль_и_фабрику(tmp_path):
    """`builder` — не только имя поколения, но и ссылка на плагин."""
    case = _case(builder="botkit-farm.adapter:mypkg.adapters.mine")
    ref = engine._adapter_for(case, tmp_path)
    assert ref.spec == "botkit-farm.adapter:mypkg.adapters.mine"
    assert ref.module == "mypkg.adapters.mine"
    assert ref.attr == "build"
    assert ref.builtin is False


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
    assert engine._adapter_for(_case(), tmp_path).spec == "newgen"


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
    monkeypatch.setattr(engine, "_adapter_for", lambda case, bot_dir: _ref("newgen"))
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


# ── C6: repeat и choice ─────────────────────────────────────────────────────


def test_plan_разворачивает_repeat_с_индексом():
    case = _case({"send": "Товар {{ i }}", "repeat": 3, "expect": {"silent": True}})
    plan = engine._plan(case)

    assert [p.value for p in plan] == ["Товар 0", "Товар 1", "Товар 2"]
    assert [p.index for p in plan] == [0, 1, 2]
    assert [p.total for p in plan] == [3, 3, 3]
    # Подпись маркируется повтором: иначе три одинаковые строки в отчёте слились бы.
    assert plan[0].label == "написать «Товар 0» [1/3]"
    assert plan[2].label == "написать «Товар 2» [3/3]"


def test_to_steps_разворачивает_repeat_в_несколько_шагов():
    case = _case({"send": "{{ i }}", "repeat": 2, "expect": {"silent": True}})
    steps = engine._to_steps(engine._plan(case), 1.0)

    assert [s["value"] for s in steps] == ["0", "1"]
    assert all("gap" in s for s in steps)


def test_choice_становится_одним_шагом_с_вариантами():
    case = _case({"choice": [{"click": "Купить"}, {"pick": "last"}], "expect": {"silent": True}})
    step = engine._to_steps(engine._plan(case), 1.0)[0]

    assert step["kind"] == "choice"
    assert step["options"] == [
        {"kind": "cb", "value": "Купить"},
        {"kind": "pick", "value": "last"},
    ]
    assert step["label"] == "выбрать доступное действие (нажать «Купить»)"


def test_plan_рендерит_ветки_choice_индексом():
    case = _case(
        {
            "choice": [{"send": "Шаг {{ i }}"}],
            "repeat": 2,
            "expect": {"silent": True},
        }
    )
    plan = engine._plan(case)

    assert [p.options[0]["value"] for p in plan] == ["Шаг 0", "Шаг 1"]


def test_consume_рендерит_expect_по_прошлым_ответам():
    case = _case(
        {"send": "а", "expect": {"text_contains": ["Привет"]}},
        {"send": "б", "expect": {"text_contains": ["{{ turns[-1] }}"]}},
    )
    res = _result()
    turns = _turns(_turn("написать «а»", ["Привет"]), _turn("написать «б»", ["Привет"]))

    assert engine._consume(turns, case, res) is Status.PASSED


def test_consume_рендерит_expect_и_падает_на_несовпадении():
    case = _case(
        {"send": "а", "expect": {"text_contains": ["Привет"]}},
        {"send": "б", "expect": {"text_contains": ["{{ turns[-1] }}"]}},
    )
    res = _result()
    turns = _turns(_turn("написать «а»", ["Привет"]), _turn("написать «б»", ["Другое"]))

    assert engine._consume(turns, case, res) is Status.FAILED


def test_consume_сверяет_каждый_повтор_отдельно():
    case = _case({"send": "{{ i }}", "repeat": 2, "expect": {"text_contains": ["{{ i }}"]}})
    res = _result()
    turns = _turns(
        _turn("написать «0» [1/2]", ["0"]),
        _turn("написать «1» [2/2]", ["1"]),
    )

    assert engine._consume(turns, case, res) is Status.PASSED
    assert res.steps_done == 2


def test_render_expect_рендерит_строки_всех_полей():
    exp = Expect(
        text_contains=["a{{ i }}"],
        buttons_contains=["b{{ n }}"],
        caption_contains=["c{{ turns[-1] }}"],
    )
    got = engine._render_expect(exp, {"i": 1, "n": 2, "turns": ["X"]}, "шаг")

    assert got.text_contains == ["a1"]
    assert got.buttons_contains == ["b2"]
    assert got.caption_contains == ["cX"]
    assert got.text_equals is None


def test_render_expect_точный_текст():
    exp = Expect(text_equals="{{ turns[0] }}")
    got = engine._render_expect(exp, {"i": 0, "n": 1, "turns": ["Привет"]}, "шаг")

    assert got.text_equals == "Привет"


def test_steps_total_считает_повторы():
    case = _case({"send": "{{ i }}", "repeat": 4, "expect": {"silent": True}})
    assert engine._начальный_результат(case, __import__("pathlib").Path(".")).steps_total == 4


def test_plan_подставляет_и_индекс_и_число_повторов():
    # Обе переменные повтора должны доехать до действия: индекс и общее число.
    case = _case({"send": "{{ i }}/{{ n }}", "repeat": 3, "expect": {"silent": True}})
    assert [p.value for p in engine._plan(case)] == ["0/3", "1/3", "2/3"]


def test_consume_подставляет_число_повторов_в_ожидание():
    case = _case({"send": "{{ i }}", "repeat": 2, "expect": {"text_contains": ["из {{ n }}"]}})
    res = _result()
    turns = _turns(_turn("написать «0» [1/2]", ["из 2"]), _turn("написать «1» [2/2]", ["из 2"]))

    assert engine._consume(turns, case, res) is Status.PASSED


def test_plan_помечает_повтор_уже_с_двух():
    case = _case({"send": "x", "repeat": 2, "expect": {"silent": True}})
    plan = engine._plan(case)

    assert plan[0].label == "написать «x» [1/2]"
    assert plan[1].label == "написать «x» [2/2]"


def test_подпись_обрезает_длинное_сообщение_до_40():
    long = "х" * 41
    case = _case({"send": long, "expect": {"silent": True}})
    assert engine._plan(case)[0].label == f"написать «{'х' * 40}»"


def test_plan_переносит_поля_шага_и_повтора():
    case = _case(
        {
            "choice": [{"click": "Далее"}],
            "as": "admin",
            "pause": 3.0,
            "optional": True,
            "repeat": 2,
            "expect": {"text_contains": ["ок"]},
        }
    )
    plan = engine._plan(case)

    assert len(plan) == 2
    for j, p in enumerate(plan):
        assert p.kind == "choice"
        assert p.expect is not None
        assert p.expect.text_contains == ["ок"]
        assert p.as_ == "admin"
        assert p.pause == 3.0
        assert p.optional is True
        assert p.index == j
        assert p.total == 2


def test_plan_переносит_поля_обычного_шага():
    case = _case(
        {
            "send": "а",
            "as": "admin",
            "pause": 3.0,
            "optional": True,
            "expect": {"text_contains": ["ок"]},
        }
    )
    p = engine._plan(case)[0]

    assert p.expect is not None
    assert p.as_ == "admin"
    assert p.pause == 3.0
    assert p.optional is True


def test_plan_называет_место_ошибки_шаблона():
    case = _case({"send": "{{ missing }}", "expect": {"silent": True}})
    with pytest.raises(tmpl.TemplateError, match="test-case: шаг 1"):
        engine._plan(case)


def test_plan_называет_место_ошибки_в_ветке_choice():
    case = _case({"choice": [{"send": "{{ missing }}"}], "expect": {"silent": True}})
    with pytest.raises(tmpl.TemplateError, match="test-case: шаг 1"):
        engine._plan(case)


def test_render_expect_называет_место_ошибки_в_точном_тексте():
    exp = Expect(text_equals="{{ missing }}")
    with pytest.raises(tmpl.TemplateError, match="МЕСТО"):
        engine._render_expect(exp, {"i": 0, "n": 1, "turns": []}, "МЕСТО")


def test_render_expect_называет_место_ошибки_в_text_contains():
    exp = Expect(text_contains=["{{ missing }}"])
    with pytest.raises(tmpl.TemplateError, match="МЕСТО"):
        engine._render_expect(exp, {"i": 0, "n": 1, "turns": []}, "МЕСТО")


def test_render_expect_называет_место_ошибки_в_buttons_contains():
    exp = Expect(buttons_contains=["{{ missing }}"])
    with pytest.raises(tmpl.TemplateError, match="МЕСТО"):
        engine._render_expect(exp, {"i": 0, "n": 1, "turns": []}, "МЕСТО")


def test_render_expect_называет_место_ошибки_в_caption_contains():
    exp = Expect(caption_contains=["{{ missing }}"])
    with pytest.raises(tmpl.TemplateError, match="МЕСТО"):
        engine._render_expect(exp, {"i": 0, "n": 1, "turns": []}, "МЕСТО")


def test_consume_называет_место_ошибки_шаблона_ожидания():
    case = _case({"send": "а", "expect": {"text_contains": ["{{ missing }}"]}})
    res = _result()
    turns = _turns(_turn("написать «а»", ["ок"]))
    with pytest.raises(tmpl.TemplateError, match="написать «а»"):
        engine._consume(turns, case, res)
