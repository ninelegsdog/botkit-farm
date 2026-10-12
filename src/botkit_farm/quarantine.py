"""Карантин: что код бота может делать, а что нет — и как об этом узнать.

Ферма обещает «любого бота». Бот исполняется с правами владельца, и пока это
так, обещание держится на честном слове: чужой код может создать файл в `$HOME`,
положить что-нибудь в `~/.ssh/authorized_keys` или уйти в сеть. Два обрыва,
которые дешевле всего закрыть здесь:

1. **Запись вне рабочих каталогов.** Каталоги бота, база и временная папка
   прогона разрешены; всё остальное — нет. Чтение не трогаем: боту нужно читать
   свой код и библиотеки.
2. **Исходящие соединения.** Только loopback: фейковый Bot API и Redis стоят
   там, а наружу стенду ходить незачем.

Что это и что это не:

* **Это** — запрет на уровне Python внутри процесса бота. Работает везде, не
  требует прав и ничего не стоит.
* **Это не** — защита от кода, который намеренно обходит интерпретатор
  (`ctypes`, прямой `syscall`). Против такого нужен `bubblewrap`, и это
  отдельный слой ([[безопасность/sec-model]]).

Отказ здесь всегда с текстом: `PermissionError` с указанием пути и списка
разрешённых каталогов. Бот обычно ловит исключение сам и падает красиво, но
даже если нет — ферма читает список нарушений и показывает его в отчёте.
"""

from __future__ import annotations

import builtins
import io
import os
import socket
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from botkit_farm.security import _is_local as _is_local_host  # noqa: PLC2701

# Флаги os.open, которые означают запись. Чтение (O_RDONLY = 0) сюда не входит
# намеренно: читать бот должен где угодно.
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

# Режимы open(), которые означают запись.
_WRITE_MODES = frozenset("wax+")


@dataclass(frozen=True, slots=True)
class Violation:
    """Одна попытка сделать что-то запрещённое."""

    op: str  # "open" | "os.open" | "mkdir" | "connect"
    path: str
    detail: str = ""

    def __str__(self) -> str:
        """Наружение одной строкой — так оно попадёт в результат кейса."""
        where = f"{self.path}: {self.detail}" if self.detail else self.path
        return f"{self.op} → {where}"


class Quarantine:
    """Запреты на запись вне рабочих каталогов и на сеть мимо loopback.

    Объект один на процесс бота: он же копит нарушения, и его состояние нужно
    прочитать после прогона, поэтому нарушения лежат в памяти, а не в файле.
    """

    def __init__(self, roots: Iterable[str | os.PathLike[str]]) -> None:
        """Подготовить карантин: запомнить оригиналы и привести пути к реальным.

        Оригиналы перехватываемых функций запоминаются здесь, а не при первой
        подмене: иначе второй `install()` запомнил бы уже обёртку и карантин
        внутри карантина.
        """
        # Каталоги приводятся к реальным путям один раз: бот делает os.chdir и
        # symlink'и, и сравнение по неразрешённому пути обошлось бы в дыру.
        self.roots: tuple[str, ...] = tuple(sorted({os.path.realpath(str(root)) for root in roots}))
        self.violations: list[Violation] = []
        self._installed = False

    # ── проверка пути ──────────────────────────────────────────────────────

    def allows(self, path: str | os.PathLike[str] | int) -> bool:
        """Разрешена ли запись по этому пути.

        Дескриптор (int) пропускается: у него уже нет имени, и проверять
        нечего — файл открыт раньше и разрешён был тогда.
        """
        if isinstance(path, int):
            return True
        try:
            resolved = os.path.realpath(str(path))
        except (OSError, ValueError):
            # Неразрешимый путь — тоже не должно проходить молча.
            return False
        return any(resolved == root or resolved.startswith(root + os.sep) for root in self.roots)

    def _deny(self, op: str, path: Any, detail: str = "") -> None:
        where = str(path)
        self.violations.append(Violation(op=op, path=where, detail=detail))
        roots = ", ".join(self.roots) or "(пусто)"
        raise PermissionError(
            f"ферма запретила {op} по пути {where!r}: писать можно только в "
            f"рабочие каталоги прогона ({roots}). Бот исполняется с правами "
            f"владельца, и запись в его домашний каталог — это запись в машину "
            f"разработчика. Если боту это нужно, положите каталог в эти пути "
            f"через переменную BOTKIT_FARM_QUARANTINE." + (f" ({detail})" if detail else "")
        )

    def _check(self, op: str, path: str | os.PathLike[str] | int, detail: str = "") -> None:
        if not self.allows(path):
            self._deny(op, path, detail)

    # ── установка ──────────────────────────────────────────────────────────

    def install(self) -> Quarantine:
        """Включить запреты. Повторный вызов ничего не меняет."""
        if self._installed:
            return self
        _remember()
        self._install_paths()
        self._install_egress()
        self._installed = True
        return self

    def disarm(self) -> None:
        """Снять запреты.

        Нужен не для безопасности, а для тестов и для финальной записи отчёта:
        сам карантин должен иметь право дописать свой же файл.
        """
        if not self._installed:
            return
        builtins.open = _ORIGINALS["open"]
        io.open = _ORIGINALS["io_open"]
        os.open = _ORIGINALS["os_open"]
        os.mkdir = _ORIGINALS["mkdir"]
        os.makedirs = _ORIGINALS["makedirs"]
        socket.socket.connect = _ORIGINALS["connect"]  # type: ignore[method-assign]
        socket.create_connection = _ORIGINALS["create_connection"]
        self._installed = False

    def _install_paths(self) -> None:
        guard = self

        def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, E501
            if any(ch in _WRITE_MODES for ch in mode):
                guard._check("open", file, f"режим {mode!r}")
            return _ORIGINALS["open"](file, mode, *args, **kwargs)

        def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:  # noqa: ANN401, E501
            if flags & _WRITE_FLAGS:
                guard._check("os.open", path, f"флаги {flags:#o}")
            return int(_ORIGINALS["os_open"](path, flags, *args, **kwargs))

        def guarded_mkdir(path: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            guard._check("mkdir", path)
            _ORIGINALS["mkdir"](path, *args, **kwargs)

        def guarded_makedirs(name: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            guard._check("makedirs", name)
            _ORIGINALS["makedirs"](name, *args, **kwargs)

        builtins.open = guarded_open
        io.open = guarded_open
        os.open = guarded_os_open
        os.mkdir = guarded_mkdir
        os.makedirs = guarded_makedirs

    def _install_egress(self) -> None:
        guard = self

        def guarded_connect(sock: socket.socket, address: Any) -> Any:  # noqa: ANN401
            host = address[0] if isinstance(address, tuple) and address else address
            if not (isinstance(host, str) and _is_local_host(host)):
                guard.violations.append(
                    Violation(op="connect", path=str(address), detail="только loopback")
                )
                raise PermissionError(
                    f"ферма заблокировала исходящее подключение к {address!r}: стенд "
                    f"работает офлайн. Бот должен обращаться только к поддельному API "
                    f"на 127.0.0.1 и к локальному Redis."
                )
            return _ORIGINALS["connect"](sock, address)

        def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, E501
            host = address[0] if isinstance(address, tuple) and address else address
            if not (isinstance(host, str) and _is_local_host(host)):
                guard.violations.append(
                    Violation(op="connect", path=str(address), detail="только loopback")
                )
                raise PermissionError(
                    f"ферма заблокировала исходящее подключение к {address!r}: стенд "
                    f"работает офлайн."
                )
            return _ORIGINALS["create_connection"](address, *args, **kwargs)

        socket.socket.connect = guarded_connect  # type: ignore[assignment, method-assign]
        socket.create_connection = guarded_create_connection

    # ── отчёт ──────────────────────────────────────────────────────────────

    def summary(self) -> list[str]:
        """Нарушения строками — так они попадают в результат кейса."""
        return [str(v) for v in self.violations]

    def describe(self) -> str:
        """Что разрешено и что запрещено — для диагностики `doctor`."""
        roots = "\n  ".join(self.roots) or "(пусто)"
        return f"карантин: запись разрешена только в\n  {roots}"


# Оригиналы перехватываемых функций. Модуль импортируется один раз, но
# `install()` можно вызвать повторно (например, два прогона в одном процессе),
# и тогда перехватываться станет уже обёртка — карантин внутри карантина.
_ORIGINALS: dict[str, Any] = {}


def _remember() -> None:
    """Запомнить функции-оригиналы до первой подмены."""
    if not _ORIGINALS:
        _ORIGINALS.update(
            {
                "open": builtins.open,
                "io_open": io.open,
                "os_open": os.open,
                "mkdir": os.mkdir,
                "makedirs": os.makedirs,
                "connect": socket.socket.connect,
                "create_connection": socket.create_connection,
            }
        )


def default_roots(bot_dir: str | os.PathLike[str], *extra: str | os.PathLike[str]) -> list[str]:
    """Каталоги, которые бот может писать без вопросов.

    Кроме каталога бота и базы сюда входит системный временный каталог:
    половина библиотек пишет туда при первом же импорте, и запрет на
    `tempfile` выглядел бы как «бот сломан», а не как «бот писал не туда».

    Пути разрешаются здесь же, а не только внутри `Quarantine`. Разница видна
    на macOS, где `/var` — симлинок в `/private/var`: возвращённый список
    обещал бы каталоги, которых на самом деле нет, и сравнение «разрешён ли
    этот путь» отвечало бы «нет».
    """
    roots: list[str] = [str(bot_dir), *map(str, extra)]
    roots.extend(os.environ.get("BOTKIT_FARM_QUARANTINE", "").split(os.pathsep))
    tmp = tempfile.gettempdir()
    roots.append(tmp)
    return [os.path.realpath(root) for root in roots if root]


def arm(
    bot_dir: str | os.PathLike[str],
    *extra: str | os.PathLike[str],
) -> Quarantine:
    """Включить карантин в текущем процессе и вернуть его.

    Вызывается один раз в начале работы хоста, до импорта кода бота: пока
    модуль бота не импортирован, он не успел ничего написать.
    """
    return Quarantine(default_roots(bot_dir, *extra)).install()


__all__ = ["Quarantine", "Violation", "arm", "default_roots"]
