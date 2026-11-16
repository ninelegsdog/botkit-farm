"""Кэш окружений бота по хэшу зависимостей (ТЗ D5).

Пересборка venv — самый долгий этап прогона, а переиспользовать чужое окружение
опасно: бот получит версии, которых уже не объявлял. Проверки держат обе стороны:
хэш меняется от содержимого файлов и не меняется от имени каталога.
"""

from __future__ import annotations

import pathlib
import venv
from types import SimpleNamespace
from typing import Any

import pytest

from botkit_farm import env, security


def _bot(root: pathlib.Path, *, pyproject: str | None = None, requirements: str | None = None):
    root.mkdir(parents=True, exist_ok=True)
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if requirements is not None:
        (root / "requirements.txt").write_text(requirements, encoding="utf-8")
    return root


# ── хэш зависимостей ────────────────────────────────────────────────────────


def test_хэш_меняется_от_содержимого_pyproject(tmp_path) -> None:
    a = _bot(tmp_path / "bot", pyproject="[project]\ndependencies=['x']\n")
    b = _bot(tmp_path / "other", pyproject="[project]\ndependencies=['y']\n")
    assert env.deps_digest(a) != env.deps_digest(b)


def test_хэш_не_зависит_от_имени_каталога(tmp_path) -> None:
    a = _bot(tmp_path / "one", pyproject="[project]\ndependencies=['x']\n")
    b = _bot(tmp_path / "two", pyproject="[project]\ndependencies=['x']\n")
    assert env.deps_digest(a) == env.deps_digest(b)


def test_хэш_меняется_от_содержимого_requirements(tmp_path) -> None:
    a = _bot(tmp_path / "one", requirements="flask==1\n")
    b = _bot(tmp_path / "two", requirements="flask==2\n")
    assert env.deps_digest(a) != env.deps_digest(b)


def test_хэш_учитывает_оба_файла(tmp_path) -> None:
    base = _bot(tmp_path / "one", pyproject="[project]\n", requirements="flask==1\n")
    changed = _bot(tmp_path / "two", pyproject="[project]\n", requirements="flask==2\n")
    assert env.deps_digest(base) != env.deps_digest(changed)


def test_имя_каталога_начинается_с_имени_бота(tmp_path) -> None:
    bot = _bot(tmp_path / "booking", pyproject="[project]\n")
    name = env.env_dir_name(bot)
    assert name.startswith("booking-")
    assert name.endswith(env.deps_digest(bot))


# ── ensure_env ──────────────────────────────────────────────────────────────


class _Сборка:
    """Подменяет venv и pip: запоминает вызовы, но ничего не ставит."""

    def __init__(self, cache: pathlib.Path) -> None:
        self.cache = cache
        self.install: list[list[str]] = []

    def builder(self) -> Any:
        def create(path: pathlib.Path) -> None:
            (path / "bin").mkdir(parents=True, exist_ok=True)
            (path / "bin" / "python").write_text("", encoding="utf-8")

        def factory(**_kw: Any) -> Any:
            return SimpleNamespace(create=create)

        return factory

    def run(self, args: list[str], *, timeout: int, what: str) -> None:
        self.install.append(args)


@pytest.fixture
def сборка(tmp_path, monkeypatch) -> _Сборка:
    monkeypatch.setattr(security, "allow_egress", lambda: None)
    monkeypatch.setattr(security, "block_egress", lambda: None)
    s = _Сборка(tmp_path / "envs")
    monkeypatch.setattr(env, "venv", SimpleNamespace(EnvBuilder=s.builder()))
    monkeypatch.setattr(env, "_run", s.run)
    return s


def test_готовое_окружение_переиспользуется(tmp_path, сборка) -> None:
    bot = _bot(tmp_path / "bot", pyproject="[project]\n")
    cache = tmp_path / "envs"
    key = env.env_dir_name(bot)
    py = cache / key / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")

    result = env.ensure_env(bot, cache)

    assert result.created is False
    assert result.python == py.resolve()
    assert сборка.install == []  # ничего не пересобиралось


def test_пустое_окружение_собирается_и_ставит_pyproject(tmp_path, сборка) -> None:
    bot = _bot(tmp_path / "bot", pyproject="[project]\ndependencies=['x']\n")
    cache = tmp_path / "envs"

    result = env.ensure_env(bot, cache)

    assert result.created is True
    assert result.python == (cache / env.env_dir_name(bot) / "bin" / "python")
    assert result.python.exists()
    (args,) = сборка.install
    assert args[-2:] == ["-e", str(bot)]


def test_requirements_ставится_через_r(tmp_path, сборка) -> None:
    bot = _bot(tmp_path / "bot", requirements="flask==1\n")
    cache = tmp_path / "envs"

    env.ensure_env(bot, cache)

    (args,) = сборка.install
    assert args[-2:] == ["-r", str(bot / "requirements.txt")]


def test_без_объявленных_зависимостей_pip_не_зовётся(tmp_path, сборка) -> None:
    bot = _bot(tmp_path / "bot")
    cache = tmp_path / "envs"

    result = env.ensure_env(bot, cache)

    assert result.created is True
    assert сборка.install == []


def test_смена_зависимостей_даёт_новое_окружение(tmp_path, сборка) -> None:
    bot = _bot(tmp_path / "bot", pyproject="[project]\ndependencies=['x']\n")
    cache = tmp_path / "envs"
    first = env.ensure_env(bot, cache)

    (bot / "pyproject.toml").write_text("[project]\ndependencies=['y']\n", encoding="utf-8")
    second = env.ensure_env(bot, cache)

    assert first.path != second.path
    assert second.created is True
    assert len(сборка.install) == 2


def test_сборка_берётся_под_блокировку(tmp_path, сборка) -> None:
    bot = _bot(tmp_path / "bot", pyproject="[project]\n")
    cache = tmp_path / "envs"

    env.ensure_env(bot, cache)

    assert (cache / f".{env.env_dir_name(bot)}.lock").exists()


def test_окружение_собирается_со_ссылкой_а_не_копией(tmp_path, monkeypatch) -> None:
    """venv-питон — ссылка на базовый интерпретатор, не копия.

    Копия теряет $ORIGIN-контекст и не находит общую libpython базового питона:
    на Ubuntu 26.04 её нет и в /usr, поэтому под песочницей бот умирал на
    загрузчике. Контрольная сборка с дефолтным EnvBuilder показывает, что
    свойство не было свободным — дефолт даёт копию, а не ссылку.
    """
    monkeypatch.setattr(security, "allow_egress", lambda: None)
    monkeypatch.setattr(security, "block_egress", lambda: None)
    bot = _bot(tmp_path / "bot", pyproject="[project]\n")  # без зависимостей pip не зовётся
    cache = tmp_path / "envs"

    result = env.ensure_env(bot, cache)

    assert result.python.is_symlink()

    control = tmp_path / "control"
    venv.EnvBuilder(with_pip=False, clear=True).create(control)
    assert not (control / "bin" / "python").is_symlink()


# ── base_prefix_of ──────────────────────────────────────────────────────────


def test_базовый_префикс_читается_из_pyvenv_cfg(tmp_path) -> None:
    base_bin = tmp_path / "base" / "bin"
    base_bin.mkdir(parents=True)
    env_dir = tmp_path / "envs" / "store-abc"
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "pyvenv.cfg").write_text(f"home = {base_bin}\n", encoding="utf-8")

    assert env.base_prefix_of(env_dir / "bin" / "python") == tmp_path / "base"


def test_без_pyvenv_cfg_биндить_нечего(tmp_path) -> None:
    """Питон без pyvenv.cfg сам себе база — отдельный бинд не нужен."""
    assert env.base_prefix_of(tmp_path / "py") is None


def test_битый_home_даёт_отсутствие_префикса(tmp_path) -> None:
    """home из pyvenv.cfg указывает в никуда — префикса нет, бинд не строится."""
    env_dir = tmp_path / "envs" / "store-abc"
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "pyvenv.cfg").write_text("home = /несуществующий/bin\n", encoding="utf-8")

    assert env.base_prefix_of(env_dir / "bin" / "python") is None


# ── python_of ───────────────────────────────────────────────────────────────


def test_python_of_находит_готовое_окружение(tmp_path) -> None:
    bot = _bot(tmp_path / "bot", pyproject="[project]\n")
    cache = tmp_path / "envs"
    py = cache / env.env_dir_name(bot) / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")

    assert env.python_of(bot, cache) == py


def test_python_of_без_окружения_отдаёт_текущий(tmp_path) -> None:
    import sys

    bot = _bot(tmp_path / "bot", pyproject="[project]\n")
    assert env.python_of(bot, tmp_path / "envs") == pathlib.Path(sys.executable)
