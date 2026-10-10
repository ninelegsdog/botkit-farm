"""Рендер сценария в PNG через HTML и headless Chromium.

Интерфейс рисуется нами, а не снимается с Telegram: Bot API не отдаёт историю
сообщений и не рендерит интерфейс. Поэтому на кадре — наш макет, и он честно
подписан как «демо-интерфейс», а не как «живая работа бота».

Почему Chromium, а не PIL: в тексте реплик и подписях кнопок есть эмодзи
(«📅 Записаться»), а DejaVu Sans их не содержит — в PIL на месте каждого такого
глифа был пустой квадрат. NotoColorEmoji в системе есть, но это CBDT-растр:
Pillow рисует его только при размере 109 px и отказывается при меньшем
(«invalid pixel size»), то есть каждый эмодзи пришлось бы композитить руками
в обход text-layout. Chromium делает шрифтовой фолбэк сам, вёрстку и перенос
строк — CSS. Шрифты: DejaVu Sans для текста, Noto Color Emoji для эмодзи.
"""

from __future__ import annotations

import datetime as dt
import html
import pathlib
import re
from typing import Any

W, H = 1280, 720

# Телефонный режим: портретный экран приложения Telegram (примерно 9:19.5, как
# скриншот телефона), а не широкий макет «для отчёта». Активен только тогда, когда
# вызван с phone=True; по умолчанию стенд рисует прежний 1280×720.
PHONE_W, PHONE_H = 720, 1560

# Реальный фон приложения Telegram, который владелец кладёт в farm/assets/tg-bg.*
# (только паттерн, без текста/пузырей/шапки). Пузыри и текст рисуются поверх.
# Файл вшивается в HTML base64, чтобы кадр был воспроизводим из любой рабочей
# директории; без файла рендер работает как раньше.
# Подложка лежит в assets/ в корне проекта: это пользовательский артефакт
# (снимок окна Telegram), а не часть пакета, и переустанавливать его вместе с
# библиотекой незачем. Поддерживается и вариант рядом с пакетом — чтобы старый
# стенд и новый проект читали один и тот же файл.
_ASSET_CANDIDATES = (
    pathlib.Path(__file__).resolve().parents[2] / "assets",
    pathlib.Path(__file__).resolve().parent / "assets",
)


def _asset_dir() -> pathlib.Path:
    """Каталог подложек. Считается при каждом обращении, а не один раз на
    импорте: подложка — пользовательский файл, и он может появиться после
    первого запуска инструмента.
    """
    for cand in _ASSET_CANDIDATES:
        if cand.exists():
            return cand
    return _ASSET_CANDIDATES[0]


BG_ASSET_DIR = _asset_dir()

# ── Режим «окно»: подложка из настоящего скриншота Telegram Desktop ──────────
# Владелец кладёт в farm/assets/tg-bg.* снимок окна Telegram Desktop, из которого
# руками удалён весь текст, кроме подписи «бот» под заголовком. Кадр 1:1, без
# масштабирования: 1920×1054 — это размер его монитора, и любое вписывание в
# 720×1560 размывало бы и узор, и кириллицу.
WINDOW_W, WINDOW_H = 1920, 1054

# Границы измерены по самому файлу, а не взяты «на глаз» (farm/assets/tg-bg.png):
# шапка окна y0..24 (#242f3d), шапка чата y24..96 (#17212b), зона сообщений
# y96..868, поле ввода y868..1054. Пузыри рисуются строго между 96 и 868, иначе
# они наезжают на поле ввода — а это уже не похоже на живой чат.
CHAT_TOP, CHAT_BOTTOM = 96, 868
# Имя бота — там, где заголовок: x=112 (после аватара-квадрата), по центру строки
# шапки y≈48. Подпись «бот» в файле уже нарисована, её не трогаем.
TITLE_X, TITLE_Y = 112, 44
TITLE_FONT = 19


# Цвета Telegram (тёмная тема), а не выдуманные.
BG = "#17212b"
HEADER = "#202b36"
IN_BUBBLE = "#182533"
OUT_BUBBLE = "#2b5278"
IN_TEXT = "#ffffff"
OUT_TEXT = "#ffffff"
BTN_BG = "#202b36"
BTN_BORDER = "#3d8fe4"
ACCENT = "#6ab3f3"

# Единственный источник истины для шрифтов: и кадр, и проверка в tests берут
# этот список. Если убрать Noto Color Emoji отсюда, тест обязан упасть —
# иначе «эмодзи нарисованы цветом» останется непроверяемым утверждением.
FONT_STACK = '"DejaVu Sans","Noto Color Emoji",sans-serif'
MUTED = "#7f91a4"
DIVIDER = "#0e1621"

_CSS = f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{W}px; background:{BG}; }}
body {{
  font-family:{FONT_STACK};
  color:{IN_TEXT};
  display:flex; flex-direction:column;
  -webkit-font-smoothing:antialiased;
}}
header {{
  display:flex; align-items:center; gap:14px;
  padding:14px 24px; background:{HEADER}; border-bottom:1px solid {DIVIDER};
}}
.avatar {{
  width:40px; height:40px; border-radius:50%; background:{ACCENT};
  display:flex; align-items:center; justify-content:center;
  font-size:20px; color:#0e1621; font-weight:bold;
}}
.title {{ font-size:19px; line-height:22px; }}
.handle {{ font-size:14px; color:{MUTED}; line-height:17px; }}
main {{ flex:1; padding:20px 24px 12px; display:flex; flex-direction:column; gap:10px; }}
.row {{ display:flex; }}
.row.out {{ justify-content:flex-end; }}
.bubble {{
  max-width:620px; padding:9px 14px; border-radius:14px;
  font-size:18px; line-height:24px; white-space:pre-wrap; word-break:break-word;
  background:{IN_BUBBLE}; color:{IN_TEXT};
}}
.row.out .bubble {{ background:{OUT_BUBBLE}; }}
.row.out {{ flex-direction:column; align-items:flex-end; }}
.row.out .bubble {{ border-bottom-right-radius:4px; }}
.row:not(.out) .bubble {{ border-bottom-left-radius:4px; }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 2px; }}
.chip {{
  padding:7px 13px; border-radius:11px; background:{BTN_BG};
  border:1px solid {BTN_BORDER}; color:{ACCENT}; font-size:16px;
}}
footer {{
  padding:9px 24px 11px; border-top:1px solid {DIVIDER};
  display:flex; justify-content:space-between; align-items:baseline; gap:16px;
}}
.stamp {{ font-size:13px; color:{MUTED}; }}
.kind {{
  font-size:13px; color:{MUTED}; border:1px solid {DIVIDER};
  padding:2px 9px; border-radius:8px;
}}
"""


_CSS_PHONE = f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{PHONE_W}px; background:#0b141a; }}
body.phone {{
  font-family:{FONT_STACK}; color:{IN_TEXT};
  -webkit-font-smoothing:antialiased;
}}
.screen {{
  width:{PHONE_W}px; min-height:{PHONE_H}px;
  background:{BG}; position:relative;
  display:flex; flex-direction:column;
}}
.status {{
  display:flex; justify-content:space-between;
  padding:12px 20px 4px; font-size:13px; color:{MUTED};
}}
header {{
  display:flex; align-items:center; gap:12px;
  padding:8px 16px; background:{HEADER}; border-bottom:1px solid {DIVIDER};
}}
.avatar {{
  width:42px; height:42px; border-radius:50%; background:{ACCENT};
  display:flex; align-items:center; justify-content:center;
  font-size:20px; color:#0e1621; font-weight:bold;
}}
.title {{ font-size:18px; line-height:21px; }}
.handle {{ font-size:14px; color:{MUTED}; line-height:17px; }}
main {{ flex:1; padding:14px 14px 10px; display:flex; flex-direction:column; gap:8px; }}
.row {{ display:flex; }}
.row.out {{ justify-content:flex-end; }}
.bubble {{
  max-width:78%; padding:8px 12px; border-radius:16px;
  font-size:17px; line-height:23px; white-space:pre-wrap; word-break:break-word;
  background:{IN_BUBBLE}; color:{IN_TEXT};
}}
.row.out .bubble {{ background:{OUT_BUBBLE}; }}
.row.out {{ flex-direction:column; align-items:flex-end; }}
.row.out .bubble {{ border-bottom-right-radius:4px; }}
.row:not(.out) .bubble {{ border-bottom-left-radius:4px; }}
.time {{ font-size:11px; color:{MUTED}; margin-top:3px; }}
.row.out .time {{ align-self:flex-end; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 2px; }}
.chip {{
  flex:1; text-align:center; min-width:96px;
  padding:8px 10px; border-radius:14px; background:{BTN_BG};
  border:1px solid {BTN_BORDER}; color:{ACCENT}; font-size:15px;
}}

/* Всплывающее уведомление Telegram: тёмная плашка с закруглёнными краями,
   появляется поверх чата и не занимает места в ленте сообщений. */
.toast {{
  align-self:center; margin:2px auto;
  max-width:60%; padding:7px 14px; border-radius:11px;
  background:#202b36; color:{IN_TEXT}; font-size:0.92em;
  box-shadow:0 2px 8px rgba(0,0,0,0.35); text-align:center;
}}
.wm {{
  position:absolute; left:14px; bottom:10px;
  font-size:12px; color:#566372; background:rgba(11,20,26,0.62);
  padding:3px 9px; border-radius:9px;
}}
"""


_CSS_WINDOW = f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{WINDOW_W}px; height:{WINDOW_H}px; background:#0e1621; }}
body.win {{ font-family:{FONT_STACK}; -webkit-font-smoothing:antialiased; }}
/* Подложка — снимок окна, который сделал владелец. Ни шапки, ни поля ввода, ни
   статуса мы не рисуем: они уже в файле, и вторая шапка поверх первой выдала бы
   макет вместо живого окна. */
.screen {{
  position:relative; width:{WINDOW_W}px; height:{WINDOW_H}px;
  overflow:hidden;
}}
.chat {{
  position:absolute; left:104px; right:24px;
  top:{CHAT_TOP}px; height:{CHAT_BOTTOM - CHAT_TOP}px;
  display:flex; flex-direction:column; gap:5px;
  font-size:var(--fs,15px); line-height:1.36;
}}
.row {{ display:flex; }}
.row.out {{ justify-content:flex-end; }}
.bubble {{
  position:relative; max-width:74%; padding:5px 10px 5px 11px;
  border-radius:11px; background:{IN_BUBBLE}; color:{IN_TEXT};
  white-space:pre-wrap; word-break:break-word;
}}
.row.out .bubble {{ background:{OUT_BUBBLE}; }}
.row:not(.out) .bubble {{ border-bottom-left-radius:4px; }}
.row.out .bubble {{ border-bottom-right-radius:4px; }}
/* Время в Telegram Desktop стоит справа в последней строке пузыря, а не под ним,
   как на телефоне. Сдвиг вниз на полстроки убирает провал базовой линии. */
.t {{ float:right; font-size:0.72em; color:{MUTED}; margin:4px 0 0 10px; }}
.row.out .t {{ color:#b9d4f0; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin:5px 0 1px; }}
.chip {{
  padding:4px 11px; border-radius:9px; background:{BTN_BG};
  border:1px solid {BTN_BORDER}; color:{ACCENT}; font-size:0.9em;
}}
.title {{
  position:absolute; left:{TITLE_X}px; top:{TITLE_Y}px;
  font-size:{TITLE_FONT}px; line-height:{TITLE_FONT + 4}px;
  color:{IN_TEXT}; font-weight:600;
}}

/* Всплывающее уведомление Telegram: тёмная плашка с закруглёнными краями,
   появляется поверх чата и не занимает места в ленте сообщений. */
.toast {{
  align-self:center; margin:2px auto;
  max-width:60%; padding:7px 14px; border-radius:11px;
  background:#202b36; color:{IN_TEXT}; font-size:0.92em;
  box-shadow:0 2px 8px rgba(0,0,0,0.35); text-align:center;
}}
.wm {{
  position:absolute; left:104px; bottom:{WINDOW_H - CHAT_BOTTOM + 12}px;
  font-size:12px; color:#63737f;
}}
"""


def _esc(text: str) -> str:
    """Текст бота — это данные, а не разметка.

    Сценарий кормит боту текст пользователя, а бот может вернуть его в ответе;
    без экранирования строка вида `<b>цена</b>` или сломанный `<` порвала бы
    вёрстку всего кадра, а не своей пузырьковой части.
    """
    return html.escape(text, quote=True)


# Боты создаются с parse_mode=HTML (bot_factory.create_bot), поэтому Telegram
# показывает их разметку, а не теги. Чтобы кадр совпадал с тем, что увидит
# клиент, разрешён ровно тот же минимальный набор тегов, что понимает Telegram,
# а всё прочее остаётся текстом.
_TAG = re.compile(r"&lt;(/?)(b|i|u|s|code|pre)&gt;")
_LINK = re.compile(r"&lt;a href=&quot;(https?://[^&]+)&quot;&gt;")


def _tg_html(text: str) -> str:
    out = _TAG.sub(lambda m: ("</" if m.group(1) else "<") + m.group(2) + ">", _esc(text))
    return _LINK.sub(lambda m: f'<a href="{m.group(1)}">', out)


def _clock(idx: int) -> str:
    """Время под сообщением в телефонном кадре: детерминированное, шаг 3 минуты."""
    m = 600 + idx * 3
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def _bg_assets() -> list[pathlib.Path]:
    d = _asset_dir()
    if not d.exists():
        return []
    return sorted(d.glob("tg-bg.*"))


def _bg_style(*, size: str = "cover") -> str:
    """Подложка из farm/assets/tg-bg.*, вшитая в HTML как base64: кадр должен
    воспроизводиться из любой рабочей директории, а не только из корня стенда.

    `size` — в режиме окна это `1920px 1054px` (1:1, без искажения пропорций),
    в телефонном прежний cover.
    """
    import base64
    import mimetypes

    assets = _bg_assets()
    if not assets:
        return ""
    img = assets[0]
    mime = mimetypes.guess_type(img.name)[0] or "image/png"
    b64 = base64.b64encode(img.read_bytes()).decode("utf-8")
    return (
        f"background-image:url(data:{mime};base64,{b64});"
        f"background-size:{size};background-position:top left;"
        "background-repeat:no-repeat;"
    )


def _bubble_row(
    turn: dict[str, Any], idx: int = 0, *, phone: bool = False, window: bool = False
) -> str:
    cls = "row out" if turn["out"] else "row"
    if turn.get("is_toast"):
        # Всплывающее уведомление в Telegram — отдельный элемент поверх чата:
        # без собственного оформления кадр врал бы о том, что клиент видит.
        return f'<div class="toast">{_tg_html(turn["text"])}</div>'
    body = _tg_html(turn["text"])
    if window:
        # В окне Telegram время — часть последней строки пузыря, поэтому оно
        # внутри, а не отдельной строкой под ним, как на телефоне.
        body += f'<span class="t">{_clock(idx)}</span>'
    parts = [f'<div class="{cls}"><div class="bubble">{body}</div>']
    if phone:
        parts.append(f'<span class="time">{_clock(idx)}</span>')
    labels = turn.get("buttons") or []
    if labels:
        chips = "".join(f'<span class="chip">{_esc(x)}</span>' for x in labels)
        parts.append(f'<div class="chips">{chips}</div>')
    parts.append("</div>")
    return "".join(parts)


def page_html(
    turns: list[dict[str, Any]],
    *,
    title: str,
    handle: str,
    stamp: str,
    kind: str = "демо-интерфейс",
    phone: bool = False,
    window: bool = False,
    fs: int | None = None,
) -> str:
    initial = html.escape(title[0].upper()) if title else "?"
    rows = "".join(_bubble_row(t, i, phone=phone, window=window) for i, t in enumerate(turns))
    if window:
        if not _bg_assets():
            raise RuntimeError(
                "нет подложки: положите снимок окна Telegram в farm/assets/tg-bg.png. "
                "Кадр без него получился бы нашим макетом, а это ровно то, что "
                "владелец уже отверг."
            )
        bg = _bg_style(size=f"{WINDOW_W}px {WINDOW_H}px")
        size = f' style="--fs:{fs}px"' if fs else ""
        return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><style>{_CSS_WINDOW}</style></head>
<body class="win">
<div class="screen" style="{bg}">
  <div class="title">{_esc(title)}</div>
  <div class="chat"{size}>{rows}</div>
  <span class="wm">Демо-интерфейс · локальный стенд · {_esc(stamp)}</span>
</div>
</body></html>"""
    if phone:
        return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><style>{_CSS_PHONE}</style></head>
<body class="phone">
<div class="screen">
  <div class="status"><span>10:00</span><span>100%</span></div>
<header>
  <div class="avatar">{initial}</div>
  <div><div class="title">{_esc(title)}</div><div class="handle">{_esc(handle)}</div></div>
</header>
<main style="{_bg_style()}">{rows}</main>
<span class="wm">Демо-интерфейс · локальный стенд · {_esc(stamp)}</span>
</div>
</body></html>"""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
<header>
  <div class="avatar">{initial}</div>
  <div><div class="title">{_esc(title)}</div><div class="handle">{_esc(handle)}</div></div>
</header>
<main>{rows}</main>
<footer>
  <span class="stamp">Демо-интерфейс · сценарий воспроизведён на локальном стенде · {_esc(stamp)}</span>
  <span class="kind">{_esc(kind)}</span>
</footer>
</body></html>"""


LAUNCH_ARGS = ["--disable-lcd-text", "--force-color-profile=srgb"]

# Кегль в режиме окна подбирается под высоту зоны сообщений: сценарий длиннее
# обычного не должен молча наезжать на поле ввода. Ниже 12 px текст нечитаем, и
# тогда лучше не кадр, а отказ.
FS_MIN = 12
FS_MAX = 15


def _fit_font(page: Any, avail: int) -> int:
    """Подобрать кегль так, чтобы сообщения влезли в зону чата.

    Молчаливый обрез хвоста сценария — это кадр, который выглядит правдоподобно,
    но показывает меньше, чем бот на самом деле отвечает. Поэтому не влезает —
    падаем.
    """
    for fs in range(FS_MAX, FS_MIN - 1, -1):
        page.evaluate(f"document.querySelector('.chat').style.fontSize='{fs}px'")
        used = page.evaluate("document.querySelector('.chat').scrollHeight")
        if used <= avail:
            return fs
    raise RuntimeError(
        f"сценарий не влезает в зону чата даже кеглем {FS_MIN}px "
        f"({used}px > {avail}px) — кадр не создаю"
    )


def shot(
    browser: Any,
    turns: list[dict[str, Any]],
    out_png: pathlib.Path,
    *,
    title: str,
    handle: str,
    stamp: str | None = None,
    long: bool = False,
    phone: bool = False,
    window: bool = False,
) -> pathlib.Path:
    """Отрисовать кадр в уже открытом браузере.

    `long` — во всю высоту содержимого, для длинных сценариев в канал; иначе
    строго 1280x720, как в портфолио и на Kwork.
    `phone` — портретный экран приложения Telegram (720×1560).
    `window` — поверх снимка настоящего окна Telegram Desktop 1920×1054.
    """
    stamp = stamp or dt.datetime.now().strftime("%d.%m.%Y")
    kind = "демо-интерфейс · 2 экрана" if long else "демо-интерфейс"
    doc = page_html(
        turns, title=title, handle=handle, stamp=stamp, kind=kind, phone=phone, window=window
    )
    if window:
        vw, vh = WINDOW_W, WINDOW_H
    elif phone:
        vw, vh = PHONE_W, PHONE_H
    else:
        vw, vh = W, H
    out_png.parent.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(
        viewport={"width": vw, "height": vh}, device_scale_factor=1, color_scheme="dark"
    )
    try:
        page.set_content(doc, wait_until="load")
        # Шрифты локальные и системные, web-шрифтов нет, поэтому ждать нечего:
        # небольшая пауза только на то, чтобы layout успел устояться.
        page.wait_for_timeout(120)
        if window:
            _fit_font(page, CHAT_BOTTOM - CHAT_TOP)
        page.screenshot(path=str(out_png), full_page=bool(long))
    finally:
        page.close()
    return out_png


def render(
    turns: list[dict[str, Any]],
    out_png: pathlib.Path,
    *,
    title: str,
    handle: str,
    stamp: str | None = None,
    long: bool = False,
    phone: bool = False,
    window: bool = False,
) -> pathlib.Path:
    """Отрисовать кадр, подняв браузер под задачу."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # --disable-lcd-text: без него Chromium сглаживает текст субпиксельно и
        # оставляет цветные каёмки на белых буквах — в кадре они выглядят как
        # грязь, а в тестах маскируют настоящие цвета эмодзи.
        browser = p.chromium.launch(args=LAUNCH_ARGS)
        try:
            return shot(
                browser,
                turns,
                out_png,
                title=title,
                handle=handle,
                stamp=stamp,
                long=long,
                phone=phone,
                window=window,
            )
        finally:
            browser.close()
