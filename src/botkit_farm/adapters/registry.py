"""Точка расширения адаптеров: встроенные поколения и сторонние плагины.

Двенадцать ботов фермы описываются тремя встроенными адаптерами. Но у любого,
кто приносит чужой бот, композиционный корень свой, и правка кода фермы ради
этого — стоп для OSS. Поэтому `builder` в кейсе принимает не только имя
встроенного адаптера, но и ссылку на модуль:

    builder: botkit-farm.adapter:mypkg.adapters.mine        # attr=build
    builder: botkit-farm.adapter:mypkg.adapters.mine:make   # attr=make

Модуль ищется в окружении **бота** (там, где он установлен), а не фермы: у
чужого бота свои зависимости, и импортировать их из процесса фермы нельзя.

Ссылка разбирается здесь и остаётся строкой до самого подпроцесса. Импортирует
её хост (`runner/host.py`), уже внутри venv бота, — и делает это через
`adapters.contract`, где стоит проверка результата.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Final

#: Префикс ссылки на сторонний адаптер.
PLUGIN_PREFIX: Final = "botkit-farm.adapter:"

#: Встроенные поколения: имя в кейсе → модуль фермы.
BUILTIN: Final[dict[str, str]] = {
    "legacy": "botkit_farm.adapters.legacy",
    "newgen": "botkit_farm.adapters.newgen",
    "reminder": "botkit_farm.adapters.reminder",
}

#: Имя фабрики по умолчанию, если в ссылке атрибут не задан.
DEFAULT_ATTR: Final = "build"

# Полное имя модуля/атрибута: только идентификаторы и точки. Отсекает пробелы,
# слеши и `..`, чтобы строка из кейса не превратилась в путь или в вызов.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


class AdapterError(Exception):
    """Ссылка на адаптер неверна: неизвестное имя или недопустимая форма."""


@dataclasses.dataclass(frozen=True)
class AdapterRef:
    """Разобранная ссылка на адаптер.

    `spec` — то, что написал автор кейса: оно попадает в отчёт, и по нему
    прогон воспроизводится. `module` и `attr` — то, что импортирует хост.
    """

    spec: str
    module: str
    attr: str
    builtin: bool

    @property
    def target(self) -> str:
        """Ссылка для импорта в подпроцессе бота: `module:attr`."""
        return f"{self.module}:{self.attr}"


def _valid_module(name: str) -> bool:
    return bool(_DOTTED.fullmatch(name))


def _valid_attr(name: str) -> bool:
    return bool(_IDENT.fullmatch(name))


def unknown_builder(builder: str) -> str:
    """Текст отказа: что написали и что допускается."""
    return (
        f"неизвестный builder {builder!r}: ожидается одно из {sorted(BUILTIN)} "
        f"или {PLUGIN_PREFIX}<модуль>[:<фабрика>]"
    )


def is_valid(builder: str) -> bool:
    """Проверить форму ссылки без импорта (для валидации кейса).

    Годится только встроенное имя или синтаксически корректная ссылка на
    плагин. Существует ли модуль — здесь не проверяется: он живёт в окружении
    бота, недоступном из процесса фермы.
    """
    if builder in BUILTIN:
        return True
    if not builder.startswith(PLUGIN_PREFIX):
        return False
    target = builder[len(PLUGIN_PREFIX) :]
    module, _, attr = target.partition(":")
    attr = attr or DEFAULT_ATTR
    return _valid_module(module) and _valid_attr(attr)


def resolve(builder: str) -> AdapterRef:
    """Ссылка из кейса → чем её импортировать.

    Бросает `AdapterError` с человеческим текстом: не тот builder даёт ImportError
    внутри подпроцесса бота, и он выглядел бы как баг бота, а не кейса.
    """
    if builder in BUILTIN:
        return AdapterRef(builder, BUILTIN[builder], DEFAULT_ATTR, True)
    if builder.startswith(PLUGIN_PREFIX):
        target = builder[len(PLUGIN_PREFIX) :]
        module, _, attr = target.partition(":")
        attr = attr or DEFAULT_ATTR
        if _valid_module(module) and _valid_attr(attr):
            return AdapterRef(builder, module, attr, False)
    raise AdapterError(unknown_builder(builder))


__all__ = [
    "BUILTIN",
    "DEFAULT_ATTR",
    "PLUGIN_PREFIX",
    "AdapterError",
    "AdapterRef",
    "is_valid",
    "resolve",
    "unknown_builder",
]
