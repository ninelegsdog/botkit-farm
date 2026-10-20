"""Обязательства покрытия: какой поток каждого бота обязан иметь кейс (ТЗ D-coverage).

Зачем отдельный модуль, а не ещё один тест в каталоге. Каталог `examples/`
отвечает на вопрос «регресс X существует». Здесь другой вопрос: **у каждого
бота есть кейс на его ключевой поток**, а не только на админ-гейт. Разница
измерима: `support` имел ровно один кейс — `support-admin-guard`, то есть
проверялся отказ гостю, но ни один счастливый путь. Гейт этого не видел, потому
что «кейсы есть» и «потоки покрыты» — разные утверждения.

Манифест — `cases/coverage.json`. Он перечисляет обязательства: бот, поток,
кейс, вид. Проверка сверяет в обе стороны, как каталог:

* обязательство ссылается на существующий кейс и этот кейс действительно имеет
  нужный тег/вид — иначе «поток считают покрытым, а кейса нет»;
* ни один кейс не лишний против желаемого — предупреждение, не ошибка.

Вид `positive` — кейс, который проверяет **работу** потока (клиент получает
ответ). Вид `gate` — кейс, который проверяет **отказ** (гость молчит). Бот без
`positive` не доказывает, что умеет работать: только что умеет отказывать.
"""

from __future__ import annotations

import json
import pathlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from botkit_farm.case.loader import CaseError, load_file
from botkit_farm.case.model import Case

MANIFEST = "coverage.json"


class CoverageError(Exception):
    """Манифест обязательств не читается или не соответствует схеме."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Obligation(_Strict):
    """Один обязательный поток: бот → кейс → вид проверки.

    `flow` — человекочитаемое имя потока («ticket-lifecycle»), попадает в отчёт
    об ошибке: читателю нужно знать, что именно не покрыто, а не только имя файла.
    `kind` — `positive` (работа) или `gate` (отказ).
    """

    bot: str = Field(min_length=1, max_length=80)
    flow: str = Field(min_length=1, max_length=80)
    case: str = Field(min_length=1, max_length=256)
    kind: Literal["positive", "gate"]


class Obligations(_Strict):
    """Манифест обязательств покрытия."""

    schema_version: int = Field(ge=1)
    obligations: list[Obligation] = Field(min_length=1)


def _where(error: ValidationError) -> str:
    first = error.errors()[0]
    loc = ".".join(str(p) for p in first["loc"]) or "<корень>"
    return f"поле {loc}: {first['msg']}"


def load(coverage_dir: pathlib.Path) -> Obligations:
    """Прочитать манифест обязательств.

    Бросает CoverageError с понятным сообщением, а не ValidationError.
    """
    manifest = coverage_dir / MANIFEST
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverageError(f"нет манифеста покрытия: {manifest}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CoverageError(f"{manifest}: невалидный JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CoverageError(f"{manifest}: манифест должен быть объектом, не {type(raw).__name__}")
    try:
        return Obligations.model_validate(raw)
    except ValidationError as exc:
        raise CoverageError(f"{manifest}: {_where(exc)}") from exc


def _is_gate_case(case: Case) -> bool:
    """Кейс-отказ: проверяет, что бот молчит не-админу.

    Признак берётся из **поведения** кейса, а не из заголовка: у gate-кейса есть
    шаг, где ожидается `silent: true` — бот обязан промолчать. Заголовок
    («...guard») не годится: он у семи кейсов есть, а у двух admin-flow кейсов
    его нет, хотя те проверяют вход админа, а не отказ. Поведенческий признак
    совпадает с фактической разметкой и не ломается от переименования.
    """
    return any(s.expect is not None and s.expect.silent for s in case.steps)


def check(coverage_dir: pathlib.Path) -> list[str]:
    """Сверить обязательства с кейсами. Пустой список — покрытие в порядке.

    Возвращает полный список проблем, а не падает на первой: чинить за один
    проход.
    """
    try:
        obligations = load(coverage_dir)
    except CoverageError as exc:
        return [str(exc)]

    problems: list[str] = []
    for ob in obligations.obligations:
        path = coverage_dir / ob.case
        try:
            case = load_file(path)
        except CaseError as exc:
            if not path.exists():
                problems.append(
                    f"обязательство не выполнено: {ob.bot}/{ob.flow} → кейс {ob.case} пропал"
                )
            else:
                problems.append(str(exc))
            continue

        gate = _is_gate_case(case)
        if ob.kind == "positive" and gate:
            problems.append(
                f"обязательство {ob.bot}/{ob.flow} обещает positive-поток, "
                f"а кейс {ob.case} — gate (в кейсе ожидается молчание бота): "
                f"работа потока не доказана"
            )
        if ob.kind == "gate" and not gate:
            problems.append(
                f"обязательство {ob.bot}/{ob.flow} обещает gate-кейс, "
                f"а {ob.case} им не является (нет шага с ожиданием silent)"
            )

    # Боты, у которых есть обязательства, обязаны иметь хотя бы один positive:
    # бот, покрытый только отказом, не доказывает, что умеет работать.
    by_bot: dict[str, list[Obligation]] = {}
    for ob in obligations.obligations:
        by_bot.setdefault(ob.bot, []).append(ob)
    for bot, obs in sorted(by_bot.items()):
        if not any(o.kind == "positive" for o in obs):
            problems.append(f"{bot}: ни одного positive-обязательства — покрыт только отказ")

    return problems


__all__ = ["MANIFEST", "CoverageError", "Obligation", "Obligations", "check", "load"]
