"""Общий браузер на сессию тестов.

По одному chromium на каждый скриншот тесты работали нестабильно: восемь
подряд запусков и закрытий, и связь с браузером рвалась. Один браузер на
сессию убирает и источник flake, и лишние секунды.
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


def shoot(browser, html: str, out: pathlib.Path, *, width: int, height: int) -> pathlib.Path:
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    try:
        page.set_content(html, wait_until="load")
        page.screenshot(path=str(out))
    finally:
        page.close()
    return out
