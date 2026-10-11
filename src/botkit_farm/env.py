"""Окружение для запуска кода бота.

Отдельное venv на каждый бот, а не общее на ферму. Причина не в аккуратности:
у девяти ботов разные зависимости (старый Kit v2 тянет `pydantic-settings` и
`sentry-sdk`, новый — `yookassa`), и общее окружение означало бы, что версия
одного бота ломает другой. Ферма ничего не знает про эти библиотеки — она
устанавливает ровно то, что бот объявил, и импортирует его как есть.

Ключ кэша — **хэш объявленных зависимостей** (`pyproject.toml`,
`requirements.txt`) вместе с версией интерпретатора (ТЗ D5). Имя бота — только
приставка: пересборка окружения — самый долгий этап прогона, и переиспользовать
venv можно лишь тогда, когда он точно соответствует объявленным зависимостям.
Правка `pyproject.toml` меняет хэш, и окружение собирается заново; правка кода
бота хэш не меняет (для `pyproject` он ставится как `-e`, код подхватывается
на месте).
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import venv
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from botkit_farm import security

# POSIX-блокировка файла. На Windows модуля нет — там и параллельных прогонов
# нет; `Any` здесь честнее, чем `ModuleType | None`: дальше мы только вызываем
# его функции, и проверка `is None` — ветка платформы, а не типов.
fcntl: Any
try:
    import fcntl
except ImportError:  # pragma: no cover - платформенная ветка
    fcntl = None

# Файлы, содержимое которых определяет окружение. Порядок фиксирован, чтобы
# хэш не зависел от обхода каталога.
_DEP_FILES = ("pyproject.toml", "requirements.txt")


@dataclass
class Env:
    """Готовое окружение бота."""

    path: pathlib.Path  # каталог .venv
    python: pathlib.Path  # интерпретатор
    created: bool


def _project_root(repo: pathlib.Path) -> pathlib.Path | None:
    for fname in _DEP_FILES:
        cand = repo / fname
        if cand.exists():
            return cand
    return None


def deps_digest(repo: pathlib.Path) -> str:
    """Хэш зависимостей бота: содержимое файлов + версия интерпретатора.

    Интерпретатор входит в хэш потому, что venv не переносится между версиями
    Python: после апгрейда фермы старый каталог переиспользовать нельзя.
    Отсутствие обоих файлов даёт стабильный хэш от одной метки версии — такой
    бот кэшируется по имени, как и раньше.
    """
    h = hashlib.sha256()
    h.update(f"cp{sys.version_info.major}.{sys.version_info.minor}\0".encode())
    for fname in _DEP_FILES:
        path = repo / fname
        if path.exists():
            h.update(fname.encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()[:16]


def env_dir_name(repo: pathlib.Path) -> str:
    """Имя каталога окружения: имя бота плюс хэш его зависимостей."""
    return f"{repo.name}-{deps_digest(repo)}"


@contextmanager
def _creation_lock(key: str, cache: pathlib.Path) -> Iterator[None]:
    """Не дать двум процессам (воркерам xdist) собирать один venv одновременно.

    `venv.EnvBuilder` пишет в каталог напрямую; параллельная сборка оставила бы
    половину окружения. Блокировка — на каталог кэша, а не на venv: ждём именно
    момент создания.
    """
    if fcntl is None:  # pragma: no cover - платформенная ветка
        yield
        return
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / f".{key}.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def ensure_env(repo: pathlib.Path, cache: pathlib.Path, *, timeout: int = 900) -> Env:
    """Создать (или переиспользовать) venv бота и поставить его зависимости.

    Повторный запуск с теми же зависимостями ничего не делает: окружение лежит
    в `cache/<имя>-<хэш>` и создаётся один раз. Изменение объявленных
    зависимостей даёт новый хэш и новую сборку — прежнее окружение не
    переиспользуется, иначе бот поставил бы версии, которых уже не объявлял.
    """
    key = env_dir_name(repo)
    env_dir = security.safe_join(cache, key)
    py = env_dir / "bin" / "python"

    if py.exists():
        return Env(path=env_dir, python=py, created=False)

    with _creation_lock(key, cache):
        # Второй воркер мог собрать окружение, пока мы ждали блокировку.
        if py.exists():
            return Env(path=env_dir, python=py, created=False)
        security.allow_egress()  # установка пакетов требует сети
        try:
            venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
            root = _project_root(repo)
            if root is not None:
                args = [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
                if root.name == "pyproject.toml":
                    args += ["-e", str(repo)]
                else:
                    args += ["-r", str(root)]
                _run(args, timeout=timeout, what=f"зависимости бота {repo.name}")
            return Env(path=env_dir, python=py, created=True)
        finally:
            security.block_egress()


def _run(args: list[str], *, timeout: int, what: str) -> None:
    """Запустить команду с понятной ошибкой вместо сырого traceback.

    Сообщение должно называть, что именно сломалось: «ModuleNotFoundError:
    pydantic_settings» из лога бота не говорит, что не хватило.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — аргументы фиксированы, ввод не участвует
            args, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise security.SecurityError(f"{what}: установка не уложилась в {timeout} с") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise security.SecurityError(f"{what} не установились:\n" + "\n".join(tail))


def python_of(repo: pathlib.Path, cache: pathlib.Path) -> pathlib.Path:
    """Интерпретатор окружения бота (без установки — что уже есть)."""
    env_dir = security.safe_join(cache, env_dir_name(repo))
    py = env_dir / "bin" / "python"
    return py if py.exists() else pathlib.Path(sys.executable)


__all__ = ["Env", "deps_digest", "ensure_env", "env_dir_name", "python_of"]
