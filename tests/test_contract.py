"""Контрактные проверки границы хост↔движок.

Граница — место, где жили два из семи дефектов ([[опыт/lessons]] №4, №5). Эти
тесты держат форму `Turn`: неизвестное поле обязано падать с именем поля, а
полный набор форм зафиксирован снимком `golden/host_turns.json`.

Логика проста: хост и движок — два разных процесса, и обновляться они могут
порознь. Если хост добавит поле, которого движок не знает, движок обязан
**отказать**, а не молча потерять данные — иначе отчёт станет ложью.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from botkit_farm.runner.contract import CrashTurn, Media, NoButtonTurn, StepTurn, parse_turns

GOLDEN = pathlib.Path(__file__).parent / "golden" / "host_turns.json"


# ── golden-снимок ─────────────────────────────────────────────────────────────


def test_golden_разбирается_в_три_вида():
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    turns = parse_turns(raw)
    assert [type(t) for t in turns] == [StepTurn, StepTurn, NoButtonTurn, CrashTurn]


def test_golden_round_trip_не_теряет_полей():
    """Прогон снимка туда-обратно: если поле удалить из модели, разбор golden
    упадёт (лишнее поле), а если поле появилось без обновления снимка — упадёт
    это сравнение."""
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    dumped = [t.model_dump(mode="json") for t in parse_turns(raw)]
    assert dumped == raw


def test_медиа_в_golden_типизировано():
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    step = parse_turns(raw)[1]
    assert isinstance(step, StepTurn)
    assert step.media == [Media(kind="document", file_id="FAKE-DOC", caption="Прайс")]


# ── отказ на расхождении контракта ────────────────────────────────────────────


def test_неизвестное_поле_падает_с_именем():
    """Новый хост, старый движок: лишнее поле обязано назвать себя."""
    with pytest.raises(ValidationError) as ei:
        parse_turns([{"kind": "step", "text": "x", "unexpected_field": 1}])
    assert "unexpected_field" in str(ei.value)


def test_отсутствие_обязательного_поля_падает():
    """Старый хост, новый движок: crash без причины — уже не данные."""
    with pytest.raises(ValidationError):
        parse_turns([{"kind": "crash"}])


def test_неизвестный_kind_отвергается():
    with pytest.raises(ValidationError):
        parse_turns([{"kind": "что-то-новое"}])


def test_неизвестный_вид_медиа_отвергается():
    with pytest.raises(ValidationError):
        parse_turns(
            [{"kind": "step", "media": [{"kind": "location", "file_id": "x", "caption": ""}]}]
        )


# ── обратная совместимость со старым хостом ──────────────────────────────────


def test_старый_хост_без_новых_полей_читается():
    """Ранний хост не присылал messages/toasts/media/buttons. Поля необязательны,
    поэтому его транскрипция обязана читаться без ошибок."""
    turns = parse_turns([{"kind": "step", "text": "ping", "replies": ["Ок"]}])
    step = turns[0]
    assert isinstance(step, StepTurn)
    assert step.messages == []
    assert "messages" not in step.model_fields_set, "поля не было — движок должен видеть это"


def test_транскрипция_не_список_отвергается():
    with pytest.raises(ValidationError):
        parse_turns({"kind": "step"})
