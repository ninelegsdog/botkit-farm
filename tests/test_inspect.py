"""Проверки инспектора: разбор репозитория должен давать факты, а не догадки.

Инспектор кормит кейсы. Ошибка в нём опаснее, чем кажется: агент напишет в кейс
команду, которой у бота нет, и получит «баг», которого нет. Поэтому проверки
бьют по главному: команда найдена или нет, поколение определено или нет.
"""

from __future__ import annotations

import pathlib
import textwrap

from botkit_farm.inspector.repo import detect_builder, inspect_repo, render_report

NEWGEN = textwrap.dedent("""
    from aiogram import Router, F
    from aiogram.types import Message, CallbackQuery

    router = Router()

    def register_routers(state):
        return router

    @router.message(F.text == "/start")
    async def start(message: Message) -> None:
        await message.answer("Добро пожаловать")
        await message.answer("Выберите действие",
                             reply_markup=kb.row(btn("Записаться", "act_book")))

    @router.callback_query(F.data == "act_book")
    async def book(call: CallbackQuery) -> None:
        await call.message.edit_text("Выберите услугу")
""")

LEGACY = textwrap.dedent("""
    def build_dispatcher(storage, throttling):
        return dp
""")


def _repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


def test_команды_извлекаются(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN, "src/bot.py": ""})
    rep = inspect_repo(root)
    assert "/start" in rep.command_names()


def test_подписи_кнопок_извлекаются(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN, "src/bot.py": ""})
    rep = inspect_repo(root)
    labels = [b.label for b in rep.buttons]
    assert "Записаться" in labels


def test_обработчики_кнопок_извлекаются(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN, "src/bot.py": ""})
    rep = inspect_repo(root)
    cbs = [b.callback for b in rep.buttons]
    assert "book" in cbs


def test_тексты_ответов_извлекаются(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN, "src/bot.py": ""})
    rep = inspect_repo(root)
    texts = [t.text for t in rep.texts]
    assert "Добро пожаловать" in texts


def test_поколение_newgen_определяется(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN, "src/core/bot_factory.py": ""})
    builder, why = detect_builder(root)
    assert builder == "newgen"
    assert "app.py" in why


def test_поколение_legacy_определяется(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/app.py": "",
            "src/core/bot_factory.py": LEGACY,
        },
    )
    builder, _ = detect_builder(root)
    assert builder == "legacy"


def test_мусорные_каталоги_не_сканируются(tmp_path):
    """Заглянуть в .venv значит утонуть в чужих обработчиках: инспектор обязан
    пропускать их, иначе список команд будет нечитаемым мусором."""
    root = _repo(
        tmp_path,
        {
            "src/app.py": NEWGEN,
            "src/venv/site_packages/lib/bot.py": '@router.message(F.text == "/чужой")\nasync def x(m): ...',
        },
    )
    rep = inspect_repo(root)
    assert "/чужой" not in rep.command_names()


def test_битый_файл_не_роняет_разбор(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/app.py": NEWGEN,
            "src/broken.py": "def ((((",
        },
    )
    rep = inspect_repo(root)  # не должно бросить
    assert rep.notes


def test_пустой_репозиторий_даёт_пустой_отчёт(tmp_path):
    rep = inspect_repo(tmp_path)
    assert rep.commands == [] and rep.buttons == []


def test_отчёт_печатается(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN, "src/bot.py": ""})
    text = render_report(inspect_repo(root))
    assert "/start" in text and "Кнопки" in text


def test_подпись_переменной_не_выдумывается(tmp_path):
    """Если подпись собрана переменной, её нельзя угадать — инспектор честно
    молчит, чтобы агент не вписал в кейс выдуманный текст."""
    code = textwrap.dedent("""
        from aiogram.types import InlineKeyboardButton
        LABEL = "Динамическая"
        btn = InlineKeyboardButton(text=LABEL, callback_data="x")
    """)
    root = _repo(tmp_path, {"src/app.py": code})
    rep = inspect_repo(root)
    assert "Динамическая" not in [b.label for b in rep.buttons]


def test_команда_через_command_извлекается(tmp_path):
    """@router.message(Command("start")) — второй распространённый способ
    объявить команду, и без него инспектор показывал бы пустой список."""
    code = textwrap.dedent("""
        from aiogram import Router, F
        from aiogram.filters import Command

        router = Router()

        @router.message(Command("start"))
        async def start(message):
            await message.answer("Привет")

        @router.message(Command("delete_my_data"))
        async def wipe(message):
            await message.answer("Удалено")
    """)
    root = _repo(tmp_path, {"src/app.py": code})
    rep = inspect_repo(root)
    names = rep.command_names()
    assert "/start" in names and "/delete_my_data" in names


def test_команда_без_слеша_дописывается(tmp_path):
    """Command("start") — без слеша; в кейсе команда пишется как /start."""
    code = textwrap.dedent("""
        from aiogram import Router
        from aiogram.filters import Command

        @Router().message(Command("help"))
        async def h(message): ...
    """)
    root = _repo(tmp_path, {"src/app.py": code})
    assert "/help" in inspect_repo(root).command_names()


def test_pick_не_ломает_разбор_кнопок(tmp_path):
    root = _repo(tmp_path, {"src/app.py": NEWGEN})
    assert inspect_repo(root).buttons
