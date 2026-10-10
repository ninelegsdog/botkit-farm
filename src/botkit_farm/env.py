"""Окружение для запуска кода бота.

Отдельное venv на каждый бот, а не общее на ферму. Причина не в аккуратности:
у девяти ботов разные зависимости (старый Kit v2 тянет `pydantic-settings` и
`sentry-sdk`, новый — `yookassa`), и общее окружение означало бы, что версия
одного бота ломает другой. Ферма ничего не знает про эти библиотеки — она
устанавливает ровно то, что бот объявил, и импортирует его как есть.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import venv
from dataclasses import dataclass

from botkit_farm import security


@dataclass
class Env:
    """Готовое окружение бота."""

    path: pathlib.Path  # каталог .venv
    python: pathlib.Path  # интерпретатор
    created: bool


def _project_root(repo: pathlib.Path) -> pathlib.Path | None:
    for cand in (repo / "pyproject.toml", repo / "requirements.txt"):
        if cand.exists():
            return cand
    return None


def ensure_env(repo: pathlib.Path, cache: pathlib.Path, *, timeout: int = 900) -> Env:
    """Создать (или переиспользовать) venv бота и поставить его зависимости.

    Повторный запуск ничего не делает: окружение лежит в `cache/<имя бота>`,
    и venv создаётся один раз. Это не только быстрее — прогон с уже
    установленными зависимостями детерминирован.
    """
    name = repo.name
    env_dir = security.safe_join(cache, name)
    py = env_dir / "bin" / "python"

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
            _run(args, timeout=timeout, what=f"зависимости бота {name}")
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
    env_dir = security.safe_join(cache, repo.name)
    py = env_dir / "bin" / "python"
    return py if py.exists() else pathlib.Path(sys.executable)


__all__ = ["Env", "ensure_env", "python_of"]
