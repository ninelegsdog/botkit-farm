"""Тест-кейс: декларативное описание проверки поведения бота.

Кейс — это YAML, а не код Python. Причина не в «данные отдельно от логики», а в
том, что кейсы пишутся дважды в жизни: агент генерирует их, изучив код
репозитория, и человек правит их после падения. Python в этой роли требует
исправления синтаксиса и не даёт подсказки, а YAML даёт: неизвестное поле — ошибка
валидации с именем поля, а не AttributeError в середине прогона.

Что кейс НЕ может: объявить ожидаемый текст, которого нет в коде бота. Поле
`expect` обязано быть заполнено тем, что репозиторий действительно содержит,
иначе прогон будет проверять не бота, а фантазию автора кейса.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from botkit_farm.adapters import registry as adapters
from botkit_farm.case.migration import SCHEMA_VERSION


class Strict(BaseModel):
    """Общая база: лишнее поле — ошибка, а не тихо проигнорированная опечатка.

    `model_config` с extra='forbid' — главная защита от кейса, который «прошёл»,
    потому что проверял поле, которого нет в схеме: опечатка в `expect:` иначе
    выглядела бы как «всё хорошо, ожиданий не было».
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# Ограничение длины текста — не косметика. Регулярка на сообщение в 10 МБ проходит
# мгновенно, но съедает память раннера и время на отчёт; 4 КБ заведомо больше
# любой реплики бота.
MAX_TEXT = 4096


class Actor(Strict):
    """Один участник (роль) в мультипользовательском кейсе.

    У каждого участника свой `user_id` — это ключ для изоляции FSM бота.
    Имя опционально, для кадра. `is_bot` — для бот-акторов (редко).
    """

    user_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=64)
    is_bot: bool = False


class ChatSpec(Strict):
    """Настройки чата для группового/тредового кейса.

    `type`: private | group | supergroup. `title` — для групп/супергрупп.
    `thread_id` — для форумов (топики). `bot_username` — для упоминаний.
    """

    type: Literal["private", "group", "supergroup"] = "private"
    title: str | None = Field(default=None, max_length=128)
    thread_id: int | None = Field(default=None, ge=1)
    bot_username: str | None = Field(default=None, max_length=32)


# Виды медиа, которые ферма умеет ловить и рисовать. Список закрыт намеренно:
# неизвестный вид — это сигнал, что кейс и фейковый API разъехались, а не
# «нарисуем пустоту» (роадмап B2). Живёт рядом с Expect, потому что и модель
# кейса, и контракт границы (runner/contract.py) сверяются с одним списком.
MediaKind = Literal["photo", "document", "voice", "sticker", "video", "animation"]


class Expect(Strict):
    """Что должно быть правдой после шага.

    Пустое ожидание допустимо только как `silent: true` (бот промолчал — это
    тоже проверка). Молчаливое «ничего не проверял» без silent было бы кадром
    в отчёт, ничего не доказывающим.
    """

    text_contains: list[str] = Field(default_factory=list)
    text_equals: str | None = None
    buttons_contains: list[str] = Field(default_factory=list)
    buttons_count: int | None = Field(default=None, ge=0)
    silent: bool = False
    # Медиа в ответе — не текст, и проверять его текстом нельзя: бот может
    # прислать фото с подписью «Прайс» или без неё. Проверки отдельные.
    media_kind: MediaKind | None = None
    media_count: int | None = Field(default=None, ge=0)
    caption_contains: list[str] = Field(default_factory=list)
    # Якорь в коде: используется sync для обнаружения дрейфа
    anchor: str | None = Field(default=None, max_length=512)
    # Ограничение на длину ответа в символах: ловит «бот ответил пустым» и
    # «бот ушёл в бесконечный цикл печати» раньше, чем это увидит человек.
    max_len: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _нужно_хоть_что_то(self) -> Expect:
        if not (
            self.text_contains
            or self.text_equals
            or self.buttons_contains
            or self.buttons_count is not None
            or self.silent
            or self.max_len
            or self.media_kind is not None
            or self.media_count is not None
            or self.caption_contains
        ):
            raise ValueError(
                "ожидание пустое: перечислено ни одно условие. Либо опишите, что "
                "должно быть в ответе, либо укажите silent: true, если бот "
                "молчание — тоже проверка"
            )
        if self.text_equals is not None and self.text_contains:
            raise ValueError("text_equals и text_contains вместе: либо точное, либо содержит")
        return self


class Step(Strict):
    """Один шаг диалога.

    Два вида действия: `send` (клиент пишет текст) и `click` (клиент жмёт
    inline-кнопку). `click` принимает подпись кнопки, а не callback_data:
    подпись — это то, что видит человек в Telegram, и тест-кейс должен
    проверять интерфейс, а не внутреннее состояние FSM бота.
    """

    send: str | None = Field(default=None, max_length=MAX_TEXT)
    click: str | None = Field(default=None, max_length=256)
    # Нажать кнопку по позиции, а не по подписи: first | last | 0 | 1 | ...
    # Нужно там, где бот показывает то, что клиент не знает заранее, — даты,
    # идентификаторы, слоты. Зашивать их в кейс нельзя: завтра они изменятся,
    # и кейс упадёт не из-за кода бота, а из-за своей устаревшей копии.
    pick: str | None = Field(default=None, pattern=r"^(first|last|[0-9]+)$")
    # Ожидание относится именно к этому шагу. Не переносится на следующий:
    # «ответ на /start» и «ответ на кнопку» — разные утверждения.
    expect: Expect | None = None
    # Пауза перед шагом в секундах. Нужна там, где бот тротттлит (игнорирует
    # быстрые сообщения), иначе «бот молчит» — это артефакт стенда, а не баг.
    pause: float | None = Field(default=None, ge=0, le=60)
    # Пометка «этот шаг можно упасть без порчи кейса» — для навигационных
    # шагов, где у бота нет одного правильного ответа.
    optional: bool = False
    # Роль исполнителя шага (ТЗ C3). Если в кейсе заданы actors, поле обязательно
    # и должно ссылаться на существующую роль. Если actors пуст — поле игнорируется.
    as_: str | None = Field(default=None, alias="as", max_length=32)
    # Якорь шага в коде (команда/кнопка/обработчик)
    anchor: str | None = Field(default=None, max_length=512)
    note: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _одно_действие(self) -> Step:
        given = [x for x in (self.send, self.click, self.pick) if x is not None]
        if len(given) != 1:
            raise ValueError(
                "шаг должен быть ровно один из send, click, pick — не несколько и не ни одного"
            )
        if self.send is not None and not self.send.strip():
            raise ValueError("send пустой: бот не получит ничего")
        return self

    def label(self) -> str:
        """Человекочитаемое имя шага для отчёта и ошибок."""
        if self.click is not None:
            return f"нажать «{self.click}»"
        if self.pick is not None:
            return f"нажать кнопку {self.pick}"
        head = str(self.send).splitlines()[0][:40]
        return f"написать «{head}»"


class Case(Strict):
    """Тест-кейс целиком."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    origin: Literal["generated", "manual"] | None = None
    status: Literal["active", "removed"] | None = None

    # id — часть имени файла отчёта и имя кадра, поэтому только безопасные
    # символы: иначе «../../etc/passwd» уедет из каталога отчётов.
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)

    # Откуда берётся бот: локальный путь или git-URL. Обязательно явно, чтобы
    # «прогнать что-нибудь» не означало «клонировать что попало из сети».
    source: str = Field(min_length=1, max_length=2048)
    # Поколение сборки бота: legacy (Kit v2), newgen (create_app), reminder
    # (модульный синглтон) или сторонний адаптер ссылкой
    # `botkit-farm.adapter:<модуль>[:<фабрика>]`. Определяется по коду
    # репозитория, если не задан. Форма ссылки проверяется сразу: существование
    # модуля — нет, он живёт в окружении бота, недоступном ферме.
    builder: str | None = Field(default=None, max_length=256)
    # Отдавать диспетчеру сырой JSON вместо готового объекта Update (ТЗ C2).
    # Тот же диалог, но проверяется ещё и десериализация — путь, которым Update
    # приходит с webhook/polling. По умолчанию выключено: это строже, а не
    # обязательнее.
    raw: bool = False
    # Фикстуры — модуль с seed(db) в cases/fixtures/, заполняющий базу до
    # прогона. Без неё кейс стартует на пустой базе.
    fixture: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")
    # Фикстура может и понадобиться, а может и нет — пустой список фикстур
    # означает «свежая база».
    seed: bool = True

    # Роли (участники) для мультипользовательских кейсов (ТЗ C3).
    # Словарь: роль -> Actor(user_id, name?). Пустой = обычный кейс с одним
    # пользователем (FAKE_USER_ID). Если задан, шаги должны иметь `as: роль`.
    actors: dict[str, Actor] = Field(default_factory=dict)
    # Настройки чата: тип (private/group/supergroup), название, тред, ботюзернейм.
    chat: ChatSpec | None = None
    # Режим прогона: polling (feed_update) или webhook (HTTP POST на /webhook).
    # По умолчанию polling — совместимость с существующими кейсами.
    mode: Literal["polling", "webhook"] = "polling"

    # Заголовок кадра: имя бота в шапке и подпись «демо-интерфейс» на фото.
    frame_title: str | None = Field(default=None, max_length=80)
    frame_handle: str | None = Field(default=None, max_length=80)
    # Режим кадра: window — поверх снимка окна (1920×1054), phone — портрет,
    # wide — обычный макет 1280×720. window требует подложки в assets/.
    frame: Literal["window", "phone", "wide"] = "window"
    # Помечать кадр как пригодный для публикации. Кадр с упавшим кейсом сюда не
    # попадает: на публикацию идут только проверенные.
    publishable: bool = True

    steps: list[Step] = Field(min_length=1, max_length=200)
    # Теги для отчёта и фильтрации: smoke, regression, ui, edge.
    tags: list[str] = Field(default_factory=list)
    # Регресс, который кейс закрывает: «issue-12». Попадает в отчёт, чтобы
    # было видно, что кейс появился не зря.
    covers: str | None = Field(default=None, max_length=64)

    @field_validator("tags")
    @classmethod
    def _теги_из_known_set(cls, v: list[str]) -> list[str]:
        known = {"smoke", "regression", "ui", "edge", "security"}
        bad = [t for t in v if t not in known]
        if bad:
            raise ValueError(f"неизвестные теги {bad}; допустимы {sorted(known)}")
        return v

    @field_validator("builder")
    @classmethod
    def _builder_допустим(cls, v: str | None) -> str | None:
        """Форма ссылки на адаптер проверяется сразу, а не в середине прогона.

        Существование плагина — нет: его модуль стоит в окружении бота, а не
        фермы. Но опечатку в имени встроенного адаптера видно уже на загрузке.
        """
        if v is not None and not adapters.is_valid(v):
            raise ValueError(adapters.unknown_builder(v))
        return v

    @model_validator(mode="after")
    def _не_противоречит(self) -> Case:
        # Кейс без ожиданий ни на одном шаге ничего не проверяет: кадр получится,
        # баг не поймается, отчёт будет зелёным и бессмысленным. Единственное
        # исключение — пометка silent хотя бы на одном шаге.
        has_any = any(s.expect for s in self.steps)
        if not has_any and not any(s.expect and s.expect.silent for s in self.steps):
            raise ValueError(
                "ни на одном шаге нет ожидания: такой кейс не может упасть, "
                "а значит не доказывает ничего"
            )
        return self

    @model_validator(mode="after")
    def _actors_и_as_согласованы(self) -> Case:
        """Если заданы actors, каждый шаг должен иметь as, ссылающийся на роль."""
        if not self.actors:
            return self
        role_names = set(self.actors.keys())
        for i, step in enumerate(self.steps):
            role = step.as_
            if not role:
                raise ValueError(
                    f"шаг {i + 1}: поле 'as' обязательно, когда заданы actors. "
                    f"Допустимые роли: {sorted(role_names)}"
                )
            if role not in role_names:
                raise ValueError(
                    f"шаг {i + 1}: роль {role!r} не найдена в actors. "
                    f"Допустимые: {sorted(role_names)}"
                )
        # user_id должны быть уникальны (для изоляции FSM)
        user_ids = [a.user_id for a in self.actors.values()]
        if len(user_ids) != len(set(user_ids)):
            raise ValueError("actors: user_id должны быть уникальными для изоляции FSM")
        return self


# Плейсхолдеры вида {{...}} — незаполненные слоты. Проверка на этапе загрузки,
# а не на этапе рендера: незаполненный кейс должен падать до того, как бот
# запущен и база стёрта.
_SLOT = re.compile(r"\{\{[^}]*\}\}")

# Значения, которые почти наверняка означают, что кейс не доделан.
_PLACEHOLDERS = re.compile(r"(?i)\b(TODO|FIXME|XXX|заглушка|позже|дописать|уточнить)\b")


def find_slots(text: str) -> list[str]:
    """Найти незаполненные слоты в тексте кейса."""
    return _SLOT.findall(text)


def find_placeholders(text: str) -> list[str]:
    """Найти слова-заглушки, которые означают недописанный кейс."""
    return _PLACEHOLDERS.findall(text)


__all__ = [
    "Actor",
    "Case",
    "ChatSpec",
    "Expect",
    "Step",
    "MAX_TEXT",
    "find_slots",
    "find_placeholders",
]
