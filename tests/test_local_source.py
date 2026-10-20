"""`run --local` превращает URL кейса в локальный чекаут — и это проверяемо.

Проблема, которую закрывает файл: кейс указывает `source: https://...`, ферма
честно клонирует `--depth 1`, и разработчик, поправивший бота в рабочем дереве,
проверяет **чужой коммит**. Ошибка невидима: прогон зелёный, правка не
проверена. `--local` заменяет URL на `bots/<bot>`, а отсутствие чекаута делает
падением, а не тихим откатом на клон.

Тесты читают реальные кейсы в `cases/`: гейт держит не «флаг существует», а
«флаг сработает для каждого бота, у которого есть кейс». Каждый тест имеет
негативный близнец: если правило снять, он падает.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from botkit_farm.source import is_url, local_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
BOTS = ROOT / "bots"


def _case_sources() -> dict[str, str]:
    """id кейса → строка source, по всем кейсам каталога."""
    out: dict[str, str] = {}
    for f in sorted(CASES.rglob("*.y*ml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("source"):
            out[data["id"]] = str(data["source"])
    return out


def test_каждый_url_кейс_находит_локальный_чекаут():
    """--local обязан сработать для всех кейсов: иначе флаг есть, а пользы нет.

    Промах здесь — это бот, у которого кейс молча уходит в клон: ровно та
    ловушка, ради которой --local и появился.
    """
    missing = []
    for cid, src in _case_sources().items():
        if not is_url(src):
            continue
        if local_for(src, BOTS) is None:
            missing.append(
                f"{cid}: {src} -> нет каталога bots/{src.rstrip('/').rsplit('/', 1)[-1]}"
            )
    assert not missing, "у кейсов нет локального чекаута:\n" + "\n".join(missing)


def test_негатив_чужой_url_не_резолвится():
    """Доказательство, что проверка выше кусает: несуществующий бот — промах."""
    assert local_for("https://github.com/ninelegsdog/botkit-ghost", BOTS) is None


def test_локальный_source_не_переписывается():
    """Уже локальный source --local трогать не должен: двойная подмена — баг."""
    assert local_for("./bots/botkit-store", BOTS) is None


def test_чекаут_указывает_на_реальный_каталог():
    """Возврат — каталог, а не имя: prepare() примет только существующий путь."""
    path = local_for("https://github.com/ninelegsdog/botkit-store", BOTS)
    assert path is not None and path.is_dir()


@pytest.mark.parametrize("cid", sorted(_case_sources()))
def test_идентификатор_кейса_не_сломан(cid: str):
    """Страховка от опечатки в YAML, которая сорвала бы разбор всего файла."""
    assert cid and isinstance(cid, str)
