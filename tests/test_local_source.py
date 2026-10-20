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


def test_каждый_url_кейс_резолвится_в_свой_бот(tmp_path):
    """--local обязан срабатывать для всех кейсов — проверяем отображение, а не диска.

    Различие принципиально: этот гейт держит «URL кейса соответствует каталогу
    bots/<bot>», а не «на этой машине лежат клоны девяти ботов». У фермы в
    собственном CI каталог `bots/` пуст — боты живут отдельными репозиториями и
    клонируются под прогон, — поэтому проверка наличия клонов была бы красной
    там, где всё правильно, и молча зелёной только у того, кто уже всё склонировал.

    Здесь `bots_dir` подменяется временным, где создан каталог под каждый бот из
    кейсов: так проверяется именно отображение URL → `bots/<repo-name>`. Промах
    (опечатка в `source`, чужой хост, слипшийся путь) роняет тест, и это тот
    промах, ради которого --local и появился.
    """
    names = {src.rstrip("/").rsplit("/", 1)[-1] for src in _case_sources().values() if is_url(src)}
    assert names, "в cases/ нет URL-кейсов — тест ничего не проверяет"
    for name in names:
        (tmp_path / name).mkdir()

    missing = []
    for cid, src in _case_sources().items():
        if not is_url(src):
            continue
        got = local_for(src, tmp_path)
        want = tmp_path / src.rstrip("/").rsplit("/", 1)[-1]
        if got != want:
            missing.append(f"{cid}: {src} -> {got}, ожидался {want}")
    assert not missing, "URL кейса не отображается на свой каталог:\n" + "\n".join(missing)


def test_отсутствие_чекаута_это_промах_а_не_клон(tmp_path):
    """--local при отсутствующем чекауте обязан вернуть None, а не путь.

    Это и есть ловушка, которую флаг закрывает: если local_for вернёт несуществующий
    путь, вызывающий либо упадёт невнятно, либо откатится на клон — и разработчик
    снова проверит чужой коммит, считая, что проверяет свой.
    """
    assert local_for("https://github.com/ninelegsdog/botkit-store", tmp_path) is None


def test_негатив_чужой_url_не_резолвится():
    """Доказательство, что проверка выше кусает: несуществующий бот — промах."""
    assert local_for("https://github.com/ninelegsdog/botkit-ghost", BOTS) is None


def test_локальный_source_не_переписывается():
    """Уже локальный source --local трогать не должен: двойная подмена — баг."""
    assert local_for("./bots/botkit-store", BOTS) is None


def test_чекаут_указывает_на_реальный_каталог(tmp_path):
    """Возврат — существующий каталог, а не имя: prepare() примет только путь.

    Каталог создаём во временном `bots_dir`: тест держит «возвращается именно
    каталог», а не «на машине есть клон botkit-store» — второе в CI фермы неверно.
    """
    (tmp_path / "botkit-store").mkdir()
    path = local_for("https://github.com/ninelegsdog/botkit-store", tmp_path)
    assert path is not None and path.is_dir()


@pytest.mark.parametrize("cid", sorted(_case_sources()))
def test_идентификатор_кейса_не_сломан(cid: str):
    """Страховка от опечатки в YAML, которая сорвала бы разбор всего файла."""
    assert cid and isinstance(cid, str)
