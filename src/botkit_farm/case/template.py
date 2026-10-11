"""Шаблоны кейсов (ТЗ C6): Jinja2 в песочнице и с белым списком переменных.

Зачем это здесь, а не «просто `str.format`»: длинные сценарии (магазин с
корзиной, поддержка с ветвлением) не выражаются линейным списком, им нужны
`repeat` и подстановка значений из прошлых ответов. Jinja2 даёт и то, и другое,
но по умолчанию он исполняет произвольный Python-код: `{{ ''.__class__ }}`
дотягивается до объектов, а через них — до импорта.

Поэтому два заслона, и оба срабатывают **на чтении кейса**, до запуска бота:

* `SandboxedEnvironment` — обращение к «приватным» атрибутам (`_` в начале)
  блокируется на рендере;
* `check` разбирает шаблон и сверяет имена переменных с белым списком, а по
  дереву разбора ищет те же приватные атрибуты. Неизвестная переменная — отказ
  с её именем, а не пустая строка в отчёте.

Рендер живёт в процессе фермы, а не бота: в окружении бота Jinja2 нет и быть не
должно — ферма не тащит свои зависимости в чужой venv.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from jinja2 import StrictUndefined, nodes
from jinja2 import meta as jinja_meta
from jinja2.exceptions import SecurityError, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

# Переменные, доступные в действии шага (send/click/pick): индекс повтора.
# `i` — с нуля, `n` — сколько всего повторов.
ACTION_VARS = frozenset({"i", "n"})
# В ожидании, кроме индекса, доступны прошлые ответы бота: `turns` — список
# текстов ответов (в порядке появления), `turns[-1]` — последний.
EXPECT_VARS = frozenset({"i", "n", "turns"})

# Признак того, что строка вообще содержит шаблон: `{{ }}`, `{% %}` или `{# #}`.
# Нужен, чтобы не гонять парсер Jinja по обычному тексту 99% кейсов.
_HAS_TEMPLATE = re.compile(r"\{\{|\{%|\{#")

# Окружение создаётся один раз: `from_string` компилирует шаблон при каждом
# вызове, а окружение неизменно и потокобезопасно для чтения.
_ENV = SandboxedEnvironment(undefined=StrictUndefined)


class TemplateError(Exception):
    """Шаблон кейса недопустим: неизвестная переменная или запрещённый код.

    Сообщение всегда называет место (`где`) и причину: имя переменной или
    атрибута. Автор кейса должен понять правку, не читая исходников раннера.
    """


def has_template(text: str) -> bool:
    """Есть ли в строке шаблонный синтаксис вообще."""
    return bool(_HAS_TEMPLATE.search(text))


def _unsafe_attribute(ast: nodes.Template) -> str | None:
    """Имя первого приватного атрибута/ключа в шаблоне, иначе None.

    Это не замена песочнице, а отказ раньше: `{{ ''.__class__ }}` обязан
    упасть при чтении кейса с внятным текстом, а не SecurityError в недрах
    рендера во время прогона.
    """
    for attr in ast.find_all(nodes.Getattr):
        if attr.attr.startswith("_"):
            return attr.attr
    for item in ast.find_all(nodes.Getitem):
        arg = item.arg
        if (
            isinstance(arg, nodes.Const)
            and isinstance(arg.value, str)
            and arg.value.startswith("_")
        ):
            return arg.value
    return None


def check(text: str, allowed: frozenset[str], where: str) -> None:
    """Проверить шаблон: синтаксис, имена переменных, отсутствие кода.

    Бросает `TemplateError` с указанием места. Обычный текст (без `{{ }}`)
    проходит без разбора — это подавляющее большинство полей кейса.
    """
    if not has_template(text):
        return
    try:
        ast = _ENV.parse(text)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"{where}: синтаксис шаблона: {exc.message}") from exc

    unsafe = _unsafe_attribute(ast)
    if unsafe is not None:
        raise TemplateError(
            f"{where}: шаблон обращается к приватному атрибуту {unsafe!r} — "
            f"исполнение кода запрещено"
        )

    unknown = sorted(jinja_meta.find_undeclared_variables(ast) - allowed)
    if unknown:
        raise TemplateError(
            f"{where}: неизвестные переменные {unknown}; допускаются {sorted(allowed)}"
        )


def render(text: str, context: Mapping[str, object], where: str = "шаблон") -> str:
    """Отрендерить шаблон с контекстом. Обычный текст возвращается как есть.

    Ошибки песочницы, неизвестной переменной и синтаксиса приводятся к
    `TemplateError`: наружу не должен просачиваться SecurityError — его текст
    ничего не говорит автору кейса.
    """
    if not has_template(text):
        return text
    try:
        tmpl = _ENV.from_string(text)
        return tmpl.render(**context)
    except SecurityError as exc:
        raise TemplateError(f"{where}: запрещённая операция в шаблоне: {exc}") from exc
    except UndefinedError as exc:
        raise TemplateError(f"{where}: {exc}") from exc
    except TemplateSyntaxError as exc:
        raise TemplateError(f"{where}: синтаксис шаблона: {exc.message}") from exc


__all__ = [
    "ACTION_VARS",
    "EXPECT_VARS",
    "TemplateError",
    "check",
    "has_template",
    "render",
]
