"""Фикстуры: модули с seed(db), заполняющие базу бота до прогона.

Зачем: кейс «пользователь видит свою подписку» не пройдёт на пустой базе — ему
нужна существующая запись. Фикстура создаёт минимальное состояние, при котором
сценарий имеет смысл, и живёт отдельно от кейса: один и тот же набор данных
обслуживает несколько кейсов.

Модули лежат рядом, в каталоге `fixtures/`, и загружаются по имени из YAML-кейса.
Имя проверяется: без проверки `../../` в поле fixture читался бы произвольный
файл и исполнялся как Python.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


def path_for(name: str) -> pathlib.Path:
    """Путь к модулю фикстуры по имени."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"недопустимое имя фикстуры: {name!r}")
    path = _DIR / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"фикстура не найдена: {path}")
    return path


def load(name: str) -> Any:
    """Загрузить модуль фикстуры и достать из него async seed(db)."""
    path = path_for(name)
    spec = importlib.util.spec_from_file_location(f"botkit_farm_fixture_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"фикстура не читается: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.seed


__all__ = ["load", "path_for"]
