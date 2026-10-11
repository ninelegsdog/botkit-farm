"""Песочница исполнения: bubblewrap вокруг процесса бота.

Карантин в [[quarantine]] ограничивает бота средствами интерпретатора: запись
за пределы рабочих каталогов и сеть мимо loopback. Против кода, который эти
средства обходит (`ctypes`, прямой `syscall`), нужен запрет на уровне ядра —
это и делает `bubblewrap`: он собирает namespace, где видны только каталоги
прогона, и всё остальное просто не существует.

Что песочница умеет и чего не умеет:

* **умеет** — не дать прочитать `$HOME`, `.ssh`, чужие репозитории и править
  систему; ограничение приходит от ядра, а не от договорённости;
* **не умеет** — отрезать сеть. Фейковый Bot API и Redis стоят на loopback
  *вне* namespace, поэтому сеть приходится оставлять общей (`--share-net`),
  а запрет исходящих соединений делает карантин.

`bubblewrap` есть в каждом дистрибутиве и не требует прав root, потому что
работает через user namespaces. Если namespaces выключены в ядре, `unshare`
не поможет — и `doctor` честно скажет, что изоляции нет.

Запуск песочницы **не включён по умолчанию локально**: у разработчика на ноутбуке
добавляется слой, который мешает отладке, и ломает прогон бота, которому нужен
доступ к его собственному каталогу. В CI карантин включается принудительно
(переменная `BOTKIT_FARM_SANDBOX=1`), потому что там код бота чужой и появляется
без ведома того, кто его принёс.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from collections.abc import Sequence

# Каталоги системы, которые нужны интерпретатору. Монтируются только на чтение:
# бот должен видеть python и библиотеки, но не переписывать их.
_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt/python")

# Переменная, которой CI и владелец включают песочницу.
SWITCH = "BOTKIT_FARM_SANDBOX"


def _abs(name: str) -> str:
    """Полный путь к бинарнику.

    subprocess ищет программу по PATH, а sandbox — не то место, где стоит
    подставлять путь из чужой переменной окружения: ищем сами через shutil.
    """
    return shutil.which(name) or name


def available_backend() -> str | None:
    """Имя первого доступного песочника или None.

    Порядок — от дешёвого к дорогому: bubblewrap стоит в каждом дистрибутиве и
    поднимается за миллисекунды, Docker/Podman тяжелее, но дают полноценную
    изоляцию и сетевые правила.
    """
    for backend, probe in (
        ("bubblewrap", "bwrap"),
        ("unshare", "unshare"),
        ("podman", "podman"),
        ("docker", "docker"),
    ):
        if shutil.which(probe) is None:
            continue
        if backend == "unshare" and not unshare_works():
            continue
        if backend in ("podman", "docker") and not container_works(backend):
            continue
        return backend
    return None


def unshare_works() -> bool:
    """Есть ли unshare, который действительно создаёт namespace.

    Бинарник в системе может быть, а user namespaces — выключены в ядре. Тогда
    подсказка «изоляция доступна» была бы враньём, поэтому проверяем запуск,
    а не наличие файла.
    """
    try:
        done = subprocess.run(  # noqa: S603 — команда и аргументы константы
            [_abs("unshare"), "--user", "--map-root-user", "true"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def container_works(backend: str) -> bool:
    """Есть ли рабочий демон: бинарник без запущенного dockerd не изоляция."""
    try:
        done = subprocess.run(  # noqa: S603 — backend из константы модуля
            [_abs(backend), "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def enabled(env: dict[str, str] | None = None) -> bool:
    """Включена ли песочница в текущем прогоне."""
    source = env if env is not None else dict(os.environ)
    return source.get(SWITCH, "").strip().lower() in ("1", "true", "yes", "on")


def wrap(
    cmd: Sequence[str],
    *,
    binds: Sequence[pathlib.Path],
    chdir: pathlib.Path | None = None,
) -> list[str]:
    """Обернуть команду в bubblewrap.

    `binds` — каталоги, которые бот увидит на тех же путях и с теми же правами.
    Пути монтируются по именам, а не «куда получилось»: бот читает свой `pyproject`,
    и переезд его сломал бы так же, как отсутствие.
    """
    argv = [_abs("bwrap")]
    for system in _SYSTEM_DIRS:
        if os.path.isdir(system):
            argv += ["--ro-bind", system, system]
    for path in binds:
        resolved = pathlib.Path(path).resolve()
        argv += ["--bind", str(resolved), str(resolved)]
    argv += [
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--unshare-all",
        # Сеть общая: фейковый API и Redis живут на loopback снаружи namespace,
        # а запрет исходящих соединений обеспечивает карантин.
        "--share-net",
        "--die-with-parent",
    ]
    if chdir is not None:
        argv += ["--chdir", str(pathlib.Path(chdir).resolve())]
    argv += list(cmd)
    return argv


def wrap_or_passthrough(
    cmd: Sequence[str],
    *,
    binds: Sequence[pathlib.Path],
    chdir: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Обернуть команду, если песочница включена; иначе выполнить как есть.

    Включённая, но недоступная песочница — это ошибка, а не тихий отказ: прогон
    без изоляции выглядел бы ровно так же, как защищённый, и обманывал бы всех,
    кто на него посмотрит.
    """
    if not enabled(env):
        return list(cmd)
    if shutil.which("bwrap") is None:
        from botkit_farm.security import SecurityError

        raise SecurityError(
            f"{SWITCH}=1, но bubblewrap не найден. Либо поставьте его "
            f"(apt install bubblewrap), либо снимите переменную — прогон пойдёт "
            f"без песочницы."
        )
    return wrap(cmd, binds=binds, chdir=chdir)


def describe() -> str:
    """Что сейчас доступно — одной строкой для `doctor`."""
    backend = available_backend()
    if backend is None:
        return "песочник недоступен"
    state = "включена" if enabled() else "доступна, но выключена"
    return f"{backend}: {state} ({SWITCH}=1 включает)"


__all__ = [
    "SWITCH",
    "available_backend",
    "container_works",
    "describe",
    "enabled",
    "unshare_works",
    "wrap",
    "wrap_or_passthrough",
]
