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

# Боевой манифест лежит в `cases/`, и он обязан быть в дереве мутанта: за это
# отвечает `also_copy = ["cases/"]` в `[tool.mutmut]`. Раньше здесь стоял
# `skipif`, чтобы мутационный прогон не падал на отсутствующих данных, — но
# пропуск прячет проверку: тест, который в мутантном дереве не исполняется, не
# ловит мутанта, а делает вид. Раз данные копируются, проверка идёт везде.
ЕСТЬ_БОЕВЫЕ_ДАННЫЕ = CASES.is_dir() and (CASES / coverage.MANIFEST).is_file()


def test_боевые_данные_доехали_до_дерева():
    """Страховка от тихого возврата к skipif: данные обязаны быть на месте.

    Без этой проверки удаление `also_copy` снова оставило бы боевые тесты
    пропущенными, и никто бы не заметил. Здесь падение прямо говорит, что
    настройка `[tool.mutmut]` разъехалась с деревом.
    """
    assert ЕСТЬ_БОЕВЫЕ_ДАННЫЕ, (
        "нет cases/coverage.json — проверьте also_copy в [tool.mutmut] "
        "(данные кейсов обязаны копироваться в дерево мутанта)"
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

# Отказ тостом, а не молчанием: гард require_admin отвечает «⛔ Доступ
# запрещён.», и кейс ловит именно это. Форма refusal, которую старое
# определение gate («только silent») не признавало.
CALLBACK_GATE_CASE = """
id: {id}
title: {title}
source: https://example.invalid/bot
tags: [security]
steps:
  - callback: admin:services
    as: guest
    expect:
      text_contains: ["⛔ Доступ запрещён."]
"""


def _write(dir_: pathlib.Path, name: str, positive: bool, cid: str) -> None:
    tpl = POSITIVE_CASE if positive else GATE_CASE
    (dir_ / name).write_text(tpl.format(id=cid, title=cid), encoding="utf-8")


def _write_callback_gate(dir_: pathlib.Path, name: str, cid: str) -> None:
    (dir_ / name).write_text(CALLBACK_GATE_CASE.format(id=cid, title=cid), encoding="utf-8")


def _manifest_v2(
    dir_: pathlib.Path,
    entries: list[dict[str, str]],
    extras: list[dict[str, str]] | None = None,
) -> None:
    (dir_ / coverage.MANIFEST).write_text(
        json.dumps(
            {"schema_version": 2, "obligations": entries, "extras": extras or []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _manifest(dir_: pathlib.Path, entries: list[dict[str, str]]) -> None:
    _manifest_v2(dir_, entries, [])


def _entry(bot: str, flow: str, case: str, kind: str) -> dict[str, str]:
    return {"bot": bot, "flow": flow, "case": case, "kind": kind}


def _fleet(dir_: pathlib.Path, bots: list[str]) -> None:
    (dir_ / coverage.FLEET).write_text(
        json.dumps({"schema_version": 1, "bots": bots}, ensure_ascii=False),
        encoding="utf-8",
    )


# ── боевой манифест ─────────────────────────────────────────────────────────


def test_боевое_покрытие_выполнено():
    """Реальные кейсы обязаны закрывать все объявленные потоки."""
    assert check(CASES) == []


def test_у_каждого_бота_есть_positive_поток():
    """Бот, покрытый только отказом, не доказывает, что умеет работать."""
    ob = load(CASES)
    bots = {o.bot for o in ob.obligations}
    for bot in sorted(bots):
        kinds = {o.kind for o in ob.obligations if o.bot == bot}
        assert "positive" in kinds, f"{bot}: нет positive-обязательства"


# ── негативные близнецы ─────────────────────────────────────────────────────


def test_positive_привязан_к_gate_кейсу_ловится(tmp_path):
    """Кейс-отказ под видом positive: работа потока не доказана — падение.

    Обещание positive на gate-кейсе и обещание gate на обычном кейсе — два
    разных дефекта; сообщение первого обязано быть именно про positive.
    """
    _write(tmp_path, "gate.yaml", positive=False, cid="only-gate")
    _manifest(tmp_path, [_entry("bot-x", "работа", "gate.yaml", "positive")])
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any("обещает positive" in p and "gate" in p for p in problems), problems


def test_gate_привязан_к_positive_кейсу_ловится(tmp_path):
    """Обещали gate-кейс, а файл — обычный: сообщение именно про gate.

    Мутант, заменивший `kind == "gate" and not gate` на `or`, добавил бы лишнее
    positive-сообщение; тогда проблем стало бы две, и точная формулировка в
    проверке выше перестала бы совпадать.
    """
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "отказ", "ok.yaml", "gate")])
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any("обещает gate" in p and "не является" in p for p in problems), problems


def test_gate_по_сырому_callback_признаётся_отказом(tmp_path):
    """Отказ тостом — тоже gate: кейс с сырым callback проходит гейт.

    Определение «только silent» отвергало IDOR-кейсы трёх ботов: гард
    `require_admin` отвечает тостом, а не молчит, и гейт требовал объявить
    такой кейс `positive` — утверждать «поток работает» о проверке отказа.
    Регрессия — три обязательства `*-idor-guard` в боевом манифесте
    (`test_боевое_покрытие_выполнено` краснеет, если вернуть старое правило).
    """
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _write_callback_gate(tmp_path, "idor.yaml", cid="forged")
    _manifest(
        tmp_path,
        [
            _entry("bot-x", "работа", "ok.yaml", "positive"),
            _entry("bot-x", "админ-гейт / IDOR", "idor.yaml", "gate"),
        ],
    )
    _fleet(tmp_path, ["bot-x"])
    assert check(tmp_path) == []


def test_positive_привязан_к_callback_кейсу_ловится(tmp_path):
    """Кейс-отказ по сырому callback под видом positive: падение.

    Иначе IDOR-кейс можно было бы объявить positive и пройти гейт утверждением,
    которое сам же кейс и опровергает: он проходит и без гарда.
    """
    _write_callback_gate(tmp_path, "idor.yaml", cid="forged")
    _manifest(tmp_path, [_entry("bot-x", "админ-гейт / IDOR", "idor.yaml", "positive")])
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any("обещает positive" in p and "gate" in p for p in problems), problems


# ── закрытие выживших мутантов (mutation gate) ─────────────────────────────
#
# Мутанты ниже меняли сообщение об ошибке или группировку, и без теста на
# конкретное поведение проходили. Каждый тест ниже убивает свой класс.


def test_отсутствующий_манифест_даёт_понятную_ошибку(tmp_path):
    """Текст ошибки при отсутствии манифеста — часть контракта, не косметика."""
    with pytest.raises(CoverageError, match=r"^нет манифеста покрытия:"):
        coverage.load(tmp_path)


def test_отсутствующий_реестр_флота_даёт_понятную_ошибку(tmp_path):
    with pytest.raises(CoverageError, match=r"^нет реестра флота:"):
        coverage.load_fleet(tmp_path)


def test_check_без_реестра_флота_возвращает_проблему_а_не_падает(tmp_path):
    """Отсутствующий fleet.json → проблема в списке; мутант с default '' падает
    с AttributeError, потому что '' is not None."""
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "работа", "ok.yaml", "positive")])
    problems = check(tmp_path)
    assert any("нет реестра флота" in p for p in problems), problems


def test_check_группирует_обязательства_по_боту(tmp_path):
    """Бот только с gate обязан получить замечание о нехватке positive — даже
    если у другого бота positive есть. Мутант, группирующий всё под None,
    скрыл бы отсутствие."""
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _write(tmp_path, "gate.yaml", positive=False, cid="only-gate")
    _manifest(
        tmp_path,
        [
            _entry("bot-x", "отказ", "gate.yaml", "gate"),
            _entry("bot-y", "работа", "ok.yaml", "positive"),
        ],
    )
    _fleet(tmp_path, ["bot-x", "bot-y"])
    problems = check(tmp_path)
    assert any(p.startswith("bot-x:") and "ни одного positive" in p for p in problems), problems


def test_пропавший_кейс_ловится(tmp_path):
    """Обязательство ссылается на файл, которого нет.

    Два обязательства, и первое — пропавшее: ошибка обязана быть собрана, а
    проверка второго продолжена. Мутант `continue`→`break` остановился бы на
    первом и потерял вторую проблему (второе обязательство заведомо битое).
    """
    _write(tmp_path, "gate.yaml", positive=False, cid="only-gate")
    _manifest(
        tmp_path,
        [
            _entry("bot-x", "работа", "нет.yaml", "positive"),
            _entry("bot-x", "отказ", "gate.yaml", "positive"),  # gate под видом positive
        ],
    )
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any("пропал" in p for p in problems), problems
    assert any("обещает positive" in p for p in problems), (
        "обход обязан продолжиться после пропавшего кейса, иначе второй дефект "
        f"(positive на gate-кейсе) потерян: {problems}"
    )


def test_бот_без_positive_ловится(tmp_path):
    """Манифест, где у бота только gate, обязан упасть — молчание не покрытие.

    Сообщение обязано называть **бота**: мутант, сгруппировавший всех под одним
    ключом (`setdefault(None)`), теряет имя и делает диагностику бесполезной.
    """
    _write(tmp_path, "gate.yaml", positive=False, cid="only-gate")
    _manifest(tmp_path, [_entry("bot-x", "отказ", "gate.yaml", "gate")])
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any(p.startswith("bot-x:") and "ни одного positive" in p for p in problems), problems


def test_нет_манифеста_не_зелёный(tmp_path):
    """Нечего проверять — это ошибка, а не успех."""
    problems = check(tmp_path)
    assert problems and any("манифест" in p for p in problems)


def test_битый_json_ловится(tmp_path):
    """Невалидный JSON — ошибка с внятным текстом, а не пустое сообщение.

    Мутант, подменивший текст на `None`, оставил бы исключение без диагностики:
    тип верен, а починить по нему нечего.
    """
    (tmp_path / coverage.MANIFEST).write_text("{ не json", encoding="utf-8")
    with pytest.raises(CoverageError, match="невалидный JSON"):
        load(tmp_path)


def test_лишнее_поле_сообщает_поле(tmp_path):
    """Сообщение об ошибке обязано называть путь поля и его вид ошибки.

    Мутанты в `_where` (разделитель локации `.`, текст «корень», регистр)
    выживали: тест проверял только тип исключения. Здесь сверяется сама
    формулировка — путь до поля склеен точками.
    """
    (tmp_path / coverage.MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "obligations": [
                    {"bot": "x", "flow": "f", "case": "c.yaml", "kind": "positive", "extra": 1}
                ],
                "extras": [],
            }
        )
    )
    with pytest.raises(CoverageError) as ei:
        load(tmp_path)
    assert "obligations.0.extra" in str(ei.value), str(ei.value)


def test_неизвестный_вид_ловится(tmp_path):
    _write(tmp_path, "ok.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "работа", "ok.yaml", "weird")])
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert problems and any("kind" in p for p in problems)


def test_здоровый_манифест_зелёный(tmp_path):
    """Бот с настоящим positive-кейсом не должен получать замечаний.

    Мутанты, поменявшие литерал `"positive"` (регистр или «XXpositiveXX»),
    выживали: все негативные тесты заканчивались отсутствием positive-кейса, и
    сломанный литерал давал тот же результат. Здесь positive-кейс есть — при
    корректном коде замечаний ноль, при мутанте появляется ложное.
    """
    _write(tmp_path, "happy.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "работа", "happy.yaml", "positive")])
    _fleet(tmp_path, ["bot-x"])
    assert check(tmp_path) == []


def test_пустой_манифест_ловится(tmp_path):
    (tmp_path / coverage.MANIFEST).write_text(
        '{"schema_version": 2, "obligations": [], "extras": []}'
    )
    with pytest.raises(CoverageError):
        load(tmp_path)


def test_лишнее_поле_ловится(tmp_path):
    """Опечатка в ключе не должна молчать: extra=forbid."""
    (tmp_path / coverage.MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "obligations": [
                    {"bot": "x", "flow": "f", "case": "c.yaml", "kind": "positive", "extra": 1}
                ],
                "extras": [],
            }
        )
    )
    with pytest.raises(CoverageError):
        load(tmp_path)


def test_obligation_вид_ограничен_литералом():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Obligation(bot="x", flow="f", case="c.yaml", kind="nope")


# ── контракт покрытия v2 ─────────────────────────────────────────────────────


def test_кейс_вне_манифеста_ловится(tmp_path):
    """Кейс в каталоге, не объявленный ни в obligations, ни в extras — ошибка."""
    _write(tmp_path, "orphan.yaml", positive=True, cid="orphan")
    _manifest(tmp_path, [_entry("bot-x", "работа", "happy.yaml", "positive")])
    _write(tmp_path, "happy.yaml", positive=True, cid="happy")
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any("кейс не объявлен в манифесте" in p and "orphan.yaml" in p for p in problems), (
        problems
    )


def test_экстра_без_причины_ловится(tmp_path):
    """extra с пустым/отсутствующим why — ловится и называет бота и кейс."""
    _write(tmp_path, "extra.yaml", positive=True, cid="extra")
    _manifest_v2(
        tmp_path,
        [_entry("bot-x", "работа", "happy.yaml", "positive")],
        [{"bot": "bot-x", "case": "extra.yaml", "why": ""}],
    )
    _write(tmp_path, "happy.yaml", positive=True, cid="happy")
    _fleet(tmp_path, ["bot-x"])
    with pytest.raises(CoverageError) as ei:
        load(tmp_path)
    assert "extra bot-x/extra.yaml" in str(ei.value), str(ei.value)
    assert "не указана причина" in str(ei.value), str(ei.value)


def test_кейс_в_обоих_списках_ловится(tmp_path):
    """Кейс одновременно в obligations и extras — ошибка R4."""
    _write(tmp_path, "dup.yaml", positive=True, cid="dup")
    _manifest_v2(
        tmp_path,
        [_entry("bot-x", "работа", "dup.yaml", "positive")],
        [{"bot": "bot-x", "case": "dup.yaml", "why": "почему-то"}],
    )
    _fleet(tmp_path, ["bot-x"])
    problems = check(tmp_path)
    assert any("кейс объявлен дважды" in p and "dup.yaml" in p for p in problems), problems


def test_бот_из_флота_без_обязательств_ловится(tmp_path):
    """Бот в реестре флота обязан иметь positive-обязательство."""
    _write(tmp_path, "happy.yaml", positive=True, cid="happy")
    _manifest(tmp_path, [_entry("bot-x", "работа", "happy.yaml", "positive")])
    _fleet(tmp_path, ["bot-x", "bot-y"])
    problems = check(tmp_path)
    assert any("bot-y" in p and "positive" in p and "флота" in p for p in problems), problems


def test_версия_1_понятная_ошибка(tmp_path):
    """Манифест версии 1 — понятная ошибка с инструкцией обновиться до 2."""
    (tmp_path / coverage.MANIFEST).write_text(
        json.dumps(
            {"schema_version": 1, "obligations": [_entry("bot-x", "ф", "c.yaml", "positive")]}
        )
    )
    with pytest.raises(CoverageError) as ei:
        load(tmp_path)
    assert "версии 1" in str(ei.value)
    assert "обновите" in str(ei.value)
    assert "extras" in str(ei.value)


def test_здоровый_манифест_v2_зелёный(tmp_path):
    """Схема v2 с obligations+extras при корректности — зелёная."""
    _write(tmp_path, "happy.yaml", positive=True, cid="happy")
    _write(tmp_path, "extra.yaml", positive=True, cid="extra")
    _manifest_v2(
        tmp_path,
        [_entry("bot-x", "работа", "happy.yaml", "positive")],
        [{"bot": "bot-x", "case": "extra.yaml", "why": "дополнительная проверка"}],
    )
    _fleet(tmp_path, ["bot-x"])
    assert check(tmp_path) == []
