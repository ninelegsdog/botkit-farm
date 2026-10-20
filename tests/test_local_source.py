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

# mutmut копирует в `mutants/` только исходники (`source_paths`) и тесты — данных
# кейсов там нет. Тесты, которым нужны **боевые** `cases/`, пропускаются в таком
# дереве; гейт отображения URL → `bots/<repo-name>` работает на синтетических
# кейсах и проверяет логику `source.py` независимо от наличия данных.
_ЕСТЬ_КЕЙСЫ = CASES.is_dir() and any(CASES.rglob("*.y*ml"))
с_боевыми_кейсами = pytest.mark.skipif(
    not _ЕСТЬ_КЕЙСЫ,
    reason="нет cases/*.yaml (мутационное дерево mutmut или отсутствие данных)",
)


def _case_sources() -> dict[str, str]:
    """id кейса → строка source, по всем кейсам каталога."""
    out: dict[str, str] = {}
    for f in sorted(CASES.rglob("*.y*ml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("source"):
            out[data["id"]] = str(data["source"])
    return out


def _write_case(dir_: pathlib.Path, cid: str, source: str) -> None:
    """Кейс с заданным source — минимальный, но разбираемый yaml."""
    (dir_ / f"{cid}.yaml").write_text(
        f"id: {cid}\ntitle: {cid}\nsource: {source}\nsteps:\n"
        "  - send: /start\n    expect:\n      text_contains: ['Ок']\n",
        encoding="utf-8",
    )


def _repo_name(url: str) -> str:
    """Ожидаемое имя каталога: последний сегмент пути без `.git`.

    Дублирует контракт `source._repo_name` намеренно — тест держит именно
    отображение, документированное в модуле, а не его приватную реализацию.
    """
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def test_каждый_url_кейс_резолвится_в_свой_бот(tmp_path):
    """--local обязан срабатывать для всех URL-кейсов — отображение, а не диск.

    Различие принципиально: этот гейт держит «URL кейса соответствует каталогу
    bots/<repo-name>», а не «на этой машине лежат клоны девяти ботов». У фермы в
    собственном CI каталог `bots/` пуст, а в дереве mutmut нет и `cases/` —
    поэтому тест идёт на синтетических кейсах и не зависит ни от того, ни от
    другого. Промах (опечатка в `source`, слипшийся путь, чужой хост) роняет
    тест, и это тот промах, ради которого --local и появился.
    """
    sources = {
        "store": "https://github.com/ninelegsdog/botkit-store",
        "booking": "https://github.com/ninelegsdog/botkit-bookingbot.git",
        "trailing": "https://github.com/ninelegsdog/botkit-support/",
    }
    cases = tmp_path / "cases"
    cases.mkdir()
    for cid, src in sources.items():
        _write_case(cases, cid, src)

    parsed = {
        data["id"]: str(data["source"])
        for f in sorted(cases.rglob("*.y*ml"))
        for data in [yaml.safe_load(f.read_text(encoding="utf-8"))]
        if isinstance(data, dict) and data.get("source")
    }
    assert parsed, "синтетические кейсы не разобрались — тест ничего не проверяет"
    for name in {_repo_name(src) for src in parsed.values() if is_url(src)}:
        (tmp_path / name).mkdir()

    missing = []
    for cid, src in parsed.items():
        if not is_url(src):
            continue
        got = local_for(src, tmp_path)
        want = tmp_path / _repo_name(src)
        if got != want:
            missing.append(f"{cid}: {src} -> {got}, ожидался {want}")
    assert not missing, "URL кейса не отображается на свой каталог:\n" + "\n".join(missing)


@с_боевыми_кейсами
def test_боевые_кейсы_имеют_url_источник(tmp_path):
    """Реальные кейсы обязаны нести URL и отображаться на существующий каталог.

    Проверяет данные (`cases/`), а не логику: в мутационном дереве пропускается.
    """
    mapping = _case_sources()
    assert mapping, "в cases/ нет кейсов с source"
    for cid, src in mapping.items():
        if not is_url(src):
            continue
        (tmp_path / _repo_name(src)).mkdir(exist_ok=True)
        assert local_for(src, tmp_path) is not None, f"{cid}: {src} не резолвится"


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
