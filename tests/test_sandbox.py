"""Тесты песочницы: какие команды собираются и когда прогон падает.

Проверяется не «запустится ли bwrap» (для этого нужен настоящий бот), а
правильность решения: какие каталоги видны, что происходит при выключенной
песочнице и что будет, если её включили, а поставить забыли.
"""

from __future__ import annotations

import pathlib

import pytest

from botkit_farm import sandbox
from botkit_farm.security import SecurityError


def _подменить_бинарники(monkeypatch, found: dict[str, str]) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda n: found.get(n))


# ── доступность ─────────────────────────────────────────────────────────────


def test_песочник_не_найден_когда_пусто(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {})

    assert sandbox.available_backend() is None


def test_bubblewrap_предпочитается_unshare(monkeypatch) -> None:
    """Дешёвый backend выигрывает: bwrap поднимается за миллисекунды."""
    _подменить_бинарники(monkeypatch, {"bwrap": "/usr/bin/bwrap", "unshare": "/usr/bin/unshare"})

    assert sandbox.available_backend() == "bubblewrap"


def test_unshare_без_namespace_считается_недоступным(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {"unshare": "/usr/bin/unshare"})
    monkeypatch.setattr(sandbox, "unshare_works", lambda: False)

    assert sandbox.available_backend() is None


def test_контейнер_с_незапущенным_демоном_не_считается_изоляцией(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {"docker": "/usr/bin/docker"})
    monkeypatch.setattr(sandbox, "container_works", lambda backend: False)

    assert sandbox.available_backend() is None


def test_podman_с_работающим_демоном_доступен(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {"podman": "/usr/bin/podman"})
    monkeypatch.setattr(sandbox, "container_works", lambda backend: True)

    assert sandbox.available_backend() == "podman"


# ── переключатель ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_переключатель_понимает_человеческие_значения(value: str) -> None:
    assert sandbox.enabled({sandbox.SWITCH: value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_переключатель_выключен_по_умолчанию(value: str) -> None:
    assert sandbox.enabled({sandbox.SWITCH: value}) is False


def test_без_переменной_песочница_выключена() -> None:
    assert sandbox.enabled({}) is False


def test_окружение_берётся_из_os_environ(monkeypatch) -> None:
    monkeypatch.delenv(sandbox.SWITCH, raising=False)
    assert sandbox.enabled() is False
    monkeypatch.setenv(sandbox.SWITCH, "1")
    assert sandbox.enabled() is True


# ── сборка команды ──────────────────────────────────────────────────────────


def test_команда_оборачивается_с_рабочими_каталогами(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox.os.path, "isdir", lambda p: True)
    bot = tmp_path / "bot"
    work = tmp_path / ".work"
    bot.mkdir()
    work.mkdir()

    argv = sandbox.wrap([str(bot / "python"), "host.py"], binds=[bot, work], chdir=bot)

    assert argv[0].endswith("bwrap")
    assert str(bot.resolve()) in argv
    assert str(work.resolve()) in argv
    assert argv[-2:] == [str(bot / "python"), "host.py"]


def test_системные_каталоги_только_на_чтение(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox.os.path, "isdir", lambda p: p.startswith("/usr"))

    argv = sandbox.wrap(["python", "host.py"], binds=[tmp_path])

    ro = [argv[i + 1] for i, a in enumerate(argv) if a == "--ro-bind"]
    assert "/usr" in ro
    assert "/etc" not in ro


def test_сеть_остаётся_общей(tmp_path) -> None:
    """Фейковый API и Redis живут на loopback снаружи namespace."""
    argv = sandbox.wrap(["python", "host.py"], binds=[tmp_path])

    assert "--share-net" in argv
    assert "--unshare-all" in argv


def test_родитель_умирает_вместе_с_ботом(tmp_path) -> None:
    """Иначе бот пережил бы ферму и остался висеть в системе."""
    assert "--die-with-parent" in sandbox.wrap(["python", "host.py"], binds=[tmp_path])


def test_отсутствующие_системные_каталоги_пропускаются(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox.os.path, "isdir", lambda p: False)

    argv = sandbox.wrap(["python", "host.py"], binds=[tmp_path])

    assert "--ro-bind" not in argv


def test_без_смены_каталога(tmp_path) -> None:
    assert "--chdir" not in sandbox.wrap(["python", "host.py"], binds=[tmp_path])


# ── решение на запуске ───────────────────────────────────────────────────────


def test_выключенная_песочница_оставляет_команду_как_есть(tmp_path) -> None:
    cmd = ["python", "host.py"]

    assert sandbox.wrap_or_passthrough(cmd, binds=[tmp_path], env={}) == cmd


def test_включённая_песочница_оборачивает(tmp_path, monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {"bwrap": "/usr/bin/bwrap"})
    monkeypatch.setattr(sandbox.os.path, "isdir", lambda p: False)

    argv = sandbox.wrap_or_passthrough(
        ["python", "host.py"], binds=[tmp_path], env={sandbox.SWITCH: "1"}
    )

    assert argv[0] == "/usr/bin/bwrap"
    assert argv[-2:] == ["python", "host.py"]


def test_включённая_но_непоставленная_песочница_это_ошибка(tmp_path, monkeypatch) -> None:
    """Тихий отказ был бы враньём: прогон выглядел бы защищённым."""
    _подменить_бинарники(monkeypatch, {})

    with pytest.raises(SecurityError) as err:
        sandbox.wrap_or_passthrough(
            ["python", "host.py"], binds=[tmp_path], env={sandbox.SWITCH: "1"}
        )

    assert "bubblewrap" in str(err.value)
    assert sandbox.SWITCH in str(err.value)


# ── описание ────────────────────────────────────────────────────────────────


def test_describe_без_песочника(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {})

    assert sandbox.describe() == "песочник недоступен"


def test_describe_называет_состояние(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {"bwrap": "/usr/bin/bwrap"})
    monkeypatch.setenv(sandbox.SWITCH, "1")

    text = sandbox.describe()

    assert "bubblewrap" in text
    assert "включена" in text
    assert sandbox.SWITCH in text


def test_describe_говорит_что_выключено(monkeypatch) -> None:
    _подменить_бинарники(monkeypatch, {"bwrap": "/usr/bin/bwrap"})
    monkeypatch.delenv(sandbox.SWITCH, raising=False)

    assert "выключена" in sandbox.describe()
