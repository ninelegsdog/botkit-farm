"""Обязательства покрытия: гейт и его негативные близнецы.

Файл держит не «модуль есть», а «гейт кусает». Каждое правило из `coverage.py`
имеет тест, который ломает условие и требует красного: положительный случай
один (реальные кейсы), остальные — отрицательные. Без них гейт неотличим от
зелёной лампочки, которая ничего не проверяет.

Отдельно проверяется **боевое покрытие**: реальный `cases/coverage.json` обязан
сходиться. Это CI-гейт «у каждого бота есть кейс на поток, а не только на
админ-гейт» — именно его отсутствие прятало support без счастливого пути.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from botkit_farm import coverage
from botkit_farm.coverage import CoverageError, Obligation, check, load

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"

# Боевой манифест лежит в `cases/`, а mutmut копирует в `mutants/` только исходники
# (`source_paths`) и тесты — не данные кейсов. Без этого пропуска мутационный
# прогон падал бы на «нет манифеста» в дереве, где его и не должно быть, и ронял
# бы весь `mutation.yml` — красный по причине, не относящейся к мутантам.
# Тесты ниже проверяют **данные** (`cases/coverage.json`), а не логику `coverage.py`,
# поэтому в мутационном окружении им не место; в обычном CI `cases/` на месте.
_ЕСТЬ_БОЕВЫЕ_ДАННЫЕ = CASES.is_dir() and (CASES / coverage.MANIFEST).is_file()
без_боевых_данных = pytest.mark.skipif(
    not _ЕСТЬ_БОЕВЫЕ_ДАННЫЕ,
    reason="нет cases/coverage.json (мутационное дерево mutmut или отсутствие данных)",
)

POSITIVE_CASE = """
id: {id}
title: {title}
source: https://example.invalid/bot
steps:
  - send: /start
    expect:
      text_contains: ["Ок"]
"""

GATE_CASE = """
id: {id}
title: {title}
source: https://example.invalid/bot
tags: [security]
steps:
  - send: /start
    as: guest
    expect:
      silent: true
"""


def _write(dir_: pathlib.Path, name: str, positive: bool, cid: str) -> None:
    tpl = POSITIVE_CASE if positive else GATE_CASE
    (dir_ / name).write_text(tpl.format(id=cid, title=cid), encoding="utf-8")


def _manifest(dir_: pathlib.Path, entries: list[dict[str, str]]) -> None:
    (dir_ / coverage.MANIFEST).write_text(
        json.dumps({"schema_version": 1, "obligations": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def _entry(bot: str, flow: str, case: str, kind: str) -> dict[str, str]:
    return {"bot": bot, "flow": flow, "case": case, "kind": kind}


# ── боевой манифест ─────────────────────────────────────────────────────────


@без_боевых_данных
def test_боевое_покрытие_выполнено():
    """Реальные кейсы обязаны закрывать все объявленные потоки."""
    assert check(CASES) == []


@без_боевых_данных
def test_у_каждого_бота_есть_positive_поток():
    """Бот, покрытый только отказом, не доказывает, что умеет работать."""
    ob = load(CASES)
    bots = {o.bot for o in ob.obligations}
    for bot in sorted(bots):
        kinds = {o.kind for o in ob.obligations if o.bot == bot}
        assert "positive" in kinds, f"{bot}: нет positive-обязательства"


# ── негативные близнецы ─────────────────────────────────────────────────────


def test_positive_привязан_к_gate_кейсу_ловится(tmp_path):
    """Кейс-отказ под видом positive: работа потока не доказана — падение."""
    _write(tmp_path, "gate.yaml", positive=False, cid="only-gate")
    _manifest(tmp_path, [_entry("bot-x", "работа", "gate.yaml", "positive")])
    problems = check(tmp_path)
    assert any("positive" in p and "gate" in p for p in problems), problems


def test_gate_привязан_к_positive_кейсу_ловится(tmp_path):
    """Обещали gate-кейс, а файл — обычный: гейт обязан заметить."""
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "отказ", "ok.yaml", "gate")])
    problems = check(tmp_path)
    assert any("gate" in p for p in problems), problems


def test_пропавший_кейс_ловится(tmp_path):
    """Обязательство ссылается на файл, которого нет."""
    _manifest(tmp_path, [_entry("bot-x", "работа", "нет.yaml", "positive")])
    problems = check(tmp_path)
    assert any("пропал" in p for p in problems), problems


def test_бот_без_positive_ловится(tmp_path):
    """Манифест, где у бота только gate, обязан упасть — молчание не покрытие."""
    _write(tmp_path, "gate.yaml", positive=False, cid="only-gate")
    _manifest(tmp_path, [_entry("bot-x", "отказ", "gate.yaml", "gate")])
    problems = check(tmp_path)
    assert any("positive" in p for p in problems), problems


def test_нет_манифеста_не_зелёный(tmp_path):
    """Нечего проверять — это ошибка, а не успех."""
    problems = check(tmp_path)
    assert problems and any("манифест" in p for p in problems)


def test_битый_json_ловится(tmp_path):
    (tmp_path / coverage.MANIFEST).write_text("{ не json", encoding="utf-8")
    with pytest.raises(CoverageError):
        load(tmp_path)


def test_неизвестный_вид_ловится(tmp_path):
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "работа", "ok.yaml", "weird")])
    problems = check(tmp_path)
    assert problems and any("kind" in p for p in problems)


def test_пустой_манифест_ловится(tmp_path):
    (tmp_path / coverage.MANIFEST).write_text('{"schema_version": 1, "obligations": []}')
    with pytest.raises(CoverageError):
        load(tmp_path)


def test_лишнее_поле_ловится(tmp_path):
    """Опечатка в ключе не должна молчать: extra=forbid."""
    (tmp_path / coverage.MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "obligations": [
                    {"bot": "x", "flow": "f", "case": "c.yaml", "kind": "positive", "extra": 1}
                ],
            }
        )
    )
    with pytest.raises(CoverageError):
        load(tmp_path)


def test_obligation_вид_ограничен_литералом():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Obligation(bot="x", flow="f", case="c.yaml", kind="nope")
