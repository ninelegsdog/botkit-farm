"""Источник кода бота: локальная папка или git-URL, всегда в песочнице.

Разделение намеренное. Кейс указывает `source`, и отсюда код попадает в прогон
двумя путями: локальный путь (чтобы гонять незакоммиченные правки) или клон
(чтобы воспроизвести поведение по конкретному коммиту). Оба кончаются в
`bots/` — и это единственное место, откуда запускается код. Никакого «clone куда
попало».
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from dataclasses import dataclass

from botkit_farm import security


@dataclass
class Source:
    """Где лежит код бота после подготовки."""

    path: pathlib.Path
    origin: str  # исходная строка из кейса
    was_cloned: bool


def is_url(source: str) -> bool:
    """Отличить git-URL от локального пути."""
    return source.startswith(("http://", "https://", "git@", "ssh://", "git://"))


def prepare(source: str, sandbox: pathlib.Path, *, ref: str | None = None) -> Source:
    """Подготовить код бота: скопировать или склонировать в песочницу.

    Локальный путь копируется, а не используется на месте. Причина в том, что
    бот во время прогона пишет в свой каталог (база, логи, миграции) — и эти
    записи не должны оказываться в рабочем дереве разработчика.
    """
    sandbox.mkdir(parents=True, exist_ok=True)

    if is_url(source):
        security.check_repo_url(source, sandbox)
        name = _repo_name(source)
        dst = security.safe_join(sandbox, name)
        if dst.exists():
            shutil.rmtree(dst)
        _git_clone(source, dst, ref)
        return Source(path=dst, origin=source, was_cloned=True)

    local = pathlib.Path(source).expanduser()
    if not local.exists():
        raise security.SecurityError(
            f"источник не найден: {local}. Укажите существующий путь или git-URL."
        )
    if not local.is_dir():
        raise security.SecurityError(f"источник не каталог: {local}")

    name = local.name
    dst = security.safe_join(sandbox, name)
    if dst.resolve() == local.resolve():
        # Уже песочница: копия в себя означала бы, что прогон сотрёт то, что
        # разработчик качает (а это стирание его рабочего дерева).
        return Source(path=local, origin=source, was_cloned=False)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        local,
        dst,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "venv", "__pycache__", "*.pyc", "node_modules"
        ),
    )
    if ref and (dst / ".git").exists():
        _git("-C", str(dst), "checkout", ref)
    return Source(path=dst, origin=source, was_cloned=False)


def _repo_name(url: str) -> str:
    """Имя каталога из URL, очищенное от путевых символов.

    Ни слеша, ни «..» в имени быть не должно: иначе клон уедет из песочницы.
    """
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    tail = tail.split(":")[-1]  # git@host:user/repo.git
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in tail)
    if not safe or safe.startswith("."):
        safe = "repo" + safe
    return safe[:64]


def _git_clone(url: str, dst: pathlib.Path, ref: str | None) -> None:
    # Сеть на время клона разрешена, потом запрещается обратно: прогон бота
    # офлайн. Иначе «локальный стенд» тянет что-то извне в момент запуска.
    security.allow_egress()
    try:
        _git("clone", "--depth", "1", *(["--branch", ref] if ref else []), url, str(dst))
    finally:
        security.block_egress()


def _git(*args: str) -> None:
    proc = subprocess.run(  # noqa: S603 — аргументы фиксированы, ввод не участвует
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        raise security.SecurityError(
            f"git {' '.join(args)} не удался: {(proc.stderr or proc.stdout).strip()[:400]}"
        )


__all__ = ["Source", "is_url", "prepare"]
