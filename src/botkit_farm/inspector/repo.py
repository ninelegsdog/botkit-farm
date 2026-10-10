"""Инспектор репозитория бота: что вообще умеет бот.

Смысл модуля — убрать догадки при написании кейса. Кейс пишется по коду
репозитория, и почти все опечатки в кейсах происходят из одного: автор придумал
команду или подпись кнопки, которой у бота нет. Этот модуль отвечает на вопросы
«какие команды есть», «какие тексты кнопок», «какие callback_data», «какого
поколения сборка» — из исходников, а не из памяти.

Разбор лёгкий и статический (ast, без импорта чужого кода): импортировать
чужой репозиторий, чтобы «посмотреть список команд», — это выполнение
недоверенного кода на боевой машине. Здесь такого нет: файл только читается.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

# Строки, которые почти наверняка означают callback_data. Их слишком много,
# чтобы держать полный список, но они узнаются по форме.
_CB_HINTS = ("cb_", "act_", "nav_", "menu_", "back", "next", "cancel", "confirm")


@dataclass
class Button:
    """Кнопка, найденная в коде: подпись и (если есть) её callback_data."""

    label: str
    callback: str | None = None
    source: str = ""  # "file.py:123" — чтобы кейс ссылался на источник


@dataclass
class Command:
    """Обработчик команды: сама команда и её описание (если есть)."""

    command: str
    doc: str | None = None
    source: str = ""


@dataclass
class TextReply:
    """Текст, который бот отправляет клиенту (send/editMessage)."""

    text: str
    source: str = ""


@dataclass
class Report:
    """Итог разбора одного репозитория."""

    root: pathlib.Path
    commands: list[Command] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    texts: list[TextReply] = field(default_factory=list)
    builder: str | None = None
    builder_reason: str = ""
    handlers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    files_scanned: int = 0

    def command_names(self) -> list[str]:
        """Список имён команд — то, что агент подставляет в поле send кейса."""
        return [c.command for c in self.commands]


# ── определение поколения сборки ────────────────────────────────────────────


def detect_builder(root: pathlib.Path) -> tuple[str | None, str]:
    """Определить поколение сборки по структуре репозитория.

    Три поколения отличаются не стилем, а точкой входа: у старых Kit v2
    composition root в корне bot.py и build_dispatcher, у новых — src/app.py и
    create_app, у reminder — модульный синглтон состояния. Ошибка здесь стоит
    дорого: адаптер не найдёт свои символы и упадёт с ImportError, который
    выглядит как баг бота, хотя это несовпадение адаптера.
    """
    app_py = root / "src" / "app.py"
    factory = root / "src" / "core" / "bot_factory.py"

    if app_py.exists():
        try:
            text = app_py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "def register_routers" in text or "register_routers" in text:
            return "newgen", "src/app.py: есть register_routers(state)"
        if "create_app" in text:
            return "newgen", "src/app.py: есть create_app()"

    if factory.exists():
        try:
            text = factory.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "def build_dispatcher" in text:
            return "legacy", "src/core/bot_factory.py: есть build_dispatcher(...)"
        if "def create_app" in text:
            return "newgen", "src/core/bot_factory.py: есть create_app()"
            # reminder отдельно: у него модульный синглтон состояния,
            # и newgen/legacy адаптеры на нём падают.

    # reminder: модуль состояния верхнего уровня без класса App
    for cand in root.glob("src/*.py"):
        if cand.name in {"app.py", "main.py", "__init__.py"}:
            continue
        text = cand.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*state\s*=\s*", text, re.M) and "register_routers" not in text:
            return "reminder", f"{cand.name}: модульный синглтон состояния"
    return None, "не опознано: укажите builder в кейсе вручную"


# ── разбор команд и кнопок ──────────────────────────────────────────────────

_CMD_DECOR = re.compile(r"@(\w+)\.command\(")


def _iter_py(root: pathlib.Path) -> list[pathlib.Path]:
    """Все .py репозитория, кроме мусорных каталогов.

    Фильтр по каталогам, а не по имени файла: заглянуть в .venv клонированного
    бота — значит разобрать чужую библиотеку и утонуть в её обработчиках.
    """
    skip = {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        "data",
        "migrations",
    }
    out: list[pathlib.Path] = []
    for p in root.rglob("*.py"):
        if any(part in skip for part in p.parts):
            continue
        out.append(p)
    return out


def _handler_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "command":
            return node.name
    return None


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _find_buttons(tree: ast.AST, source: str) -> list[Button]:
    """Кнопки из InlineKeyboardMarkup: подпись рядом с callback_data.

    Только literal: если подпись собрана переменной, точный текст кнопки из кода
    не извлекается — и это нормально, автор кейса увидит, что кнопка найдена, но
    подпись неизвестна, и возьмёт её из текста ответа бота.
    """
    found: list[Button] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"button", "url"}:
            continue
        if not node.args:
            continue
        label = _literal(node.args[0])
        if not isinstance(label, str):
            continue
        cb: str | None = None
        if len(node.args) > 1:
            raw_cb = _literal(node.args[1])
            # Не строка (число, None, вычисленное выражение) — точный callback
            # из кода не извлекается, и придумывать его нельзя.
            cb = raw_cb if isinstance(raw_cb, str) else None
        if node.func.attr == "url" and cb is None:
            cb = "<url>"
        found.append(Button(label=label, callback=cb, source=source))
    return found


# Локальные хелперы кнопок в ботах называют по-разному: btn, kb, button,
# InlineKeyboardButton. Их аргументы всё равно два строковых литерала рядом.
_BUTTON_FACTORY_HINTS = ("btn", "button", "kb", "key", "ikb")


def _find_wrapped_buttons(tree: ast.AST, source: str) -> list[Button]:
    """Кнопки, собранные локальным хелпером: btn("Записаться", "act_book").

    Фабрика ловится по двум строковым литералам в вызове и по имени функции,
    содержащему btn/button/key. Без этого кейс, написанный по отчёту, не увидел
    бы ни одной кнопки у бота, который их показывает через хелпер.
    """
    found: list[Button] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        low = name.lower()
        if not any(h in low for h in _BUTTON_FACTORY_HINTS):
            continue
        lits = [_literal(a) for a in node.args[:2]]
        if len(lits) >= 2 and all(isinstance(x, str) for x in lits[:2]):
            found.append(Button(label=str(lits[0]), callback=str(lits[1]), source=source))
    return found


def _find_texts(tree: ast.AST, source: str) -> list[TextReply]:
    """Тексты, которые бот реально показывает: message.answer/edit_text/send.

    Учитываются и позиционный аргумент (`answer("Привет")`), и именованный
    (`answer(text="Привет")`, `send_document(caption=...)`): в разных ботах форма
    разная, а кейс должен ссылаться на то, что видит клиент.
    """
    texts: list[TextReply] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"answer", "edit_text", "send"}:
            continue
        candidates: list[ast.AST] = list(node.args[:1])
        candidates += [kw.value for kw in node.keywords if kw.arg in {"text", "caption"}]
        for cand in candidates:
            val = _literal(cand)
            if isinstance(val, str) and val.strip():
                texts.append(TextReply(text=val, source=source))
    return texts


def _find_callbacks(tree: ast.AST, source: str) -> list[Button]:
    """Обработчики нажатий: имя функции с callback_data в теле."""
    found: list[Button] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "callback_query":
                data = None
                for kw in call.keywords:
                    if kw.arg == "data":
                        data = _literal(kw.value)
                label = data if isinstance(data, str) else f"<{node.name}>"
                found.append(Button(label=label, callback=node.name, source=source))
                break
    return found


def _command_literal(node: ast.AST) -> str | None:
    """Команда из @router.message(Command("start")) — объявление через Command.

    Это второй распространённый способ (наряду с F.text == "/start"), и без него
    инспектор показывал бы боту, у которого команды есть, пустой список.
    """
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)):
            continue
        if sub.func.id != "Command" or not sub.args:
            continue
        raw = _literal(sub.args[0])
        if isinstance(raw, str):
            return "/" + raw.lstrip("/")
    return None


def _f_text_commands(node: ast.AST, rel: str, lineno: int) -> list[Command]:
    """Команды, объявленные magic-фильтром: @router.message(F.text == "/start").

    В aiogram это основной способ повесить команду, а не `@router.command`, и
    пропустить его — значит показать агенту пустой список команд у бота, который
    их обрабатывает. Разбираем две формы: `F.text == "/start"` (Compare) и
    `F.text.startswith("/help")` (Call на атрибуте F.text).
    """
    found: list[Command] = []

    def _want(attr: str | None) -> bool:
        return attr in ("text", "caption")

    for sub in ast.walk(node):
        # Форма 1: F.text == "/start"  (ast.Compare)
        if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Attribute):
            if not _want(sub.left.attr):
                continue
            for op, comp in zip(sub.ops, sub.comparators, strict=True):
                if (
                    isinstance(op, ast.Eq)
                    and isinstance(comp, ast.Constant)
                    and isinstance(comp.value, str)
                    and comp.value.startswith("/")
                ):
                    found.append(Command(command=comp.value, doc=None, source=f"{rel}:{lineno}"))
                    break
        # Форма 2: F.text.startswith("/help")  (ast.Call)
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if not _want(sub.func.attr) or sub.func.attr not in ("startswith", "func"):
                continue
            if not sub.args:
                continue
            raw = _literal(sub.args[0])
            if isinstance(raw, str) and raw.startswith("/"):
                cmd = f"{raw}()" if sub.func.attr == "func" else raw
                found.append(Command(command=cmd, doc=None, source=f"{rel}:{lineno}"))

    # дедупликация по (команда, файл:строка): один обработчик может матчить
    # и F.text, и что-то ещё
    uniq: dict[tuple[str, str], Command] = {}
    for c in found:
        uniq.setdefault((c.command, c.source), c)
    return list(uniq.values())


def inspect_repo(root: pathlib.Path) -> Report:
    """Разобрать репозиторий и вернуть отчёт: команды, кнопки, тексты, поколение."""
    builder, reason = detect_builder(root)
    rep = Report(root=root, builder=builder, builder_reason=reason)
    files = _iter_py(root)
    rep.files_scanned = len(files)

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError) as exc:
            rep.notes.append(f"не разобран {path.name}: {type(exc).__name__}")
            continue
        rel = path.relative_to(root).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    fname = getattr(dec.func, "attr", "")
                    if fname == "command":
                        raw = _literal(dec.args[0]) if dec.args else None
                        cmd = raw if isinstance(raw, str) else f"<{node.name}>"
                        doc = ast.get_docstring(node)
                        rep.commands.append(
                            Command(
                                command=cmd,
                                doc=(doc or "").strip() or None,
                                source=f"{rel}:{node.lineno}",
                            )
                        )
                        rep.handlers.append(f"{cmd} → {rel}:{node.lineno}")
                    elif fname == "message":
                        # Команда может быть спрятана в magic-фильтре, а может быть
                        # объявлена как Command("start") — оба способа равноправны.
                        literal_cmd = _command_literal(dec)
                        if literal_cmd:
                            rep.commands.append(
                                Command(
                                    command=literal_cmd, doc=None, source=f"{rel}:{node.lineno}"
                                )
                            )
                            rep.handlers.append(f"{literal_cmd} → {rel}:{node.lineno}")
                        else:
                            for c in _f_text_commands(dec, rel, node.lineno):
                                rep.commands.append(c)
                                rep.handlers.append(f"{c.command} → {rel}:{node.lineno}")

        rep.buttons.extend(_find_buttons(tree, rel))
        rep.buttons.extend(_find_wrapped_buttons(tree, rel))
        rep.texts.extend(_find_texts(tree, rel))
        rep.buttons.extend(_find_callbacks(tree, rel))

    # дедупликация: один и тот же callback описан в нескольких местах — норма
    seen: set[tuple[str, str | None]] = set()
    uniq: list[Button] = []
    for b in rep.buttons:
        key = (b.label, b.callback)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)
    rep.buttons = uniq
    return rep


def render_report(rep: Report) -> str:
    """Отчёт как текст: то, что агент читает, прежде чем написать кейс."""
    lines: list[str] = []
    lines.append(f"# Разбор репозитория: {rep.root.name}")
    lines.append("")
    lines.append(f"- файлов разобрано: {rep.files_scanned}")
    lines.append(f"- поколение сборки: {rep.builder or '?'} ({rep.builder_reason})")
    lines.append("")

    lines.append(f"## Обработчики команд ({len(rep.commands)})")
    for c in rep.commands:
        doc = f" — {c.doc.splitlines()[0]}" if c.doc else ""
        lines.append(f"- `{c.command}`{doc}  [{c.source}]")
    lines.append("")

    lines.append(f"## Кнопки ({len(rep.buttons)})")
    for b in rep.buttons[:200]:
        cb = f" → `{b.callback}`" if b.callback else ""
        lines.append(f"- «{b.label}»{cb}")
    if len(rep.buttons) > 200:
        lines.append(f"- … ещё {len(rep.buttons) - 200}")
    lines.append("")

    lines.append(f"## Тексты ответов бота ({len(rep.texts)})")
    for t in rep.texts[:200]:
        one = " ".join(t.text.split())
        if len(one) > 110:
            one = one[:107] + "…"
        lines.append(f"- «{one}»")
    if len(rep.texts) > 200:
        lines.append(f"- … ещё {len(rep.texts) - 200}")
    lines.append("")

    if rep.notes:
        lines.append("## Замечания разбора")
        for n in rep.notes[:50]:
            lines.append(f"- {n}")
    return "\n".join(lines)


__all__ = [
    "Button",
    "Command",
    "Report",
    "TextReply",
    "detect_builder",
    "inspect_repo",
    "render_report",
]
