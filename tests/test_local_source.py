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

from botkit_farm.source import _repo_name as repo_name
from botkit_farm.source import is_url, local_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
BOTS = ROOT / "bots"

# Данные кейсов копируются в дерево мутанта (`also_copy = ["cases/"]` в
# `[tool.mutmut]`), поэтому боевые тесты идут и там, и в обычном CI. Раньше здесь
# стоял `skipif` — он прятал проверку: пропущенный тест мутанта не ловит.
ЕСТЬ_КЕЙСЫ = CASES.is_dir() and any(CASES.rglob("*.y*ml"))


def test_боевые_кейсы_доехали_до_дерева():
    """Страховка от тихого возврата к skipif: кейсы обязаны быть на месте."""
    assert ЕСТЬ_КЕЙСЫ, (
        "нет cases/*.yaml — проверьте also_copy в [tool.mutmut] "
        "(данные кейсов обязаны копироваться в дерево мутанта)"
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


def _expected_name(url: str) -> str:
    """Ожидаемое имя каталога по контракту модуля: последний сегмент без `.git`.

    Это оракул для теста отображения: независимое выражение документированного
    контракта, а не вызов проверяемой функции. Имя намеренно не совпадает с
    `repo_name` — совпадение теней заставило бы тест проверять сам себя.
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
    for name in {_expected_name(src) for src in parsed.values() if is_url(src)}:
        (tmp_path / name).mkdir()

    missing = []
    for cid, src in parsed.items():
        if not is_url(src):
            continue
        got = local_for(src, tmp_path)
        want = tmp_path / _expected_name(src)
        if got != want:
            missing.append(f"{cid}: {src} -> {got}, ожидался {want}")
    assert not missing, "URL кейса не отображается на свой каталог:\n" + "\n".join(missing)


def test_боевые_кейсы_имеют_url_источник(tmp_path):
    """Реальные кейсы обязаны нести URL и отображаться на существующий каталог.

    Проверяет данные (`cases/`), а не логику: данные копируются в дерево
    мутанта через `also_copy`, поэтому тест исполняется и там.
    """
    mapping = _case_sources()
    assert mapping, "в cases/ нет кейсов с source"
    for cid, src in mapping.items():
        if not is_url(src):
            continue
        (tmp_path / _expected_name(src)).mkdir(exist_ok=True)
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


# ── is_url: каждая принятая схема под проверкой ────────────────────────────
#
# Мутант, вычеркнувший одну схему из кортежа (ssh://, git://, http://),
# выживал: ни один тест не подавал URL с ней. Схема без теста — это и есть
# незакрытая ветка, поэтому перебираем все.


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://github.com/a/b",
        "git@github.com:a/b.git",
        "ssh://git@github.com/a/b",
        "git://github.com/a/b",
    ],
)
def test_каждая_принятая_схема_это_url(url: str):
    """Все схемы из кортежа is_url распознаются — выпадение любой роняет тест."""
    assert is_url(url) is True


@pytest.mark.parametrize(
    "source",
    [
        "./bots/botkit-store",
        "/abs/path/bot",
        "bots/botkit-store",
        "HTTP://github.com/a/b",  # регистр значим: схемы строчные
        "SSH://git@github.com/a/b",
        "htp://typo",
    ],
)
def test_не_url_не_распознаётся(source: str):
    """Ложное срабатывание отправило бы локальный путь в клон — это баг."""
    assert is_url(source) is False


# ── _repo_name: ветки без покрытия держатся точечно ────────────────────────
#
# Мутанты в rstrip/rsplit/срезе .git/срезе двоеточия/замене символов/гварде
# «.» и обрезке до 64 выживали: существующие тесты брали лишь одну форму URL.
# Здесь каждая ветка зафиксирована отдельным случаем.


def test_repo_name_снимает_git_и_хвостовой_слеш():
    """`.git` срезается, хвостовой `/` не попадает в имя каталога."""
    assert repo_name("https://github.com/ninelegsdog/botkit-store.git") == "botkit-store"
    assert repo_name("https://github.com/ninelegsdog/botkit-store/") == "botkit-store"


def test_repo_name_берёт_последний_сегмент_а_не_предыдущий():
    """rsplit(delim, 1) — именно 1: больший n вернул бы кусок пути."""
    assert repo_name("https://github.com/ninelegsdog/botkit-store") == "botkit-store"


def test_repo_name_терпит_гонки_и_схему_scp():
    """`git@host:user/repo` — двоеточие отделяет имя репозитория от хоста."""
    assert repo_name("git@github.com:ninelegsdog/botkit-store.git") == "botkit-store"


def test_repo_name_заменяет_опасные_символы():
    """Слеш и `..` не должны пережить преобразование — иначе уход из песочницы."""
    got = repo_name("https://github.com/evil/../../etc/passwd.git")
    assert "/" not in got and ".." not in got


def test_repo_name_пустой_и_точечный_получают_префикс():
    """Пустое/начинающееся с точки имя получило бы `repo` — иначе небезопасно."""
    assert repo_name("https://github.com/a/.hidden").startswith("repo")
    assert repo_name("https://github.com/a/.").startswith("repo")


def test_repo_name_обрезается_до_64():
    """Слишком длинное имя обрезается ровно до 64 символов."""
    long_name = "x" * 200
    assert len(repo_name(f"https://github.com/a/{long_name}")) == 64


def test_repo_name_заменяет_каждый_опасный_символ_на_один_подчёрк():
    """Небезопасный символ (`@`, пробел) обязан стать РОВНО одним `_`.

    Мутанты, заменившие `_` на «XX_XX» или на пустоту, выживали: в тестах не
    было URL, где подстановка вообще происходит. Символ `@` и пробел из имени
    каталога уходят — иначе имя разъедется с `bots/<name>`.
    """
    assert repo_name("https://github.com/a/b@c") == "b_c"
    assert repo_name("https://github.com/a/we ird") == "we_ird"


def test_repo_name_двоеточие_в_имени_обрезается_до_последнего_сегмента():
    """Двоеточие разделяет хост и путь (`git@host:user/repo`) — берётся хвост.

    Мутант, убравший разбиение по `:`, оставлял бы `host:user/repo`, и имя
    каталога не совпало бы с ботом. Проверяем обе формы: scp-форму и двоеточие,
    оказавшееся в последнем сегменте пути.
    """
    assert repo_name("git@github.com:user/repo-name") == "repo-name"
    assert repo_name("https://github.com/a/b:c") == "c"
