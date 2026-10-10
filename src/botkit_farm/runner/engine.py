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
from typing import Any

from botkit_farm import env as envmod
from botkit_farm import fixtures as fx
from botkit_farm import security
from botkit_farm.case.model import Case, Expect
from botkit_farm.inspector.repo import detect_builder
from botkit_farm.runner.host import ExecResult, run_bot
from botkit_farm.runner.result import CaseResult, Failure, Status, Turn

BUILDERS = {"legacy", "newgen", "reminder"}
# Пауза между шагами по умолчанию: боты тротттлинг, и «бот промолчал» на быстрых
# сообщениях — артефакт стенда, а не дефект бота. В кейсе переопределяется
# через `pause`.
DEFAULT_GAP = 2.1


class RunError(Exception):
    """Прогон невозможен: нет Redis, не собралось окружение, нет адаптера."""


def _adapter_for(case: Case, bot_dir: pathlib.Path) -> str:
    """Выбрать адаптер сборки: из кейса или по структуре репозитория.

    Три поколения отличаются не стилем, а точкой входа, и ошибка здесь стоит
    дорого: не тот адаптер даст ImportError, который выглядит как баг бота.
    """
    builder: str | None = case.builder
    if builder is None:
        builder, why = detect_builder(bot_dir)
        if builder is None:
            raise RunError(
                f"{case.id}: не удалось определить поколение сборки ({why}). "
                f"Укажите builder в кейсе: {sorted(BUILDERS)}"
            )
    if builder not in BUILDERS:
        raise RunError(f"{case.id}: неизвестный builder {builder!r}")
    return builder


def _check(
    exp: Expect, step_index: int, label: str, replies: list[str], buttons: list[str]
) -> list[Failure]:
    """Сверить ожидание с фактом. Пустой список — всё сошлось."""
    fails: list[Failure] = []
    joined = "\n".join(replies)

    if exp.silent:
        if replies:
            fails.append(
                Failure(
                    step_index,
                    label,
                    "not_silent",
                    "бот промолчал",
                    f"бот ответил: {replies[0][:120]!r}",
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
    result = CaseResult(
        case_id=case.id,
        title=case.title,
        status=Status.ERRORED,
        steps_total=len(case.steps),
        repo=str(bot_dir),
    )
    try:
        result.commit = _git_commit(bot_dir)
        security.assert_fake_token()
        adapter = _adapter_for(case, bot_dir)
        result.builder = adapter

        e = envmod.ensure_env(bot_dir, cache_dir)
        fixture = fx.path_for(case.fixture) if case.seed and case.fixture else None

        steps = _to_steps(case, default_gap)
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

        execr: ExecResult = run_bot(
            farm_src=_farm_src(),
            bot_python=e.python,
            bot_dir=bot_dir,
            adapter=adapter,
            port=port,
            fixture=fixture,
            steps=steps,
            db_path=db_path,
        )
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


def _to_steps(case: Case, default_gap: float) -> list[dict[str, Any]]:
    """Кейс → список шагов для подпроцесса."""
    out: list[dict[str, Any]] = []
    for st in case.steps:
        if st.click is not None:
            out.append({"kind": "cb", "value": st.click, "label": st.label()})
        elif st.pick is not None:
            out.append({"kind": "pick", "value": st.pick, "label": st.label()})
        else:
            out.append({"kind": "msg", "value": str(st.send), "label": st.label()})
        if st.pause is not None:
            out[-1]["pause"] = st.pause
        else:
            out[-1]["gap"] = default_gap
    return out


def _consume(turns: list[dict[str, Any]], case: Case, result: CaseResult) -> Status:
    """Разобрать транскрипцию: собрать реплики, сверить ожидания, вынести вердикт."""
    status = Status.PASSED
    idx = 0
    for turn in turns:
        kind = turn.get("kind")

        if kind == "crash":
            result.status = Status.ERRORED
            result.error = f"бот упал: {turn.get('error')}"
            return Status.ERRORED

        if kind == "no_button":
            result.failures.append(
                Failure(
                    idx,
                    f"нажать «{turn['label']}»",
                    "no_such_button",
                    f"кнопка «{turn['label']}»",
                    "бот предлагал: " + "; ".join(turn.get("known", [])),
                    "подпись сверьте с инспектором или с ответом бота",
                )
            )
            result.status = Status.FAILED
            step = case.steps[min(idx, len(case.steps) - 1)]
            if not step.optional:
                return Status.FAILED
            idx += 1
            continue

        if kind != "step":
            continue

        # Кнопки и ответы уже отфильтрованы хостом; текст клиента добавляем сами.
        result.turns.append(Turn(text=turn.get("text", ""), out=True))
        replies = turn.get("replies") or []
        toasts = turn.get("toasts") or []
        buttons = [b for b in (turn.get("buttons") or []) if b]
        # Всплывающие уведомления попадают в кадр отдельной репликой и помечаются:
        # в Telegram это отдельный элемент интерфейса, и стирать разницу нельзя.
        for toast in toasts:
            result.turns.append(Turn(text=str(toast), out=False, is_toast=True))
        # Сообщение рисуем из messages, а не из replies: в replies входит и тост,
        # и последним он оказывался бы продублированным. В Telegram сообщение
        # остаётся в ленте, а всплывающее — нет: разница видима.
        #
        # Если поля messages нет вовсе (другой хост, старая версия), берём replies
        # за вычетом тостов. Молча рисовать пустоту нельзя: кейс прошёл бы, а
        # кадр остался бы без ответа бота.
        if "messages" in turn:
            messages = list(turn["messages"])
        else:
            messages = [r for r in replies if r not in toasts]
        if messages:
            result.turns.append(Turn(text=messages[-1], out=False, buttons=buttons))

        step = case.steps[min(idx, len(case.steps) - 1)]
        result.steps_done = idx + 1
        if step.expect and idx < len(case.steps):
            fails = _check(step.expect, idx, step.label(), replies, buttons)
            if fails:
                result.failures.extend(fails)
                if not step.optional:
                    result.status = Status.FAILED
                    return Status.FAILED
        idx += 1
    return status


def _farm_src() -> pathlib.Path:
    """Каталог с пакетом фермы (`src/`) — его подпроцесс импортирует оттуда.

    Именно `src`, а не корень проекта: ферма ставится editable, но в venv бота
    её нет, и путь к пакету надо передать явно.
    """
    return pathlib.Path(__file__).resolve().parents[2]


def _api_port(key: str) -> int:
    """Свой порт на бота: два параллельных прогона не должны драться за порт."""
    return 8100 + (sum(key.encode()) % 500)


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
