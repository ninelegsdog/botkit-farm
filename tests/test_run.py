"""Прогон целиком: run_case и его помощники.

До этих тестов `run_case` — главная точка входа фермы — не проверялась ничем:
покрытие проходило мимо неё, потому что полный прогон требует бота, Redis и
клона. Мутационное тестирование это показало честнее любого отчёта о
покрытии: 105 выживших мутантов живут именно здесь.

Проверки точные — по значениям, а не «что-то не пустое». Иначе мутация
`8100 → 8101` или `"HEAD" → "HEAD "` осталась бы незамеченной: порт всё ещё
«какой-то номер», а git всё ещё «что-то вернул».
"""

from __future__ import annotations

import pathlib
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from botkit_farm import security
from botkit_farm.adapters import registry as adapters
from botkit_farm.adapters.registry import AdapterRef
from botkit_farm.case.model import Case
from botkit_farm.runner import engine
from botkit_farm.runner.contract import StepTurn, parse_turns
from botkit_farm.runner.host import ExecResult
from botkit_farm.runner.result import Status

# ── _api_port: свой порт на бота ─────────────────────────────────────────────


def test_порт_считается_по_формуле_а_не_приблизительно() -> None:
    """Точное число: сдвиг на единицу или замена `%` на `/` не должны проходить."""
    for key in ("x", "booking", "zzz"):
        assert engine._api_port(key) == 8100 + sum(key.encode()) % 500


def test_порт_всегда_целый() -> None:
    assert isinstance(engine._api_port("booking"), int)


def test_ключи_у_края_диапазона_дают_разные_порты() -> None:
    # Сумма 499 по модулю 500: `% 501` дал бы другой остаток — ловушка на границе.
    ключи = [chr(97 + i) * n for n in range(1, 12) for i in range(3)]
    порты = {engine._api_port(k) for k in ключи}

    assert len(порты) > 1


def test_порт_детерминирован_для_одного_и_того_же_кейса() -> None:
    assert engine._api_port("booking") == engine._api_port("booking")


def test_порт_лежит_в_своём_диапазоне() -> None:
    for key in ("a", "booking", "длинный-идентификатор-кейса", "x" * 64):
        assert 8100 <= engine._api_port(key) <= 8599


def test_разные_кейсы_получают_разные_порты() -> None:
    """Два параллельных прогона не должны драться за один порт."""
    ports = {engine._api_port(f"case-{i}") for i in range(50)}

    assert len(ports) > 1


def test_порт_не_зависит_от_длины_имени() -> None:
    """Диапазон шириной 500: длинные имена не должны уезжать за его край."""
    assert engine._api_port("x") <= engine._api_port("x" * 10) < 8600


# ── _webhook_port: порт webhook-сервера бота ────────────────────────────────


def test_порт_webhook_считается_по_формуле() -> None:
    """Точное число: сдвиг базы на единицу не должен проходить."""
    for key in ("x", "booking", "membership"):
        assert engine._webhook_port(key) == 8600 + sum(key.encode()) % 500


def test_порт_webhook_лежит_в_своём_диапазоне() -> None:
    for key in ("a", "booking", "длинный-идентификатор-кейса", "x" * 64):
        assert 8600 <= engine._webhook_port(key) <= 9099


def test_порт_webhook_не_совпадает_с_портом_подделки_api() -> None:
    """Сервер бота и подделка API — разные порты: в одном процессе не ужиться."""
    for key in ("a", "booking", "support", "x" * 64):
        assert engine._webhook_port(key) != engine._api_port(key)
        assert engine._webhook_port(key) > engine._api_port(key)


def test_порт_webhook_детерминирован() -> None:
    assert engine._webhook_port("booking") == engine._webhook_port("booking")


# ── _farm_src: откуда подпроцесс импортирует ферму ──────────────────────────


def test_каталог_исходников_указывает_на_пакет_фермы() -> None:
    src = engine._farm_src()

    assert (src / "botkit_farm" / "__init__.py").exists()
    assert (src / "botkit_farm" / "runner" / "engine.py").exists()


def test_каталог_исходников_это_родитель_родителей() -> None:
    # src/botkit_farm/runner/engine.py → src
    assert engine._farm_src() == pathlib.Path(engine.__file__).resolve().parents[2]


# ── _to_steps: кейс → протокол хоста ─────────────────────────────────────────


def test_сообщение_даёт_вид_msg() -> None:
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [{"send": "/start", "expect": {"silent": True}}],
        }
    )

    assert engine._to_steps(case, 2.5) == [
        {"kind": "msg", "value": "/start", "label": "написать «/start»", "gap": 2.5}
    ]


def test_нажатие_даёт_вид_cb_и_подпись_из_кнопки() -> None:
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [{"click": "Купить", "expect": {"silent": True}}],
        }
    )

    assert engine._to_steps(case, 2.5) == [
        {"kind": "cb", "value": "Купить", "label": "нажать «Купить»", "gap": 2.5}
    ]


def test_выбор_из_списка_даёт_вид_pick() -> None:
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [{"pick": "first", "expect": {"silent": True}}],
        }
    )

    assert engine._to_steps(case, 0.0) == [
        {"kind": "pick", "value": "first", "label": "нажать кнопку first", "gap": 0.0}
    ]


def test_заметка_шага_не_подменяет_его_подпись() -> None:
    """Подпись строит сам шаг (что делает бот); заметка — пояснение для человека."""
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [{"send": "Привет", "note": "первый шаг", "expect": {"silent": True}}],
        }
    )

    assert engine._to_steps(case, 1.0)[0]["label"] == "написать «Привет»"


def test_пауза_шага_заменяет_общий_интервал() -> None:
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [
                {"send": "/start", "expect": {"silent": True}},
                {"send": "/help", "pause": 5.0, "expect": {"silent": True}},
            ],
        }
    )
    steps = engine._to_steps(case, 2.5)

    assert steps[0] == {"kind": "msg", "value": "/start", "label": "написать «/start»", "gap": 2.5}
    assert steps[1] == {
        "kind": "msg",
        "value": "/help",
        "label": "написать «/help»",
        "pause": 5.0,
    }


def test_пауза_ноль_это_ещё_пауза() -> None:
    """`pause: 0` — осознанное «не ждать», а не «взять общий интервал»."""
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [{"send": "/start", "pause": 0.0, "expect": {"silent": True}}],
        }
    )

    assert "pause" in engine._to_steps(case, 2.5)[0]


def test_все_шаги_кейса_доходят_до_хоста() -> None:
    case = Case.model_validate(
        {
            "id": "case-id",
            "title": "Тестовый кейс",
            "source": ".",
            "steps": [
                {"send": "/start", "expect": {"silent": True}},
                {"click": "Далее", "expect": {"silent": True}},
                {"pick": "last", "expect": {"silent": True}},
            ],
        }
    )
    steps = engine._to_steps(case, 1.0)

    assert [s["kind"] for s in steps] == ["msg", "cb", "pick"]


# ── _git_commit: воспроизводимость прогона ───────────────────────────────────


def _подменить_git(monkeypatch, *, stdout: str = "", returncode: int = 0, side: Any = None):
    """Подмена subprocess.run с записью вызванной команды."""
    seen: dict[str, Any] = {}

    def fake_run(cmd, **kw):  # noqa: ANN001, ANN202
        seen["cmd"] = cmd
        seen["kw"] = kw
        if side is not None:
            raise side
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_коммит_берётся_короткой_хеш_записи(monkeypatch, tmp_path) -> None:
    seen = _подменить_git(monkeypatch, stdout="1a2b3c4d5e6f\n")

    assert engine._git_commit(tmp_path) == "1a2b3c4d5e6f"
    assert seen["cmd"] == ["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"]
    # Проверка кода возврата обязательна: без неё `git clone` с ошибкой молчал бы.
    assert seen["kw"] == {"capture_output": True, "text": True, "timeout": 5, "check": False}


def test_пустой_вывод_git_даёт_пустую_строку(monkeypatch, tmp_path) -> None:
    _подменить_git(monkeypatch, stdout="\n")

    assert engine._git_commit(tmp_path) == ""


def test_ошибка_git_даёт_пустую_строку_и_прогон_идёт(monkeypatch, tmp_path) -> None:
    """Коммит — украшение отчёта; его отсутствие не должно ронять кейс."""
    _подменить_git(monkeypatch, returncode=128, stdout="fatal: not a repository")

    assert engine._git_commit(tmp_path) == ""


def test_длинный_хэш_обрезается_до_двенадцати(monkeypatch, tmp_path) -> None:
    _подменить_git(monkeypatch, stdout="a" * 40 + "\n")

    assert len(engine._git_commit(tmp_path)) == 12


def test_отсутствующий_git_не_роняет_прогон(monkeypatch, tmp_path) -> None:
    _подменить_git(monkeypatch, side=FileNotFoundError("git"))

    assert engine._git_commit(tmp_path) == ""


def test_зависший_git_не_роняет_прогон(monkeypatch, tmp_path) -> None:
    _подменить_git(monkeypatch, side=subprocess.TimeoutExpired(cmd="git", timeout=5))

    assert engine._git_commit(tmp_path) == ""


def test_настоящий_репозиторий_даёт_коммит() -> None:
    """Без подмен: проверка, что команда написана правильно, а не заголовком."""
    корень = pathlib.Path(__file__).resolve().parents[1]

    коммит = engine._git_commit(корень)

    assert коммит != ""
    assert len(коммит) <= 12
    assert all(c in "0123456789abcdef" for c in коммит)


# ── run_case ────────────────────────────────────────────────────────────────


class _Окружение:
    """Подменяет всё, до чего прогон не дойдёт без настоящего бота.

    Заглушки не просто возвращают заглушку, а запоминают аргументы: прогон,
    который передал в хост не тот каталог или не тот интерпретатор, обязан
    падать здесь, а не на чужом боте.
    """

    def __init__(
        self,
        tmp_path: pathlib.Path,
        monkeypatch,
        execr: ExecResult,
        *,
        real_adapter: bool = False,
    ) -> None:
        self.вызовы: dict[str, Any] = {}
        python = tmp_path / "envs" / "bin" / "python"

        def fake_assert_token() -> None:
            self.вызовы["токен"] = True

        def fake_adapter(case: Case, bot_dir: pathlib.Path) -> AdapterRef:
            self.вызовы["adapter_args"] = (case.id, bot_dir)
            return adapters.resolve("newgen")

        def fake_env(bot_dir: pathlib.Path, cache: pathlib.Path) -> Any:
            self.вызовы["env_args"] = (bot_dir, cache)
            return SimpleNamespace(python=python)

        def fake_commit(root: pathlib.Path) -> str:
            self.вызовы["commit_root"] = root
            return "abc1234"

        def fake_run_bot(**kw: Any) -> ExecResult:
            self.вызовы = {**self.вызовы, **kw}
            return execr

        monkeypatch.setattr(security, "assert_fake_token", fake_assert_token)
        if not real_adapter:
            monkeypatch.setattr(engine, "_adapter_for", fake_adapter)
        monkeypatch.setattr(engine.envmod, "ensure_env", fake_env)
        monkeypatch.setattr(engine, "_git_commit", fake_commit)
        monkeypatch.setattr(engine, "run_bot", fake_run_bot)


def test_результат_до_прогона_начинается_errored(tmp_path) -> None:
    """Прогон, который не начался, не должен выглядеть удачным."""
    bot = (tmp_path / "bot").resolve()

    начальный = engine._начальный_результат(_кейс(), bot)

    assert начальный.status is Status.ERRORED
    assert начальный.case_id == "booking"
    assert начальный.title == "Бронирование"
    assert начальный.steps_total == 1
    assert начальный.repo == str(bot)
    assert начальный.ok is False


def test_каждый_шаг_кейса_считается_в_результате(tmp_path) -> None:
    case = _кейс(
        {"send": "/start", "expect": {"silent": True}},
        {"click": "Далее", "expect": {"silent": True}},
    )

    assert engine._начальный_результат(case, tmp_path).steps_total == 2


def test_безопасность_проверяется_до_запуска_бота(tmp_path, monkeypatch) -> None:
    case = _кейс()
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))
    bot = (tmp_path / "bot").resolve()

    engine.run_case(case, bot_dir=bot, cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work")

    assert окружение.вызовы["токен"] is True


def test_каждый_вызов_получает_свои_аргументы(tmp_path, monkeypatch) -> None:
    """Подмена любого аргумента на None обязана ломать прогон."""
    case = _кейс()
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))
    bot = (tmp_path / "bot").resolve()
    cache = tmp_path / "envs"

    engine.run_case(case, bot_dir=bot, cache_dir=cache, work_dir=tmp_path / ".work")
    kw = окружение.вызовы

    assert kw["adapter_args"] == ("booking", bot)
    assert kw["adapter_module"] == "botkit_farm.adapters.newgen"
    assert kw["adapter_attr"] == "build"
    assert kw["raw"] is False
    assert kw["env_args"] == (bot, cache)
    assert kw["commit_root"] == bot
    assert kw["bot_dir"] == bot
    assert kw["bot_python"] == cache / "bin" / "python"
    assert kw["farm_src"] == engine._farm_src()


def _кейс(*steps: Any, **kw: Any) -> Case:
    payload: dict[str, Any] = {
        "id": "booking",
        "title": "Бронирование",
        "source": ".",
        "steps": list(steps) or [{"send": "/start", "expect": {"silent": True}}],
    }
    payload.update(kw)
    return Case.model_validate(payload)


def _ответ(текст: str, кнопки: list[str] | None = None, шаг: str = "шаг бота") -> StepTurn:
    """Ответ бота настоящим контрактом, а не словарём."""
    return parse_turns(
        [
            {
                "kind": "step",
                "text": шаг,
                "replies": [текст],
                "messages": [текст],
                "toasts": [],
                "buttons": кнопки or [],
            }
        ]
    )[0]


def test_успешный_прогон_даёт_вердикт_и_транскрипцию(tmp_path, monkeypatch) -> None:
    case = _кейс({"send": "/start", "expect": {"text_contains": ["Привет"]}})
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[_ответ("Привет", шаг="написать «/start»")]))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.status is Status.PASSED
    assert res.case_id == "booking"
    assert res.title == "Бронирование"
    assert res.steps_total == 1
    assert res.steps_done == 1
    assert [t.text for t in res.turns] == ["написать «/start»", "Привет"]
    assert res.error is None


def test_репозиторий_в_отчёте_абсолютный(tmp_path, monkeypatch) -> None:
    """Бот делает os.chdir в свой каталог, и относительный путь в отчёте врёт."""
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.repo == str((tmp_path / "bot").resolve())


def test_сборка_и_коммит_попадают_в_отчёт(tmp_path, monkeypatch) -> None:
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.builder == "newgen"
    assert res.commit == "abc1234"


def test_сторонний_адаптер_доходит_до_хоста_разобранным(tmp_path, monkeypatch) -> None:
    """Ссылка из кейса разбирается движком, а не склеивается с «адаптерс.» хоста.

    Модуль и фабрика идут в подпроцесс по отдельности: если движок перепутает
    `spec`, `module` или `attr`, сторонний адаптер не найдётся у бота.
    """
    case = _кейс(builder="botkit-farm.adapter:mypkg.plug:make")
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]), real_adapter=True)

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.builder == "botkit-farm.adapter:mypkg.plug:make"
    assert окружение.вызовы["adapter_module"] == "mypkg.plug"
    assert окружение.вызовы["adapter_attr"] == "make"


def test_сырой_режим_из_кейса_доходит_до_хоста(tmp_path, monkeypatch) -> None:
    case = _кейс(raw=True)
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert окружение.вызовы["raw"] is True


def test_режим_webhook_из_кейса_доходит_до_хоста(tmp_path, monkeypatch) -> None:
    """`mode: webhook` — и хост получает и режим, и порт webhook-сервера."""
    case = _кейс(mode="webhook")
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert окружение.вызовы["mode"] == "webhook"
    assert окружение.вызовы["webhook_port"] == engine._webhook_port("booking")


def test_режим_polling_по_умолчанию_доходит_до_хоста(tmp_path, monkeypatch) -> None:
    case = _кейс()
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert окружение.вызовы["mode"] == "polling"
    assert окружение.вызовы["webhook_port"] == engine._webhook_port("booking")


def test_база_обнуляется_перед_каждым_прогоном(tmp_path, monkeypatch) -> None:
    """Иначе второй запуск продолжил бы диалог с середины."""
    case = _кейс()
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))
    work = tmp_path / ".work"
    work.mkdir()
    (work / "booking.db").write_text("прошлый прогон", encoding="utf-8")

    engine.run_case(case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=work)

    db = окружение.вызовы["db_path"]
    assert db == (work / "booking.db").resolve()
    assert not db.exists()


def test_каталог_работы_создаётся_если_его_нет(tmp_path, monkeypatch) -> None:
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))
    work = tmp_path / "глубоко" / ".work"

    engine.run_case(case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=work)

    assert work.is_dir()


def test_в_хост_уходят_шаги_и_каталоги_прогона(tmp_path, monkeypatch) -> None:
    case = _кейс(
        {"send": "/start", "expect": {"silent": True}},
        {"click": "Далее", "expect": {"silent": True}},
    )
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case,
        bot_dir=tmp_path / "bot",
        cache_dir=tmp_path / "envs",
        work_dir=tmp_path / ".work",
        default_gap=1.5,
    )

    kw = окружение.вызовы
    assert [s["kind"] for s in kw["steps"]] == ["msg", "cb"]
    assert kw["steps"][0]["gap"] == 1.5
    assert kw["port"] == engine._api_port("booking")
    assert kw["adapter_module"] == "botkit_farm.adapters.newgen"
    assert kw["adapter_attr"] == "build"
    # Каталог venv идёт в песочницу: интерпретатор бота лежит не в репозитории.
    assert kw["env_root"] == tmp_path / "envs"


def test_фикстура_подключается_только_к_нужному_кейсу(tmp_path, monkeypatch) -> None:
    case = _кейс(seed=True, fixture="delivery")
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert окружение.вызовы["fixture"] is not None


def test_кейс_без_фикстуры_не_тянет_данные(tmp_path, monkeypatch) -> None:
    case = _кейс(fixture=None)
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert окружение.вызовы["fixture"] is None


def test_ошибка_хоста_даёт_errored_с_его_текстом(tmp_path, monkeypatch) -> None:
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[], error="бот не отработал"))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.status is Status.ERRORED
    assert res.error == "бот не отработал"


def test_неизвестный_сборщик_останавливает_прогон(tmp_path, monkeypatch) -> None:
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))
    monkeypatch.setattr(
        engine,
        "_adapter_for",
        lambda case, bot_dir: (_ for _ in ()).throw(engine.RunError("поколение не то")),
    )

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.status is Status.ERRORED
    assert res.error == "поколение не то"


def test_настоящий_токен_останавливает_прогон_до_запуска(tmp_path, monkeypatch) -> None:
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    def отказ() -> None:
        raise security.SecurityError("настоящий токен")

    monkeypatch.setattr(security, "assert_fake_token", отказ)

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.status is Status.ERRORED
    assert res.error == "настоящий токен"


def test_непредвиденная_ошибка_тоже_становится_результатом(tmp_path, monkeypatch) -> None:
    """Наружу исключение не выходит: «кейс упал» без причины не разобрать."""
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))
    monkeypatch.setattr(
        engine,
        "_adapter_for",
        lambda case, bot_dir: (_ for _ in ()).throw(ZeroDivisionError("деление на ноль")),
    )

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.status is Status.ERRORED
    assert res.error == "ZeroDivisionError: деление на ноль"


def test_длительность_измеряется_даже_при_ошибке(tmp_path, monkeypatch) -> None:
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[], error="упал"))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.duration_s >= 0.0


def test_длительность_это_прошедшее_время_а_не_сумма(tmp_path, monkeypatch) -> None:
    """Верхняя граница важна: `now + started` дал бы время с момента загрузки
    системы, и в отчёте кейс выглядел бы идущим сутки."""
    case = _кейс()
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.duration_s < 3600.0


def test_удовлетворённое_ожидание_не_оставляет_провалов(tmp_path, monkeypatch) -> None:
    case = _кейс({"send": "/start", "expect": {"text_contains": ["Привет"]}})
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[_ответ("Привет")]))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.failures == []
    assert res.ok is True


def test_несовпавшее_ожидание_даёт_провал_с_шагом(tmp_path, monkeypatch) -> None:
    case = _кейс({"send": "/start", "expect": {"text_contains": ["Привет"]}})
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[_ответ("Пока")]))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.status is Status.FAILED
    assert [f.kind for f in res.failures] == ["text_missing"]


def test_карантин_копируется_а_не_разделяется(tmp_path, monkeypatch) -> None:
    """Список нарушений принадлежит результату: общий бы копил чужие."""
    case = _кейс()
    нарушения = ["open → /home/x"]
    _Окружение(tmp_path, monkeypatch, ExecResult(turns=[], violations=нарушения))

    res = engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    assert res.quarantine == нарушения
    assert res.quarantine is not нарушения


@pytest.mark.parametrize("gap", [0.0, 1.0, 5.0])
def test_интервал_по_умолчанию_меняется_по_switch(tmp_path, monkeypatch, gap: float) -> None:
    case = _кейс({"send": "/start", "expect": {"silent": True}})
    окружение = _Окружение(tmp_path, monkeypatch, ExecResult(turns=[]))

    engine.run_case(
        case,
        bot_dir=tmp_path / "bot",
        cache_dir=tmp_path / "envs",
        work_dir=tmp_path / ".work",
        default_gap=gap,
    )

    assert окружение.вызовы["steps"][0]["gap"] == gap


# ── Актёры и чат (ТЗ C3): проверка, что они пробрасываются в run_bot ─────────────


def test_актеры_и_чат_пробрасываются_в_run_bot(tmp_path, monkeypatch) -> None:
    """Если в кейсе заданы actors/chat, они должны попасть в run_bot.

    Это регресс-тест: мутационное тестирование показало, что новый код не
    проверялся — мутации, удаляющие actors_data/chat_data, выживали.
    """
    from botkit_farm.case.model import Actor, Case, ChatSpec, Expect, Step
    from botkit_farm.runner.host import ExecResult

    calls: dict[str, Any] = {}

    def _fake_run_bot(*_a, **kw):
        calls.update(kw)
        return ExecResult(
            turns=[
                {
                    "kind": "step",
                    "text": "/start",
                    "replies": [],
                    "messages": [],
                    "toasts": [],
                    "media": [],
                    "buttons": [],
                }
            ]
        )

    monkeypatch.setattr(engine, "run_bot", _fake_run_bot)

    # Кейс с актерами и чатом
    case = Case(
        schema_version=1,
        id="multi-test",
        title="Multi-user test",
        source="./bots/botkit-bookingbot",
        builder="legacy",
        actors={
            "client": Actor(user_id=1001, name="Client"),
            "admin": Actor(user_id=424242, name="Admin", is_bot=False),
        },
        chat=ChatSpec(type="group", title="Test Group", thread_id=42, bot_username="testbot"),
        steps=[
            Step(send="/start", **{"as": "client"}, expect=Expect(silent=True)),
            Step(send="hello", **{"as": "admin"}, expect=Expect(silent=True)),
        ],
    )

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    # Проверить, что actors и chat переданы в run_bot
    assert "actors" in calls, "actors не передан в run_bot"
    assert "chat" in calls, "chat не передан в run_bot"

    actors = calls["actors"]
    assert actors is not None
    assert "client" in actors
    assert "admin" in actors
    assert actors["client"]["user_id"] == 1001
    assert actors["client"]["name"] == "Client"
    assert actors["admin"]["user_id"] == 424242
    assert actors["admin"]["name"] == "Admin"
    assert actors["admin"]["is_bot"] is False

    chat = calls["chat"]
    assert chat is not None
    assert chat["type"] == "group"
    assert chat["title"] == "Test Group"
    assert chat["thread_id"] == 42
    assert chat["bot_username"] == "testbot"


def test_без_актеров_actors_пустой_словарь(tmp_path, monkeypatch) -> None:
    """Если actors не заданы, передаётся пустой словарь (не None)."""
    from botkit_farm.case.model import Case, Expect, Step
    from botkit_farm.runner.host import ExecResult

    calls: dict[str, Any] = {}

    def _fake_run_bot(*_a, **kw):
        calls.update(kw)
        return ExecResult(
            turns=[
                {
                    "kind": "step",
                    "text": "/start",
                    "replies": [],
                    "messages": [],
                    "toasts": [],
                    "media": [],
                    "buttons": [],
                }
            ]
        )

    monkeypatch.setattr(engine, "run_bot", _fake_run_bot)

    case = Case(
        schema_version=1,
        id="single-test",
        title="Single user",
        source="./bots/botkit-bookingbot",
        builder="legacy",
        steps=[
            Step(send="/start", expect=Expect(silent=True)),
        ],
    )

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    actors = calls.get("actors")
    assert actors is not None
    assert actors == {}

    chat = calls.get("chat")
    assert chat is None


def test_шаги_содержат_as_когда_заданы_actors(tmp_path, monkeypatch) -> None:
    """Шаги в run_bot должны содержать поле 'as' при заданных actors."""
    from botkit_farm.case.model import Actor, Case, ChatSpec, Expect, Step
    from botkit_farm.runner.host import ExecResult

    calls: dict[str, Any] = {}

    def _fake_run_bot(*_a, **kw):
        calls.update(kw)
        return ExecResult(
            turns=[
                {
                    "kind": "step",
                    "text": "/start",
                    "replies": [],
                    "messages": [],
                    "toasts": [],
                    "media": [],
                    "buttons": [],
                }
            ]
        )

    monkeypatch.setattr(engine, "run_bot", _fake_run_bot)

    case = Case(
        schema_version=1,
        id="multi-test",
        title="Multi-user test",
        source="./bots/botkit-bookingbot",
        builder="legacy",
        actors={"client": Actor(user_id=1001)},
        chat=ChatSpec(type="group"),
        steps=[
            Step(send="/start", **{"as": "client"}, expect=Expect(silent=True)),
        ],
    )

    engine.run_case(
        case, bot_dir=tmp_path / "bot", cache_dir=tmp_path / "envs", work_dir=tmp_path / ".work"
    )

    steps = calls.get("steps")
    assert steps is not None
    assert len(steps) == 1
    assert steps[0].get("as") == "client"
