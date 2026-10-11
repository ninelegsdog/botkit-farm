"""Тесты точки расширения адаптеров: разбор ссылки и контракт `build()`.

Сторонний адаптер — единственный код, который к ферме добавляет не автор
фермы. Ошибка в ссылке не должна выглядеть сбоем бота, а ошибка в возврате
`build()` — падать позже и непонятно. Оба края закрыты здесь.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from botkit_farm.adapters import contract, registry

# ── разбор ссылки ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(registry.BUILTIN))
def test_встроенные_поколения_разбираются(name: str) -> None:
    ref = registry.resolve(name)

    assert ref.spec == name
    assert ref.module == registry.BUILTIN[name]
    assert ref.attr == registry.DEFAULT_ATTR
    assert ref.builtin is True
    assert ref.target == f"{registry.BUILTIN[name]}:build"


def test_плагин_без_фабрики_берёт_умолчание() -> None:
    ref = registry.resolve("botkit-farm.adapter:mypkg.adapters.mine")

    assert ref.module == "mypkg.adapters.mine"
    assert ref.attr == "build"
    assert ref.builtin is False
    assert ref.target == "mypkg.adapters.mine:build"


def test_плагин_с_фабрикой_берёт_её() -> None:
    ref = registry.resolve("botkit-farm.adapter:mypkg.mine:make")

    assert ref.module == "mypkg.mine"
    assert ref.attr == "make"


def test_префикс_без_модуля_отвергается() -> None:
    with pytest.raises(registry.AdapterError, match="неизвестный builder"):
        registry.resolve("botkit-farm.adapter:")


@pytest.mark.parametrize(
    "builder",
    [
        "чепуха",
        "newgen ",  # пробел недопустим (stripping делает модель, не resolve)
        "botkit-farm.adapter:mypkg..mine",
        "botkit-farm.adapter:1mypkg",
        "botkit-farm.adapter:mypkg.mine:1make",
        "botkit-farm.adapter:mypkg/mine",
        "botkit-farm.adapter:mypkg mine",
    ],
)
def test_недопустимая_ссылка_отвергается(builder: str) -> None:
    with pytest.raises(registry.AdapterError):
        registry.resolve(builder)


def test_форма_ссылки_и_разбор_согласованы() -> None:
    """То, что признано валидным на загрузке кейса, обязано разбираться.

    `is_valid` и `resolve` дублируют логику формы; расхождение дало бы кейс,
    который загрузился, но упал в прогоне.
    """
    for builder in [
        *sorted(registry.BUILTIN),
        "botkit-farm.adapter:mypkg.mine",
        "botkit-farm.adapter:mypkg.mine:make",
        "botkit-farm.adapter:mypkg:",
    ]:
        assert registry.is_valid(builder)
        registry.resolve(builder)


def test_невалидная_ссылка_и_не_валидна_и_не_разбирается() -> None:
    for builder in ["чепуха", "botkit-farm.adapter:", "botkit-farm.adapter:a/b"]:
        assert not registry.is_valid(builder)
        with pytest.raises(registry.AdapterError):
            registry.resolve(builder)


# ── контракт результата ─────────────────────────────────────────────────────


def _диспетчер() -> SimpleNamespace:
    return SimpleNamespace(feed_update=lambda *a, **k: None)


def _бот() -> SimpleNamespace:
    return SimpleNamespace(session=SimpleNamespace(close=lambda: None))


def test_правильный_результат_принимается() -> None:
    dp, bot, db = _диспетчер(), _бот(), object()

    built = contract.check((dp, bot, db))

    assert built.dispatcher is dp
    assert built.bot is bot
    assert built.db is db


def test_база_необязательна() -> None:
    built = contract.check((_диспетчер(), _бот(), None))

    assert built.db is None


def test_не_кортеж_отвергается_с_именем_типа() -> None:
    with pytest.raises(contract.AdapterContractError, match="int"):
        contract.check(42)


def test_кортеж_не_из_трёх_отвергается() -> None:
    with pytest.raises(contract.AdapterContractError, match="кортеж"):
        contract.check((_диспетчер(), _бот()))


def test_none_вместо_диспетчера_отвергается() -> None:
    with pytest.raises(contract.AdapterContractError, match="dispatcher"):
        contract.check((None, _бот(), None))


def test_объект_без_feed_update_отвергается() -> None:
    with pytest.raises(contract.AdapterContractError, match="feed_update"):
        contract.check((object(), _бот(), None))


def test_none_вместо_бота_отвергается() -> None:
    with pytest.raises(contract.AdapterContractError, match="bot"):
        contract.check((_диспетчер(), None, None))


def test_бот_без_session_отвергается() -> None:
    with pytest.raises(contract.AdapterContractError, match="session"):
        contract.check((_диспетчер(), object(), None))


# ── импорт фабрики в окружении бота ─────────────────────────────────────────


def test_фабрика_импортируется_из_реального_модуля() -> None:
    build = contract.load_builder("botkit_farm.adapters.newgen", "build")

    assert callable(build)


def test_отсутствующий_модуль_даёт_понятную_ошибку() -> None:
    with pytest.raises(contract.AdapterContractError, match="не найден"):
        contract.load_builder("нет.такого.модуляzzz", "build")


def test_отсутствующая_фабрика_отвергается() -> None:
    with pytest.raises(contract.AdapterContractError, match="фабрики"):
        contract.load_builder("botkit_farm.adapters.newgen", "нет_такой")


def test_не_вызываемая_фабрика_отвергается() -> None:
    """Модуль может содержать одноимённое имя, но не функцию."""
    with pytest.raises(contract.AdapterContractError, match="фабрики"):
        contract.load_builder("botkit_farm.adapters", "BUILTIN")
