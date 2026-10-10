"""Контракт границы «хост ↔ движок»: типизированная транскрипция.

Хост исполняет бота в отдельном процессе и отдаёт транскрипцию в JSON. Это
единственное место, где данные пересекают границу процессов, и именно здесь
жили два из семи дефектов ([[опыт/lessons]] №4 и №5): `answerCallbackQuery` не
считался ответом, а тост дублировал сообщение — оба раза потому, что структура
`reply` была неявным словарём.

Поэтому Turn — типизированный контракт с `extra="forbid"`: поле, которого движок
не знает, обязано **падать с именем поля**, а не игнорироваться. Иначе старый
движок молча теряет данные нового хоста, и отчёт становится ложью.

Обновление контракта — только с обеих сторон сразу: снимок форм лежит в
`tests/golden/host_turns.json`, и его прогон ловит удаление поля.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from botkit_farm.case.model import MediaKind

# Виды медиа объявлены рядом с моделью кейса (case/model.py): и `Expect`, и
# контракт границы сверяются с одним и тем же закрытым списком.


class Media(BaseModel):
    """Один вложенный файл в ответе бота."""

    model_config = ConfigDict(extra="forbid")

    kind: MediaKind
    file_id: str = ""
    caption: str = ""


class StepTurn(BaseModel):
    """Шаг отработал: клиент что-то сделал, бот ответил.

    Поля разделены намеренно. `replies` — всё, что клиент воспринимает как ответ
    (сообщения + тосты), для сверки ожиданий. `messages` и `toasts` — по
    отдельности, для кадра: в Telegram сообщение остаётся в ленте, а всплывающее
    нет, и это разница интерфейса, а не деталь отрисовки.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["step"]
    text: str = ""
    replies: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    toasts: list[str] = Field(default_factory=list)
    media: list[Media] = Field(default_factory=list)
    buttons: list[str] = Field(default_factory=list)


class NoButtonTurn(BaseModel):
    """Шаг не выполнен: кнопки, которую просил кейс, у бота не нашлось.

    Не ошибка запуска: это вердикт «кейс ссылается на несуществующую кнопку», и
    `known` нужен, чтобы показать автору, что бот предлагал вместо неё.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["no_button"]
    label: str
    known: list[str] = Field(default_factory=list)


class CrashTurn(BaseModel):
    """Бот упал на шаге: исключение внутри обработчика Update."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["crash"]
    error: str


# Дискриминированный союз: `kind` решает, какая модель разбирает запись. Без
# него незнакомый kind тихо прошёл бы как «ничего не знаю».
HostTurn = Annotated[StepTurn | NoButtonTurn | CrashTurn, Field(discriminator="kind")]

_TURNS: TypeAdapter[list[HostTurn]] = TypeAdapter(list[HostTurn])


def parse_turns(raw: object) -> list[HostTurn]:
    """Разобрать транскрипцию хоста в типизированные Turn.

    Бросает `pydantic.ValidationError` — вызывающий обязан показать имя поля:
    «поле toasts запрещено» разбирается за секунды, а «бот не отработал» — нет.
    """
    return _TURNS.validate_python(raw)


__all__ = [
    "CrashTurn",
    "HostTurn",
    "Media",
    "MediaKind",
    "NoButtonTurn",
    "StepTurn",
    "parse_turns",
]
