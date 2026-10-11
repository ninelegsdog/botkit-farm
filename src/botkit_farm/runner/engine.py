"""Движок прогона: кейс → настоящий бот → шаги → ожидания → результат.

Механика перенесена из прежнего стенда один в один (тот же фейковый API, та же
сцена, те же адаптеры сборки). Отличие в проверке: кейс заявляет, что именно
должно быть в ответе, и несовпадение становится багом с фактами, а не тишиной.

Исполнение бота — в его собственном venv и отдельном процессе (runner/host.py).
Здесь — только подготовка, запуск и сверка.
"""

from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, replace
from typing import Any

from botkit_farm import env as envmod
from botkit_farm import fixtures as fx
from botkit_farm import security
from botkit_farm.adapters import registry as adapters
from botkit_farm.adapters.registry import AdapterRef
from botkit_farm.case import template as tmpl
from botkit_farm.case.model import Case, Expect
from botkit_farm.inspector.repo import detect_builder
from botkit_farm.runner.contract import CrashTurn, HostTurn, Media, NoButtonTurn
from botkit_farm.runner.host import ExecResult, run_bot
from botkit_farm.runner.result import CaseResult, Failure, Status, Turn

# Встроенные поколения сборки. Сторонние адаптеры подключаются ссылкой
# `botkit-farm.adapter:<модуль>` — их допустимость решает `adapters.resolve`.
BUILDERS = set(adapters.BUILTIN)
# Пауза между шагами по умолчанию: боты тротттлинг, и «бот промолчал» на быстрых
# сообщениях — артефакт стенда, а не дефект бота. В кейсе переопределяется
# через `pause`.
DEFAULT_GAP = 2.1


class RunError(Exception):
    """Прогон невозможен: нет Redis, не собралось окружение, нет адаптера."""


def _adapter_for(case: Case, bot_dir: pathlib.Path) -> AdapterRef:
    """Выбрать адаптер сборки: из кейса или по структуре репозитория.

    Три встроенных поколения отличаются не стилем, а точкой входа, и ошибка
    здесь стоит дорого: не тот адаптер даёт ImportError, который выглядит как
    баг бота. Кроме встроенных имён `builder` может быть ссылкой на сторонний
    адаптер (`botkit-farm.adapter:<модуль>`); её форму проверила модель кейса, а
    разбор в пару `module:attr` делает `adapters.resolve`.
    """
    builder: str | None = case.builder
    if builder is None:
        builder, why = detect_builder(bot_dir)
        if builder is None:
            raise RunError(
                f"{case.id}: не удалось определить поколение сборки ({why}). "
                f"Укажите builder в кейсе: {sorted(BUILDERS)}"
            )
    return adapters.resolve(builder)


def _check(
    exp: Expect,
    step_index: int,
    label: str,
    replies: list[str],
    buttons: list[str],
    media: list[Media] | None = None,
) -> list[Failure]:
    """Сверить ожидание с фактом. Пустой список — всё сошлось."""
    fails: list[Failure] = []
    joined = "\n".join(replies)
    media = media or []

    if exp.silent:
        if replies or media:
            # Медиа — тоже ответ: бот, приславший фото, не «промолчал».
            got = replies[0][:120] if replies else "; ".join(m.kind for m in media)
            fails.append(
                Failure(
                    step_index,
                    label,
                    "not_silent",
                    "бот промолчал",
                    f"бот ответил: {got!r}",
                    "уберите silent, если ответ ожидается",
                )
            )
        return fails

    if exp.text_equals is not None and joined.strip() != exp.text_equals.strip():
        fails.append(
            Failure(
                step_index,
                label,
                "text_mismatch",
                exp.text_equals,
                joined.strip()[:300],
                "сверьте текст с кодом бота (botkit-farm study)",
            )
        )
    for needle in exp.text_contains:
        if needle not in joined:
            fails.append(
                Failure(
                    step_index,
                    label,
                    "text_missing",
                    needle,
                    joined.strip()[:300] or "(бот промолчал)",
                    "текст должен быть в коде бота; сверьте инспектором",
                )
            )
    for needle in exp.buttons_contains:
        if needle not in buttons:
            fails.append(
                Failure(
                    step_index,
                    label,
                    "button_missing",
                    needle,
                    "; ".join(buttons[:20]) or "(нет кнопок)",
                    "подпись кнопки — та, что видит клиент",
                )
            )
    if exp.buttons_count is not None and len(buttons) != exp.buttons_count:
        fails.append(
            Failure(
                step_index,
                label,
                "button_count",
                f"ровно {exp.buttons_count}",
                f"{len(buttons)}: " + "; ".join(buttons[:20]),
                "счёт из кода или из ответа предыдущего шага",
            )
        )
    if exp.max_len is not None and len(joined) > exp.max_len:
        fails.append(
            Failure(
                step_index,
                label,
                "too_long",
                f"не длиннее {exp.max_len}",
                f"{len(joined)} символов",
                "возможен цикл печати в боте",
            )
        )
    # Медиа проверяется отдельно от текста: бот может прислать фото без подписи,
    # и text_contains по такому ответу всегда провалится — а это ожидаемо.
    if exp.media_kind is not None:
        kinds = [m.kind for m in media]
        if exp.media_kind not in kinds:
            fails.append(
                Failure(
                    step_index,
                    label,
                    "media_kind",
                    exp.media_kind,
                    "; ".join(kinds) or "(медиа нет)",
                    "сверьте метод отправки в коде бота (sendPhoto → photo)",
                )
            )
    if exp.media_count is not None and len(media) != exp.media_count:
        fails.append(
            Failure(
                step_index,
                label,
                "media_count",
                f"ровно {exp.media_count}",
                f"{len(media)}: " + "; ".join(m.kind for m in media),
                "число вложений в ответе",
            )
        )
    for needle in exp.caption_contains:
        captions = "\n".join(m.caption for m in media)
        if needle not in captions:
            fails.append(
                Failure(
                    step_index,
                    label,
                    "caption_missing",
                    needle,
                    "; ".join(m.caption for m in media if m.caption) or "(подписей нет)",
                    "подпись к файлу — та, что видит клиент",
                )
            )
    return fails


def run_case(
    case: Case,
    *,
    bot_dir: pathlib.Path,
    cache_dir: pathlib.Path,
    work_dir: pathlib.Path,
    default_gap: float = DEFAULT_GAP,
) -> CaseResult:
    """Прогнать один кейс целиком.

    Исключение наружу не выходит: любой сбой становится результатом со статусом
    errored и текстом причины. «Кейс упал» без причины невозможно разобрать, а
    разбор — работа, ради которой ферма существует.
    """
    started = time.monotonic()
    # Абсолютный путь обязателен: бот делает os.chdir(bot_dir) при сборке, и любой
    # относительный путь после этого указывает уже в другую директорию.
    bot_dir = bot_dir.resolve()
    result = _начальный_результат(case, bot_dir)
    try:
        result.commit = _git_commit(bot_dir)
        security.assert_fake_token()
        ref = _adapter_for(case, bot_dir)
        result.builder = ref.spec

        e = envmod.ensure_env(bot_dir, cache_dir)
        fixture = fx.path_for(case.fixture) if case.seed and case.fixture else None

        steps = _to_steps(_plan(case), default_gap)
        port = _api_port(case.id)
        # БД начинается с нуля на каждый прогон: иначе второй запуск продолжил бы
        # диалог с середины, и кейс падал бы не из-за кода бота.
        #
        # work_dir приходит извне, а не выводится из bot_dir. Вывод был ошибкой:
        # при относительном bot_dir получался относительный work_dir, а бот
        # делает os.chdir в свой каталог — и sqlite искал БД уже относительно
        # bot_dir, то есть в несуществующем bots/<имя>/.work/. Явный параметр
        # убирает весь класс таких ошибок, а не одну.
        work_dir.mkdir(parents=True, exist_ok=True)
        db_path = (work_dir / f"{case.id}.db").resolve()
        db_path.unlink(missing_ok=True)

        # Актёры и чат для мультипользовательских кейсов (ТЗ C3)
        actors_data = {
            role: {"user_id": a.user_id, "name": a.name, "is_bot": a.is_bot}
            for role, a in case.actors.items()
        }
        chat_data = None
        if case.chat:
            chat_data = {
                "type": case.chat.type,
                "title": case.chat.title,
                "thread_id": case.chat.thread_id,
                "bot_username": case.chat.bot_username,
            }

        execr: ExecResult = run_bot(
            farm_src=_farm_src(),
            bot_python=e.python,
            bot_dir=bot_dir,
            adapter_module=ref.module,
            adapter_attr=ref.attr,
            raw=case.raw,
            mode=case.mode,
            port=port,
            webhook_port=_webhook_port(case.id),
            fixture=fixture,
            steps=steps,
            db_path=db_path,
            actors=actors_data,  # type: ignore[arg-type]
            chat=chat_data,  # type: ignore[arg-type]
            # Каталог с venv бота: он лежит рядом с репозиторием, а не внутри
            # него, и без него песочница не увидит интерпретатор.
            env_root=cache_dir,
        )
        result.quarantine = list(execr.violations)
        if execr.error:
            result.error = execr.error
            result.status = Status.ERRORED
            return result

        status = _consume(execr.turns, case, result)
        result.status = status
    except (RunError, security.SecurityError) as exc:
        result.status = Status.ERRORED
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001
        result.status = Status.ERRORED
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.duration_s = time.monotonic() - started
    return result


@dataclass(frozen=True)
class _Planned:
    """Шаг после разворачивания повторов и подстановки шаблонов.

    `repeat` превращает один шаг кейса в несколько планируемых: у каждого свой
    индекс `i` из `n`. Дальше и хост, и сверка работают с этим плоским списком —
    иначе `case.steps` перестал бы соответствовать числу транскрипций, и ожидание
    сверялось бы не с тем шагом.
    """

    label: str
    kind: str  # msg | cb | pick | choice
    value: str = ""
    options: tuple[dict[str, str], ...] = ()
    expect: Expect | None = None
    as_: str | None = None
    pause: float | None = None
    optional: bool = False
    index: int = 0
    total: int = 1


def _действие(send: str | None, click: str | None, pick: str | None) -> tuple[str, str]:
    """Вид действия и его значение. Порядок совпадает с приоритетом в модели."""
    if click is not None:
        return "cb", click
    if pick is not None:
        return "pick", pick
    return "msg", str(send)


def _подпись(kind: str, value: str) -> str:
    """Имя действия по его виду и (уже отрендеренному) значению."""
    if kind == "cb":
        return f"нажать «{value}»"
    if kind == "pick":
        return f"нажать кнопку {value}"
    head = value.splitlines()[0][:40]
    return f"написать «{head}»"


def _plan(case: Case) -> list[_Planned]:
    """Кейс → плоский план шагов: развернуть `repeat`, отрендерить действия.

    Действия рендерятся здесь, до запуска бота: им доступен только индекс
    повтора (`i`, `n`), потому что ответов бота на этот момент ещё нет. Ожидания
    рендерятся позже, в `_consume`, где прошлые ответы уже известны.
    """
    plan: list[_Planned] = []
    for n, st in enumerate(case.steps, start=1):
        count = st.repeat or 1
        for i in range(count):
            ctx = {"i": i, "n": count}
            where = f"{case.id}: шаг {n}"
            if st.choice:
                options: list[dict[str, str]] = []
                for b in st.choice:
                    kind, value = _действие(b.send, b.click, b.pick)
                    options.append({"kind": kind, "value": tmpl.render(value, ctx, where)})
                label = f"выбрать доступное действие ({_подпись(options[0]['kind'], options[0]['value'])})"
                item = _Planned(
                    label=label,
                    kind="choice",
                    options=tuple(options),
                    expect=st.expect,
                    as_=st.as_,
                    pause=st.pause,
                    optional=st.optional,
                    index=i,
                    total=count,
                )
            else:
                kind, value = _действие(st.send, st.click, st.pick)
                rendered = tmpl.render(value, ctx, where)
                item = _Planned(
                    label=_подпись(kind, rendered),
                    kind=kind,
                    value=rendered,
                    expect=st.expect,
                    as_=st.as_,
                    pause=st.pause,
                    optional=st.optional,
                    index=i,
                    total=count,
                )
            if count > 1:
                item = replace(item, label=f"{item.label} [{i + 1}/{count}]")
            plan.append(item)
    return plan


def _to_steps(plan: list[_Planned], default_gap: float) -> list[dict[str, Any]]:
    """План → список шагов для подпроцесса."""
    out: list[dict[str, Any]] = []
    for p in plan:
        step: dict[str, Any] = {"kind": p.kind, "label": p.label}
        if p.kind == "choice":
            step["options"] = list(p.options)
        else:
            step["value"] = p.value
        # Роль исполнителя шага (ТЗ C3)
        if p.as_:
            step["as"] = p.as_
        if p.pause is not None:
            step["pause"] = p.pause
        else:
            step["gap"] = default_gap
        out.append(step)
    return out


def _render_expect(exp: Expect, ctx: dict[str, object], where: str) -> Expect:
    """Отрендерить шаблонные строки ожидания, не трогая остальные поля.

    Только строки-шаблоны: `silent`, счётчики и медиа-поля передаются как есть.
    """
    return exp.model_copy(
        update={
            "text_equals": (
                tmpl.render(exp.text_equals, ctx, where) if exp.text_equals is not None else None
            ),
            "text_contains": [tmpl.render(t, ctx, where) for t in exp.text_contains],
            "buttons_contains": [tmpl.render(t, ctx, where) for t in exp.buttons_contains],
            "caption_contains": [tmpl.render(t, ctx, where) for t in exp.caption_contains],
        }
    )


def _consume(turns: list[HostTurn], case: Case, result: CaseResult) -> Status:
    """Разобрать транскрипцию: собрать реплики, сверить ожидания, вынести вердикт."""
    status = Status.PASSED
    plan = _plan(case)
    # Прошлые ответы бота — контекст `turns` для шаблонов ожиданий.
    replies_seen: list[str] = []
    idx = 0
    for turn in turns:
        if isinstance(turn, CrashTurn):
            result.status = Status.ERRORED
            result.error = f"бот упал: {turn.error}"
            return Status.ERRORED

        if isinstance(turn, NoButtonTurn):
            result.failures.append(
                Failure(
                    idx,
                    f"нажать «{turn.label}»",
                    "no_such_button",
                    f"кнопка «{turn.label}»",
                    "бот предлагал: " + "; ".join(turn.known),
                    "подпись сверьте с инспектором или с ответом бота",
                )
            )
            result.status = Status.FAILED
            planned = plan[min(idx, len(plan) - 1)]
            if not planned.optional:
                return Status.FAILED
            idx += 1
            continue

        # Союз исчерпан: остаётся только StepTurn (crash и no_button обработаны
        # выше), поэтому ветка разбора ответа безусловна.

        # Кнопки и ответы уже отфильтрованы хостом; текст клиента добавляем сами.
        result.turns.append(Turn(text=turn.text, out=True))
        replies = turn.replies
        toasts = turn.toasts
        buttons = [b for b in turn.buttons if b]
        # Всплывающие уведомления попадают в кадр отдельной репликой и помечаются:
        # в Telegram это отдельный элемент интерфейса, и стирать разницу нельзя.
        for toast in toasts:
            result.turns.append(Turn(text=str(toast), out=False, is_toast=True))
        # Сообщение рисуем из messages, а не из replies: в replies входит и тост,
        # и последним он оказывался бы продублированным. В Telegram сообщение
        # остаётся в ленте, а всплывающее — нет: разница видима.
        #
        # Если поля messages хост не прислал вовсе (ранняя версия), берём replies
        # за вычетом тостов. Молча рисовать пустоту нельзя: кейс прошёл бы, а кадр
        # остался бы без ответа бота.
        if "messages" in turn.model_fields_set:
            messages = list(turn.messages)
        else:
            messages = [r for r in replies if r not in toasts]
        media = list(turn.media)
        if messages:
            result.turns.append(Turn(text=messages[-1], out=False, buttons=buttons, media=media))
        elif media:
            # Бот ответил только медиа, без текста: пузырь всё равно нужен, иначе
            # кадр покажет «бот молчал», а кейс с media_* не увидит ни одного
            # вложения. Текст не подставляем — подписи нарисует само медиа,
            # иначе одна и та же подпись попала бы в кадр дважды.
            result.turns.append(Turn(text="", out=False, buttons=buttons, media=media))

        planned = plan[min(idx, len(plan) - 1)]
        result.steps_done = idx + 1
        # Ожидание рендерим здесь, а не при загрузке: только теперь известны
        # прошлые ответы (`turns`). Индекс повтора берём из плана. Лишние
        # транскрипции сверх плана не проверяем: сверять их не с чем.
        if planned.expect is not None and idx < len(plan):
            ctx = {"i": planned.index, "n": planned.total, "turns": list(replies_seen)}
            expect = _render_expect(planned.expect, ctx, planned.label)
            fails = _check(expect, idx, planned.label, replies, buttons, media)
            if fails:
                result.failures.extend(fails)
                if not planned.optional:
                    result.status = Status.FAILED
                    return Status.FAILED
        replies_seen.extend(messages)
        idx += 1
    return status


def _начальный_результат(case: Case, bot_dir: pathlib.Path) -> CaseResult:
    """Результат до того, как прогон начался.

    Статус здесь — `errored`, а не `passed`: прогон, который не дошёл до конца,
    не должен выглядеть удачным ни в одном промежуточном состоянии. Пока
    `_consume` не вынес вердикт, отчёт по такому результату честно говорит:
    «не проверено».
    """
    return CaseResult(
        case_id=case.id,
        title=case.title,
        status=Status.ERRORED,
        # Длина плоского плана, а не `case.steps`: `repeat` разворачивает один
        # шаг в несколько, и отчёт обязан считать по факту прогона.
        steps_total=len(_plan(case)),
        repo=str(bot_dir),
    )


def _farm_src() -> pathlib.Path:
    """Каталог с пакетом фермы (`src/`) — его подпроцесс импортирует оттуда.

    Именно `src`, а не корень проекта: ферма ставится editable, но в venv бота
    её нет, и путь к пакету надо передать явно.
    """
    return pathlib.Path(__file__).resolve().parents[2]


def _api_port(key: str) -> int:
    """Свой порт на бота: два параллельных прогона не должны драться за порт."""
    return 8100 + (sum(key.encode()) % 500)


def _webhook_port(key: str) -> int:
    """Порт webhook-сервера бота — отдельный диапазон от подделки API.

    Диапазоны _api_port (8100–8599) и _webhook_port (8600–9099) не пересекаются,
    поэтому у одного прогона порт бота и порт подделки API всегда различны.
    """
    return 8600 + (sum(key.encode()) % 500)


def _git_commit(root: pathlib.Path) -> str:
    """Короткий коммит бота, чтобы отчёт воспроизводился."""
    import subprocess

    try:
        out = subprocess.run(  # noqa: S603 — аргументы фиксированы, ввод не участвует
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip()[:12] if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


__all__ = ["RunError", "run_case"]
