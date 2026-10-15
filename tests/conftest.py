"""Общий браузер на сессию тестов.

По одному chromium на каждый скриншот тесты работали нестабильно: восемь
подряд запусков и закрытий, и связь с браузером рвалась. Один браузер на
сессию убирает и источник flake, и лишние секунды.

Плата за это — см. pytest_collection_modifyitems ниже: пока живёт контекст
sync_playwright, в главном потоке крутится его event loop, и с ним не
работает ни asyncio.run(), ни новый loop через run_until_complete().
"""

from __future__ import annotations

import pathlib

import pytest

ARGS = ["--disable-lcd-text", "--force-color-profile=srgb"]


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(args=ARGS)
        yield b
        b.close()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Браузерные тесты — в конец коллекции.

    Сессионный browser держит открытым контекст sync_playwright, а тот,
    пока жив, держит работающий event loop в главном потоке. Любая тестовая
    функция после него, зовущая asyncio.run() (или coroutine через
    run_until_complete), падает с «cannot be called from a running event loop»
    — включая async-тесты под pytest-asyncio. Это не лечится внутри теста:
    второй loop в том же потоке Python запрещает и сам.

    Поэтому порядок задаётся здесь, а не алфавитом: всё, что нуждается в
    asyncio, отыгрывается до первого запуска браузера. Сортировка
    стабильная, внутри групп порядок сборки сохраняется.
    """
    items.sort(key=lambda item: 1 if "browser" in item.fixturenames else 0)


def shoot(browser, html: str, out: pathlib.Path, *, width: int, height: int) -> pathlib.Path:
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    try:
        page.set_content(html, wait_until="load")
        page.screenshot(path=str(out))
    finally:
        page.close()
    return out
