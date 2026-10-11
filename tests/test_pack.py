"""Проверки паков: создание каркаса, поиск, загрузка манифеста.

Пак — это контракт между чужим репозиторием и фермой. Проверки держат две
вещи: каркас заводится там, где ожидает `resolve_pack_path`, и битый `bot.yaml`
отвергается понятной ошибкой, а не проглатывается.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from botkit_farm.pack import (
    MANIFEST_NAME,
    PACK_DIRNAME,
    Manifest,
    load_pack,
    resolve_pack_path,
    scaffold_pack,
)


def _bot_repo(tmp_path: pathlib.Path, *, newgen: bool = False) -> pathlib.Path:
    """Минимальный репозиторий бота для скаффолда."""
    root = tmp_path / "mybot"
    root.mkdir()
    if newgen:
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text(
            "def create_app():\n    return None\n", encoding="utf-8"
        )
    return root


def _write_manifest(pack_dir: pathlib.Path, body: str) -> pathlib.Path:
    pack_dir.mkdir(parents=True, exist_ok=True)
    mf = pack_dir / MANIFEST_NAME
    mf.write_text(textwrap.dedent(body), encoding="utf-8")
    return mf


# ── scaffold ──────────────────────────────────────────────────────────────────


def test_scaffold_создаёт_каркас(tmp_path):
    pack = scaffold_pack(_bot_repo(tmp_path))
    assert pack.root.name == PACK_DIRNAME
    assert (pack.root / MANIFEST_NAME).is_file()
    assert pack.manual_dir.is_dir()
    assert pack.generated_dir.is_dir()
    assert pack.fixtures_dir.is_dir()
    assert pack.manifest.bot.name == "mybot"


def test_scaffold_определяет_поколение(tmp_path):
    pack = scaffold_pack(_bot_repo(tmp_path, newgen=True))
    assert pack.manifest.bot.builder == "newgen"
    assert pack.manifest.bot.reason


def test_scaffold_идемпотентен_и_не_затирает(tmp_path):
    repo = _bot_repo(tmp_path)
    first = scaffold_pack(repo, builder="legacy")
    assert first.manifest.bot.builder == "legacy"
    # Повторный запуск без явного builder: определённое ранее значение сохраняется,
    # а не переписывается на «не опознано».
    second = scaffold_pack(repo)
    assert second.manifest.bot.builder == "legacy"
    assert "builder: legacy" in (repo / PACK_DIRNAME / MANIFEST_NAME).read_text(encoding="utf-8")


# ── resolve / load ────────────────────────────────────────────────────────────


def test_resolve_находит_пак_по_корню_бота(tmp_path):
    repo = _bot_repo(tmp_path)
    scaffold_pack(repo)
    assert resolve_pack_path(repo) == (repo / PACK_DIRNAME).resolve()


def test_resolve_находит_пак_по_каталогу(tmp_path):
    repo = _bot_repo(tmp_path)
    scaffold_pack(repo)
    assert resolve_pack_path(repo / PACK_DIRNAME) == (repo / PACK_DIRNAME).resolve()


def test_resolve_находит_пак_по_имени_в_farm_root(tmp_path):
    farm = tmp_path / "farm"
    packs = farm / "packs" / "mybot"
    _write_manifest(packs, "bot:\n  name: mybot\n")
    assert resolve_pack_path("mybot", farm_root=farm) == packs.resolve()


def test_resolve_отсутствующего_пака_падает(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_pack_path(tmp_path / "nope")


def test_загрузка_пака_читает_манифест(tmp_path):
    repo = _bot_repo(tmp_path)
    scaffold_pack(repo)
    pack = load_pack(repo)
    assert pack.manifest.schema_version == 1
    assert pack.bot_dir == repo.resolve()


def test_битый_манифест_отвергается(tmp_path):
    _write_manifest(tmp_path / PACK_DIRNAME, "bot: 5\n")
    with pytest.raises(ValueError, match="невалидный"):
        load_pack(tmp_path)


def test_лишнее_поле_в_манифесте_отвергается():
    with pytest.raises(ValueError):
        Manifest.model_validate({"bot": {"name": "x"}, "unknown": 1})


# ── происхождение кейса из каталога пака ──────────────────────────────────────


def test_origin_берётся_из_каталога_пака(tmp_path):
    from botkit_farm.case.loader import load_file

    body = textwrap.dedent(
        """
        id: p-manual
        title: Ручной кейс пака
        source: .
        steps:
          - send: /start
            expect:
              text_contains: ["меню"]
        """
    )
    case_dir = tmp_path / "cases" / "manual"
    case_dir.mkdir(parents=True)
    path = case_dir / "p.yaml"
    path.write_text(body, encoding="utf-8")
    assert load_file(path).origin == "manual"


# ── импорт пакета не тянет зависимости фермы ──────────────────────────────────


def test_импорт_бота_пакета_не_тянет_yaml():
    """`import botkit_farm` не должен импортировать `yaml`.

    Этот импорт выполняется и в процессе бота (host.py), где установлены только
    зависимости бота. Ранний реэкспорт пака тянул `yaml`, и любой запуск падал
    на импорте — до кода бота. Проверяем в подпроцессе: в текущем процессе yaml
    уже мог быть загружен другими тестами.
    """
    import subprocess
    import sys

    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    code = "import sys, botkit_farm; sys.exit(1 if 'yaml' in sys.modules else 0)"
    proc = subprocess.run(  # noqa: S603 — аргументы фиксированы, ввод не участвует
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_реэкспорт_пака_доступен_лениво():
    """Имена пака остаются доступны через пакет, но без раннего импорта."""
    import botkit_farm

    assert botkit_farm.load_pack is load_pack
    assert botkit_farm.Manifest is Manifest
