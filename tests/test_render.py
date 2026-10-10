"""Проверки рендера кадра.

Кадр я прочитать не могу — значит проверка обязана быть пиксельной и обязана
уметь падать.

Про эмодзи. Первая версия проверки считала «тёплые» пиксели по всему кадру и
 сравнивала с контрольным кадром без эмодзи. Контроль оказался дырявым (эмодзи
вырезались только из текста, но не из подписей кнопок), а сама метрика ловила
цветные каёмки субпиксельного сглаживания. Поэтому проверка перенесена на
отдельную пробу: один эмодзи крупным планом на белом фоне плюс такой же пробный
кадр без эмодзи. Метрика та же, контроль — честный.
"""

from __future__ import annotations

import colorsys

# пакет ставится editable, sys.path не нужен
from PIL import Image, ImageStat

import conftest
from botkit_farm import render

TURNS = [
    {"text": "/start", "out": True},
    {
        "text": "Запись\nВыберите действие:",
        "out": False,
        "buttons": ["📅 Записаться", "🗓 Мои записи"],
    },
    {"text": "📅 Записаться", "out": True},
    {"text": "Выберите услугу:", "out": False, "buttons": ["Стрижка и укладка — 60 мин, 2200₽"]},
    {"text": "✅ Запись #12 подтверждена!", "out": False, "buttons": ["💳 Внести депозит"]},
]

# Длинный набор: содержимое одного экрана не занимает и половины, а проверка
# «длинный кадр длиннее экрана» на коротком материале всегда врала бы.
LONG_TURNS = TURNS + [
    {"text": f"Сообщение номер {i}: проверяем, что кадр растёт, а не обрезается", "out": i % 2 == 0}
    for i in range(8)
]


def _shot(browser, turns, tmp_path, **kw) -> Image.Image:
    """Кадр ровно тем же кодом, что и в бою: render.shot, а не тестовый код."""
    png = render.shot(
        browser,
        turns,
        tmp_path / "f.png",
        title="BookingBot · запись",
        handle="@SaasBookingKitBot",
        stamp="03.10.2026",
        **kw,
    )
    return Image.open(png).convert("RGB")


def _warm(img: Image.Image) -> int:
    """Насыщенные пиксели не-синего тона: у эмодзи они есть, у палитры Telegram нет."""
    n = 0
    for r, g, b in img.convert("RGB").getdata():
        h, _l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s > 0.35 and not 180 <= h * 360 <= 250:
            n += 1
    return n


def _probe(browser, text: str, tmp_path, name: str) -> Image.Image:
    """Крупный план одного куска текста на белом фоне."""
    # Тот же FONT_STACK, что и у кадра: если из него убрать Noto Color Emoji,
    # проверка обязана упасть, а не продолжать мерить мусор.
    doc = (
        '<!doctype html><meta charset="utf-8">'
        f'<body style="margin:0;background:#fff;font-family:{render.FONT_STACK};'
        'font-size:64px">'
        f'<div style="padding:12px">{text}</div>'
    )
    out = conftest.shoot(browser, doc, tmp_path / name, width=700, height=120)
    return Image.open(out).convert("RGB")


def test_short_frame_is_exactly_1280x720(browser, tmp_path):
    assert _shot(browser, TURNS, tmp_path).size == (1280, 720)


def test_long_frame_is_taller_than_one_screen(browser, tmp_path):
    img = _shot(browser, LONG_TURNS, tmp_path, long=True)
    assert img.size[0] == 1280
    assert img.size[1] > 720


def test_frame_is_not_blank(browser, tmp_path):
    img = _shot(browser, TURNS, tmp_path)
    stat = ImageStat.Stat(img)
    assert max(stat.stddev) > 10, "кадр почти одноцветный — вероятно, ничего не нарисовано"
    assert len(img.getcolors(maxcolors=100_000) or []) > 30


def test_color_emoji_font_is_really_used(browser, tmp_path):
    """Chromium обязан подставлять Noto Color Emoji, а не рисовать пустые квадраты.

    Контроль: та же проба без эмодзи. Если бы DejaVu умел их (или кайма от
    сглаживания попала в счёт), контроль бы не обнулился — и тест упал бы.
    """
    with_emoji = _warm(_probe(browser, "📅 🗓 💳 ✅", tmp_path, "a.png"))
    without = _warm(_probe(browser, "X X X X", tmp_path, "b.png"))
    assert with_emoji > 300, f"эмодзи не отрисованы цветом: {with_emoji}"
    assert without < 50, f"метрика ловит не только эмодзи: {without} в контроле"


def test_frame_differs_when_emoji_removed(browser, tmp_path):
    """Эмодзи занимают место: кадр без них обязан отличаться по пикселям."""
    plain = [
        {
            **t,
            "text": "".join(c for c in t["text"] if ord(c) < 0x2190),
            "buttons": ["".join(c for c in b if ord(c) < 0x2190) for b in (t.get("buttons") or [])],
        }
        for t in TURNS
    ]
    a = _shot(browser, TURNS, tmp_path / "a")
    b = _shot(browser, plain, tmp_path / "b")
    pa_all = list(a.convert("RGB").getdata())  # noqa: DeprecationWarning
    pb_all = list(b.convert("RGB").getdata())  # noqa: DeprecationWarning
    diff = sum(
        1
        for pa, pb in zip(pa_all, pb_all, strict=True)
        if max(abs(x - y) for x, y in zip(pa, pb, strict=True)) > 30
    )
    assert diff > 5_000, f"кадры почти не отличаются: {diff} пикселей"


def test_footer_states_demo_interface():
    doc = render.page_html(TURNS, title="T", handle="@h", stamp="03.10.2026")
    assert "Демо-интерфейс" in doc
    assert "живая работа" not in doc.lower()


def test_text_is_escaped_but_telegram_tags_survive():
    hostile = "<script>alert(1)</script>"
    doc = render.page_html([{"text": hostile, "out": False}], title="T", handle="@h", stamp="x")
    assert "<script>" not in doc
    assert "&lt;script&gt;" in doc

    marked = render._tg_html("цена <b>2200₽</b> и <i>скидка</i>")
    assert "<b>2200₽</b>" in marked
    assert "<i>скидка</i>" in marked

    forbidden = render._tg_html("<img src=x onerror=alert(1)>")
    assert "<img" not in forbidden


def test_button_labels_come_from_the_bot_not_from_us():
    """Кнопки рисуются текстом, который прислал бот: подменять его нельзя."""
    turns = [{"text": "Выберите", "out": False, "buttons": ["Стрижка — 60 мин, 2200₽"]}]
    doc = render.page_html(turns, title="T", handle="@h", stamp="x")
    assert "Стрижка — 60 мин, 2200₽" in doc
    assert doc.count('class="chip"') == 1


# ── Режим «окно»: кадр поверх снимка настоящего окна Telegram Desktop ──────────


def test_window_frame_is_the_monitor_1_to_1(browser, tmp_path):
    """Кадр в окне не масштабируется: 1920×1054 это размер монитора, любое
    вписывание в чужое соотношение размыло бы и узор, и кириллицу."""
    img = _shot(browser, TURNS, tmp_path, window=True)
    assert img.size == (render.WINDOW_W, render.WINDOW_H)


def test_window_uses_the_owner_screenshot_as_backdrop(browser, tmp_path):
    """Подложка обязана быть его файлом, а не нашим макетом: в HTML вшит base64
    именно farm/assets/tg-bg.png."""
    doc = render.page_html(TURNS, title="BookingBot", handle="@h", stamp="x", window=True)
    import base64

    raw = base64.b64encode(render._bg_assets()[0].read_bytes()).decode("utf-8")
    assert raw in doc
    assert "Демо-интерфейс" in doc, "подпись «демо» обязана остаться и на окне"


def test_window_refuses_to_draw_without_backdrop(monkeypatch):
    """Нет файла — нет кадра. Молчаливый откат на макет означал бы ровно то,
    что владелец уже отверг, и тихо выдал бы негодный кадр."""
    monkeypatch.setattr(render, "_bg_assets", lambda: [])
    try:
        render.page_html(TURNS, title="T", handle="@h", stamp="x", window=True)
    except RuntimeError as exc:
        assert "tg-bg" in str(exc)
    else:
        raise AssertionError("кадр нарисован без подложки — это не то, что обещано")


def test_window_bubbles_stay_above_the_input_bar(browser, tmp_path):
    """Нижняя граница пузырей — над полем ввода (y=868), иначе кадр выдаёт себя
    макетом: настоящий чат не пишет на панели ввода."""
    img = _shot(browser, TURNS, tmp_path, window=True)
    px = img.load()
    bar = render.CHAT_BOTTOM + 4

    # Внутри зоны чата у нижней кромки должен быть хоть какой-то текст/пузырь,
    # а ниже — только подложка (однотонная полоса поля ввода).
    def _is_bar(y: int) -> bool:
        return all(abs(px[x, y][0] - px[x, y][1]) < 12 for x in range(200, 1700, 50))

    assert _is_bar(bar), "под полем ввода что-то нарисовано — пузыри налезли на панель"


def test_window_title_sits_above_the_kept_bot_label(browser, tmp_path):
    """Имя бота должно занять строку заголовка, а не затереть подпись «бот»,
    которую владелец оставил в файле."""
    doc = render.page_html(TURNS, title="BookingBot · запись", handle="@h", stamp="x", window=True)
    assert f"left:{render.TITLE_X}px; top:{render.TITLE_Y}px" in doc
    assert "BookingBot · запись" in doc
