"""Ферма тест-кейсов для Telegram-ботов.

Публичные имена пака (`Manifest`, `Pack`, `load_pack` и т. д.) отдаются лениво.
Это не микрооптимизация: `import botkit_farm` выполняется и внутри процесса бота,
а окружение бота содержит только зависимости самого бота — не фермы. Ранний
`from botkit_farm.pack import ...` тянул `yaml`, которого в этом окружении нет, и
любой запуск падал на импорте, не дойдя до кода бота. Ленивый реэкспорт оставляет
публичный доступ к именам пака, не навязывая их импорт каждому, кто коснулся
пакета.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — только для средств проверки типов
    from botkit_farm.pack import Manifest, Pack, load_pack, resolve_pack_path, scaffold_pack

__all__ = [
    "Manifest",
    "Pack",
    "load_pack",
    "resolve_pack_path",
    "scaffold_pack",
]


def __getattr__(name: str) -> Any:
    """Отдать имя из `botkit_farm.pack` по первому обращению."""
    if name in __all__:
        from botkit_farm import pack

        return getattr(pack, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
