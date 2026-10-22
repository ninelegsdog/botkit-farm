"""Обязательства покрытия: какой поток каждого бота обязан иметь кейс (ТЗ D-coverage).

Зачем отдельный модуль, а не ещё один тест в каталоге. Каталог `examples/`
отвечает на вопрос «регресс X существует». Здесь другой вопрос: **у каждого
бота есть кейс на его ключевой поток**, а не только на админ-гейт. Разница
измерима: `support` имел ровно один кейс — `support-admin-guard`, то есть
проверялся отказ гостю, но ни один счастливый путь. Гейт этого не видел, потому
что «кейсы есть» и «потоки покрыты» — разные утверждения.

Версия 1 манифеста знала 18 кейсов из 32 и проверяла ровно одно: обязательство
→ существующий кейс нужного вида. Дыр было две, и обе — молчание:

* бот, у которого не осталось ни одного обязательства, проходил гейт: список
  `by_bot` собирался из самого манифеста, и бот вне манифеста был не виден;
* кейс, не упомянутый в манифесте, тоже был не виден — а значит, не был ни
  исполнен, ни защищён.

Версия 2 манифеста (`cases/coverage.json`) закрывает обе дыры. Теперь манифест —
это два списка:

* `obligations` — обязательства: бот, поток, кейс, вид (`positive` — работа
  потока, `gate` — отказ);
* `extras` — кейсы, объявленные, но не ключевые: бот, кейс и **причина**
  (`why`). Причина обязательна, как `why` в `catalog.json`: исключение без
  причины неотличимо от недосмотра.

Реестр флота лежит отдельным файлом — `cases/fleet.json`: список ботов, у
каждого из которых обязан быть `positive`-кейс. Отдельный файл нужен потому,
что реестр требуется не только гейту, но и сверке с боевым `ops/e2e/bots.yml`.

Правила гейта:

* обязательство ссылается на существующий кейс и этот кейс действительно имеет
  нужный вид — иначе «поток считают покрытым, а кейса нет»;
* бот с обязательствами обязан иметь хотя бы один `positive` — иначе бот
  доказывает только умение отказывать;
* R1 — каждый кейс каталога объявлен: в `obligations` или в `extras`;
* R2 — каждый бот из `cases/fleet.json` имеет `positive`-обязательство;
* R3 — у каждого `extra` непустая причина;
* R4 — кейс не объявлен одновременно в `obligations` и в `extras`.
"""

from __future__ import annotations

import json
import pathlib
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from botkit_farm.case.loader import CaseError, load_file
from botkit_farm.case.model import Case

MANIFEST = "coverage.json"
FLEET = "fleet.json"
SCHEMA_VERSION = 2

BotName = Annotated[str, StringConstraints(min_length=1, max_length=80)]


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


class Extra(_Strict):
    """Кейс вне обязательств: объявлен, но поток не ключевой.

    `why` — почему поток не выбран ключевым. Схема требует непустую строку
    (`min_length=1`, как `why` в `catalog.json`), но её сообщение («поле
    extras.3.why: …») не называет бота и кейс, а чинить надо именно их.
    Поэтому пустая причина переводится в сообщение гейта: `extra <bot>/<case>:
    не указана причина`.
    """

    bot: str = Field(min_length=1, max_length=80)
    case: str = Field(min_length=1, max_length=256)
    why: str = Field(min_length=1, max_length=300)


class Obligations(_Strict):
    """Манифест обязательств покрытия (версия 2)."""

    schema_version: Literal[2]
    obligations: list[Obligation] = Field(min_length=1)
    extras: list[Extra]


class Fleet(_Strict):
    """Реестр флота: какие боты вообще обязаны быть покрыты.

    Отдельный файл (`cases/fleet.json`), а не поле манифеста: реестр нужен ещё и
    треку сверки с боевым `ops/e2e/bots.yml`, и держать его в манифесте
    обязательств значило бы смешать два разных контракта в одном.
    """

    schema_version: Literal[1]
    bots: list[BotName] = Field(min_length=1)


def _where(error: ValidationError, raw: dict[str, Any]) -> str:
    first = error.errors()[0]
    reason = _extra_reason_problem(raw, tuple(first["loc"]))
    if reason is not None:
        return reason
    # `or "<корень>"` — защитный запасной вариант: у ошибок схемы `loc` всегда
    # непуст (перебором входов пустой локации достичь не удалось), поэтому
    # мутации этого литерала эквивалентны. Разделитель `.` проверен тестом.
    loc = ".".join(str(p) for p in first["loc"]) or "<корень>"  # pragma: no mutate
    return f"поле {loc}: {first['msg']}"


def _extra_reason_problem(raw: dict[str, Any], loc: tuple[object, ...]) -> str | None:
    """Правило R3: у `extra` нет непустой причины — назвать бота и кейс.

    Сообщение схемы («поле extras.3.why: String should have at least 1
    character») читателю ничего не даёт: непонятно, к какому боту и кейсу это
    относится. Здесь оно становится таким же, как остальные проблемы гейта:
    `extra <bot>/<case>: не указана причина`.

    `None` — ошибка не про причину, тогда текст оставляет общий `_where`.
    """
    if len(loc) == 3 and loc[0] == "extras" and loc[2] == "why":
        entry = raw["extras"][loc[1]]
        return f"extra {entry['bot']}/{entry['case']}: не указана причина"
    return None


def _read_json(path: pathlib.Path, missing: str) -> dict[str, Any]:
    """Прочитать JSON-манифест и вернуть словарь.

    Ошибки — `CoverageError` с понятным текстом: читатель должен знать, какой
    файл не читается и почему, а не разбираться в стеке `json`.
    """
    try:
        # Кодировка явная: `encoding="UTF-8"`/`None` эквивалентны `utf-8` на
        # UTF-8-локали, а другой на этом проекте не бывает. Мутации литерала
        # потому эквивалентны — исключены из прогона явно.
        text = path.read_text(encoding="utf-8")  # pragma: no mutate
    except OSError as exc:
        raise CoverageError(f"{missing}: {path}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CoverageError(f"{path}: невалидный JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CoverageError(f"{path}: манифест должен быть объектом, не {type(raw).__name__}")
    return raw


def load(coverage_dir: pathlib.Path) -> Obligations:
    """Прочитать манифест обязательств.

    Бросает CoverageError с понятным сообщением, а не ValidationError.
    """
    manifest = coverage_dir / MANIFEST
    raw = _read_json(manifest, "нет манифеста покрытия")
    version = raw.get("schema_version")
    if version == 1:
        raise CoverageError(f"{manifest}: манифест версии 1: обновите до 2 (добавьте поле extras)")
    if version is not None and version != SCHEMA_VERSION:
        raise CoverageError(
            f"{manifest}: манифест версии {version}: гейт знает только версию {SCHEMA_VERSION}"
        )
    try:
        return Obligations.model_validate(raw)
    except ValidationError as exc:
        raise CoverageError(f"{manifest}: {_where(exc, raw)}") from exc


def load_fleet(coverage_dir: pathlib.Path) -> Fleet:
    """Прочитать реестр флота (`cases/fleet.json`).

    Отдельная функция, а не поле манифеста: реестр нужен и сверке с боевым
    `ops/e2e/bots.yml` (трек T8), и держать его в манифесте обязательств
    значило бы смешать два контракта в одном файле.
    """
    path = coverage_dir / FLEET
    raw = _read_json(path, "нет реестра флота")
    try:
        return Fleet.model_validate(raw)
    except ValidationError as exc:
        raise CoverageError(f"{path}: {_where(exc, raw)}") from exc


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
    """Сверить обязательства с кейсами и флотом. Пустой список — покрытие в порядке.

    Возвращает полный список проблем, а не падает на первой: чинить за один
    проход. Правила:

    * обязательство → существующий кейс нужного вида (`positive`/`gate`);
    * бот с обязательствами имеет хотя бы один `positive`;
    * R1 — каждый кейс каталога объявлен в манифесте;
    * R2 — каждый бот из `cases/fleet.json` имеет `positive`-обязательство;
    * R3 — у каждого `extra` непустая причина (её проверяет схема манифеста —
      см. `Extra`; текст ошибки даёт понятное сообщение гейта);
    * R4 — кейс не объявлен одновременно в `obligations` и в `extras`.
    """
    try:
        obligations = load(coverage_dir)
    except CoverageError as exc:
        return [str(exc)]

    problems: list[str] = []

    # Реестр флота: без него правило R2 нечем проверять. Отсутствие реестра —
    # проблема, а не «нечего проверять»: молчание здесь означало бы, что бот
    # без обязательств проходит гейт (ровно та дыра, которую закрывает v2).
    fleet: Fleet | None = None
    try:
        fleet = load_fleet(coverage_dir)
    except CoverageError as exc:
        problems.append(str(exc))

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

    # R1: весь корпус объявлен. Кейс вне манифеста не исполняется и не виден
    # гейту — раньше так были 14 кейсов из 32.
    declared = {ob.case for ob in obligations.obligations}
    declared |= {extra.case for extra in obligations.extras}
    for path in sorted(coverage_dir.glob("*.y*ml")):
        if path.name not in declared:
            problems.append(f"кейс не объявлен в манифесте: {path.name}")

    # R2: каждый бот флота обязан иметь positive-обязательство — реестр флота
    # это обещание «бот работает», а не «бот упомянут в манифесте».
    if fleet is not None:
        positives = {ob.bot for ob in obligations.obligations if ob.kind == "positive"}
        for bot in fleet.bots:
            if bot not in positives:
                problems.append(f"{bot}: нет positive-обязательства — бот в реестре флота")

    # R4: кейс не может быть одновременно обязательством и «лишним» — иначе
    # непонятно, чему он подчиняется, и правка одного места ломает другое.
    in_obligations = {ob.case for ob in obligations.obligations}
    in_extras = {extra.case for extra in obligations.extras}
    for dup_case in sorted(in_obligations & in_extras):
        problems.append(f"кейс объявлен дважды: {dup_case}")

    return problems


__all__ = [
    "FLEET",
    "MANIFEST",
    "SCHEMA_VERSION",
    "CoverageError",
    "Extra",
    "Fleet",
    "Obligation",
    "Obligations",
    "check",
    "load",
    "load_fleet",
]
