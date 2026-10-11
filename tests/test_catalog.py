"""Проверки каталога регрессов (ТЗ C5).

Каталог — это обещание: перечисленные регрессы существуют, а существующие —
перечислены. Проверки отрицательные почти все: положительный случай один
(целый каталог), а ценность каталога — ровно в том, что он ловит разрыв в обе
стороны. Отдельный тест держит целым боевой `examples/` репозитория: он и есть
гейт «удаление кейса регресса роняет CI».
"""

from __future__ import annotations

import json
import pathlib

import pytest

from botkit_farm import catalog
from botkit_farm.catalog import CatalogError, check, load

ROOT = pathlib.Path(__file__).resolve().parents[1]

CASE = """
id: {id}
title: Кейс регресса
source: https://example.invalid/bot
steps:
  - send: /start
    expect:
      text_contains: ["Ок"]
"""


def _case(dir_: pathlib.Path, name: str, case_id: str) -> None:
    (dir_ / name).write_text(CASE.format(id=case_id), encoding="utf-8")


def _manifest(dir_: pathlib.Path, entries: list[dict[str, str]], version: int = 1) -> None:
    data = {"schema_version": version, "regressions": entries}
    (dir_ / catalog.MANIFEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _entry(case: str, cid: str, why: str = "покрытие: путь") -> dict[str, str]:
    return {"id": cid, "case": case, "why": why}


# ── целый каталог ───────────────────────────────────────────────────────────


def test_целый_каталог_не_жалуется(tmp_path):
    _case(tmp_path, "reg.yaml", "reg-one")
    _manifest(tmp_path, [_entry("reg.yaml", "reg-one")])
    assert check(tmp_path) == []


def test_ручной_разбор_читает_запись(tmp_path):
    _case(tmp_path, "reg.yaml", "reg-one")
    _manifest(tmp_path, [_entry("reg.yaml", "reg-one", why="дефект #7")])
    cat = load(tmp_path)
    assert cat.schema_version == 1
    assert [e.id for e in cat.regressions] == ["reg-one"]
    assert cat.regressions[0].why == "дефект #7"


# ── манифест ────────────────────────────────────────────────────────────────


def test_нет_манифеста(tmp_path):
    assert check(tmp_path) == [f"нет манифеста каталога: {tmp_path / catalog.MANIFEST}"]
    with pytest.raises(CatalogError, match="нет манифеста"):
        load(tmp_path)


def test_битый_json(tmp_path):
    (tmp_path / catalog.MANIFEST).write_text("{ не json", encoding="utf-8")
    problems = check(tmp_path)
    assert len(problems) == 1
    assert "невалидный JSON" in problems[0]


def test_манифест_не_объект(tmp_path):
    (tmp_path / catalog.MANIFEST).write_text("[1, 2, 3]", encoding="utf-8")
    problems = check(tmp_path)
    assert len(problems) == 1
    assert "должен быть объектом" in problems[0]


def test_пустой_список_регрессов_отвергается(tmp_path):
    _manifest(tmp_path, [])
    assert any("regressions" in p for p in check(tmp_path))


def test_версия_схемы_обязательна(tmp_path):
    (tmp_path / catalog.MANIFEST).write_text(json.dumps({"regressions": []}), encoding="utf-8")
    assert any("schema_version" in p for p in check(tmp_path))


def test_пустой_why_отвергается(tmp_path):
    _case(tmp_path, "reg.yaml", "reg-one")
    _manifest(tmp_path, [_entry("reg.yaml", "reg-one", why="")])
    assert any("why" in p for p in check(tmp_path))


def test_лишнее_поле_манифеста_отвергается(tmp_path):
    _case(tmp_path, "reg.yaml", "reg-one")
    bad = _entry("reg.yaml", "reg-one")
    bad["origin"] = "issue"
    _manifest(tmp_path, [bad])
    assert any("origin" in p for p in check(tmp_path))


# ── сверка в обе стороны ────────────────────────────────────────────────────


def test_дубликат_id(tmp_path):
    _case(tmp_path, "a.yaml", "reg-one")
    _case(tmp_path, "b.yaml", "reg-two")
    _manifest(
        tmp_path,
        [_entry("a.yaml", "reg-one"), _entry("b.yaml", "reg-one")],
    )
    assert any("повторяется" in p for p in check(tmp_path))


def test_кейс_регресса_пропал(tmp_path):
    _manifest(tmp_path, [_entry("missing.yaml", "reg-gone")])
    problems = check(tmp_path)
    assert any("кейс регресса пропал" in p and "missing.yaml" in p for p in problems)


def test_id_кейса_не_совпадает_с_манифестом(tmp_path):
    _case(tmp_path, "reg.yaml", "reg-two")
    _manifest(tmp_path, [_entry("reg.yaml", "reg-one")])
    assert any("не совпадает" in p for p in check(tmp_path))


def test_кейс_вне_каталога(tmp_path):
    _case(tmp_path, "reg.yaml", "reg-one")
    _case(tmp_path, "вне.yaml", "vne-kataloga")
    _manifest(tmp_path, [_entry("reg.yaml", "reg-one")])
    assert any("кейс не в каталоге: вне.yaml" in p for p in check(tmp_path))


def test_битый_кейс_назван(tmp_path):
    (tmp_path / "reg.yaml").write_text("id: только-id\n", encoding="utf-8")
    _manifest(tmp_path, [_entry("reg.yaml", "reg-one")])
    assert any("reg.yaml" in p for p in check(tmp_path))


# ── инвариант репозитория ───────────────────────────────────────────────────


def test_каталог_репозитория_цел():
    """Боевой каталог обязан быть цел и не вырождаться.

    Это переносимая часть требования «≥10 публичных регрессов»: если кто-то
    удалит кейс и не тронет манифест (или наоборот), тест назовёт разрыв, а
    порог в 10 не даст каталогу незаметно усохнуть.
    """
    examples = ROOT / "examples"
    assert check(examples) == []
    assert len(load(examples).regressions) >= 10
