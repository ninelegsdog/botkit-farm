# Адаптеры: подключение чужого бота без правки фермы

Ферма исполняет композиционный корень бота — тот код, который собирает
`Dispatcher` и `Bot`. У встроенных поколений (`legacy`, `newgen`, `reminder`)
этот корень известен. У чужого бота он свой, и править ради него исходники
фермы — стоп для использования: чужой репозиторий не должен требовать правок в
инструменте.

Точка расширения — поле `builder` в кейсе:

```yaml
builder: botkit-farm.adapter:mypkg.adapters.mine        # фабрика build
builder: botkit-farm.adapter:mypkg.adapters.mine:make   # фабрика make
```

Модуль ставится **в окружение бота** (`envs/<бот>/`), а не фермы: у чужого бота
свои зависимости, и импортировать их из процесса фермы нельзя. Хост подпроцесса
импортирует ссылку уже внутри venv бота.

## Контракт

```python
async def build(
    *,
    bot_dir: pathlib.Path,
    key: str,                         # имя прогона: для ключей Redis/кэша
    port: int,                        # порт фейкового Bot API на loopback
    seed: Callable[[Any], Awaitable[None]] | None,
) -> tuple[Dispatcher, Bot, Any]:     # (dp, bot, db)
```

Проверяется структурой (`botkit_farm.adapters.contract`), а не докстрингом:

| Часть | Требование | Почему |
|---|---|---|
| `dispatcher` | есть `feed_update(bot, update)` | хост кормит ему Update'ы |
| `bot` | есть `session` | хост закрывает её в конце |
| `db` | что угодно, можно `None` | нужна фикстуре, если бот с состоянием |

Обязанности адаптера:

1. Вернуть **настоящий** диспетчер — с middleware и роутерами бота, иначе
   тестируется не бот, а пустышка.
2. **Не** создавать новый `Bot` с нуля — перевести адрес уже созданного
   (`stand.retarget_bot`), иначе молча меняются default-свойства вроде
   `parse_mode`, а значит и кадр.
3. Не ходить в сеть и не менять токен. Egress закрыт карантином до импорта бота,
   токен проверяется и после сборки.
4. Все настройки брать из окружения стенда (хост выставляет надмножество имён:
   `DATABASE_URL`, `DB_PATH`, `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, …).

Ошибка в контракте падает сразу и называет поле: адаптер, вернувший `None`,
даёт «build() обязан вернуть кортеж (диспетчер, бот, база), а вернул NoneType»,
а не `AttributeError` в середине диалога.

## Референсные адаптеры

Ниже — четыре формы композиционного корня, которые встречаются на практике.
Все четыре обслуживают aiogram 3: контракт границы фермы — это
`Dispatcher.feed_update`, и фреймворк обязан её предоставить.

### 1. `Dispatcher` вручную

```python
# mypkg/adapters/mine.py
from __future__ import annotations
import pathlib
from typing import Any

from botkit_farm import stand


async def build(*, bot_dir: pathlib.Path, key: str, port: int, seed) -> tuple[Any, Any, Any]:
    stand.enter_bot_dir(bot_dir)

    from aiogram import Bot, Dispatcher
    from mybot.config import settings
    from mybot.handlers import router

    bot = Bot(token="424242:FAKE-TOKEN-FOR-LOCAL-STAND")  # токен подставлен стендом
    dp = Dispatcher()
    dp.include_router(router)
    stand.retarget_bot(bot, port)  # перевести адрес на фейковый API
    return dp, bot, None
```

### 2. Composition root в стиле `create_app`

Бот сам собирает приложение (`create_app()`), адаптер лишь вызывает её и
переводит адрес:

```python
async def build(*, bot_dir, key, port, seed):
    stand.enter_bot_dir(bot_dir)

    from src.core.bot_factory import create_app
    from src.core.migrations import migrate

    state = create_app()
    state.config.validate()
    await migrate(state.db)
    await stand.seed_db(state.db, seed)   # фикстура кейса
    stand.retarget_bot(state.bot, port)
    return state.dp, state.bot, state.db
```

### 3. Модульный синглтон состояния

Состояние живёт на уровне модуля, а не в объекте:

```python
async def build(*, bot_dir, key, port, seed):
    stand.enter_bot_dir(bot_dir)

    from src.core.bot_factory import create_bot, state   # модульный state
    from src.core.database import Base, engine

    await stand.wipe_state(key, state.redis_url)          # чистое состояние прогона
    state.dp.include_router(state.router)
    stand.retarget_bot(state.bot, port)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return state.dp, state.bot, state.db
```

### 4. Приложение за webhook (aiohttp)

Корень — webhook-сервер; ферме всё равно нужен `Dispatcher`, поэтому его
достаём из приложения:

```python
async def build(*, bot_dir, key, port, seed):
    stand.enter_bot_dir(bot_dir)

    from mybot.web import make_app
    from mybot.core import dp as dispatcher          # тот же объект, что кормит webhook

    app = make_app()
    stand.retarget_bot(app["bot"], port)
    return dispatcher, app["bot"], app.get("db")
```

## Чего адаптер не заменяет

Контракт — aiogram 3. Боты на других фреймворках (`python-telegram-bot`,
pyrogram, telethon) требуют переходника: ферма отдаёт
`Update` в формате aiogram, и совместимость с чужим API обеспечивает уже сам
адаптер. Готовых переводников пока нет — это следующая ступень, а не то, что
уже работает.
