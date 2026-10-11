"""Тесты doctor: каждая неудача называет причину и команду.

Проверки в тесте — на точную рекомендацию, а не на «что-то напечаталось».
Диагностика без команды бесполезна ровно настолько, насколько бесполезен был
отчёт без неё: владелец всё равно не знает, что делать.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

from botkit_farm import cli, doctor
from botkit_farm.doctor import Check, Level, Report

# Токен нужного вида: цифры, двоеточие, длинный хвост. Именно такой настоящий
# токен и должен останавливать прогон.
REAL_TOKEN = "123456789:AAHqLZfakefakefakefakefakefake"  # noqa: S105 — фикстура, не секрет
FAKE_TOKEN = "424242:AAHfakefakefakefakefakefakefake"  # noqa: S105 — фейковый префикс фермы


def _closed_port() -> int:
    """Порт, который гарантированно никто не слушает.

    Берём свободный порт и сразу отпускаем: между закрытием и попыткой
    подключения его может занять кто-то ещё, но на практике это лучше, чем
    предполагать, что 6380 у всех свободна.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _farm(tmp_path: Path, *cases: dict[str, Any]) -> Path:
    """Каталог фермы с каталогами и указанными кейсами."""
    (tmp_path / "cases").mkdir(exist_ok=True)
    for name in ("bots", "envs", "reports"):
        (tmp_path / name).mkdir(exist_ok=True)
    for i, case in enumerate(cases):
        (tmp_path / "cases" / f"case-{i}.yaml").write_text(
            yaml.safe_dump(case, allow_unicode=True), encoding="utf-8"
        )
    return tmp_path


def _good_case(**kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "id": "demo",
        "title": "Демо",
        "source": ".",
        "steps": [{"send": "/start", "expect": {"silent": True}}],
    }
    payload.update(kw)
    return payload


# ── Redis: главный блокер ───────────────────────────────────────────────────


async def test_поваленный_redis_называет_команду_подъёма() -> None:
    """Г: с поваленным Redis doctor печатает точную команду, а не «ошибка»."""
    port = _closed_port()
    check = await doctor.check_redis(f"redis://127.0.0.1:{port}/0")

    assert check.name == "redis"
    assert check.ok is False
    assert check.level is Level.BLOCKER
    assert f"redis://127.0.0.1:{port}/0" in check.what
    assert check.fix == f"redis-server --port {port} --daemonize yes"


def test_команда_подъёма_совпадает_с_документированным_портом() -> None:
    """ТЗ, B4/Г: 6380 — порт по умолчанию, его и называем в подсказке."""
    assert doctor.redis_command() == "redis-server --port 6380 --daemonize yes"
    assert doctor.DEFAULT_REDIS_URL.endswith(":6380/0")


def test_порт_в_рекомендации_берётся_из_адреса_а_не_захардкожен() -> None:
    """Владелец поднимает стенд на своём порту — и команда должна быть его."""
    assert doctor.redis_command(6399) == "redis-server --port 6399 --daemonize yes"


# ── Токен ───────────────────────────────────────────────────────────────────


def test_настоящий_токен_останавливает_прогон() -> None:
    check = doctor.check_token({"TELEGRAM_BOT_TOKEN": REAL_TOKEN})

    assert check.ok is False
    assert check.level is Level.BLOCKER
    assert "TELEGRAM_BOT_TOKEN" in check.what
    assert "424242" in check.fix


def test_фейковый_токен_пропускается() -> None:
    check = doctor.check_token({"TELEGRAM_BOT_TOKEN": FAKE_TOKEN})

    assert check.ok is True
    assert check.level is Level.BLOCKER
    assert check.fix == ""


def test_токен_не_в_переменной_с_ботом_тоже_ловится() -> None:
    """Токен может лежать в любой переменной, а не только в именованной."""
    check = doctor.check_token({"ЧТО_ТО_НЕОЖИДАННОЕ": REAL_TOKEN})

    assert check.ok is False
    assert "ЧТО_ТО_НЕОЖИДАННОЕ" in check.what


# ── Каталоги ────────────────────────────────────────────────────────────────


def test_без_каталога_кейсов_прогон_не_имеет_смысла(tmp_path: Path) -> None:
    checks = {c.name: c for c in doctor.check_dirs(tmp_path)}
    cases = checks["dir:cases"]

    assert cases.ok is False
    assert cases.level is Level.BLOCKER
    assert cases.fix == "botkit-farm init <репозиторий-бота>"


def test_отсутствующий_вспомогательный_каталог_это_ухудшение(tmp_path: Path) -> None:
    """Нет reports/ — ферма создаст сама; вердикт кейса от этого не меняется."""
    (tmp_path / "cases").mkdir()
    checks = {c.name: c for c in doctor.check_dirs(tmp_path)}

    assert checks["dir:reports"].ok is False
    assert checks["dir:reports"].level is Level.DEGRADED
    assert checks["dir:reports"].fix == "mkdir -p reports"


def test_недоступный_для_записи_каталог_это_блокер(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "reports").mkdir()
    real_access = __import__("os").access

    def fake_access(path: Any, mode: int, **kw: Any) -> bool:
        return False if "reports" in str(path) else real_access(path, mode, **kw)

    monkeypatch.setattr("botkit_farm.doctor.os.access", fake_access)
    checks = {c.name: c for c in doctor.check_dirs(tmp_path)}

    assert checks["dir:reports"].level is Level.BLOCKER
    assert "chown" in checks["dir:reports"].fix


def test_каталоги_на_месте_проходят(tmp_path: Path) -> None:
    _farm(tmp_path)
    checks = doctor.check_dirs(tmp_path)

    assert all(c.ok for c in checks), [c.what for c in checks if not c.ok]


# ── Кейсы ───────────────────────────────────────────────────────────────────


def test_пустой_каталог_кейсов_это_блокер(tmp_path: Path) -> None:
    _farm(tmp_path)
    check = doctor.check_cases(tmp_path)

    assert check.ok is False
    assert check.level is Level.BLOCKER
    assert "нет кейсов" in check.what


def test_битый_кейс_останавливает_прогон_и_называет_файл(tmp_path: Path) -> None:
    _farm(tmp_path)
    (tmp_path / "cases" / "broken.yaml").write_text(
        yaml.safe_dump({"id": "x", "title": "y", "source": ".", "steps": "не список"}),
        encoding="utf-8",
    )
    check = doctor.check_cases(tmp_path)

    assert check.ok is False
    assert check.level is Level.BLOCKER
    assert "broken.yaml" in check.what
    assert "botkit-farm migrate --check" in check.fix


def test_кейс_без_версии_схемы_просит_миграцию(tmp_path: Path) -> None:
    """Файл без schema_version читается, но споткнётся о `migrate --check` в CI."""
    payload = _good_case()
    payload.pop("schema_version")
    _farm(tmp_path, payload)
    check = doctor.check_cases(tmp_path)

    assert check.ok is False
    assert check.level is Level.BLOCKER
    assert "case-0.yaml" in check.what
    assert check.fix == f"botkit-farm migrate {tmp_path / 'cases'}"


def test_годные_кейсы_проходят_проверку(tmp_path: Path) -> None:
    _farm(tmp_path, _good_case(), _good_case(id="second", title="Второй"))
    check = doctor.check_cases(tmp_path)

    assert check.ok is True
    assert check.level is Level.BLOCKER
    assert "кейсов 2" in check.what


# ── Изоляция ────────────────────────────────────────────────────────────────


def test_без_песочника_доктор_говорит_прямо() -> None:
    check = doctor.check_isolation(probe=lambda: None)

    assert check.ok is False
    assert check.level is Level.DEGRADED
    assert "с вашими правами" in check.what
    assert "bubblewrap" in check.fix
    assert "BOTKIT_FARM_SANDBOX" in check.fix


def test_выключенный_песочник_это_не_то_же_что_отсутствующий() -> None:
    """Три состояния, а не два: нет / есть, но выключен / есть и включён."""
    check = doctor.check_isolation(probe=lambda: "bubblewrap", is_enabled=lambda: False)

    assert check.ok is False
    assert "но выключен" in check.what
    assert check.fix == "BOTKIT_FARM_SANDBOX=1 botkit-farm run  # включить песочницу"


def test_включённый_песочник_идёт_как_прошлое_но_остаётся_ухудшением() -> None:
    check = doctor.check_isolation(probe=lambda: "bubblewrap", is_enabled=lambda: True)

    assert check.ok is True
    assert check.level is Level.DEGRADED
    assert "включена через bubblewrap" in check.what


# ── Кадры ───────────────────────────────────────────────────────────────────


def test_нет_подложки_и_браузера_это_ухудшение(tmp_path: Path) -> None:
    check = doctor.check_frame_support(tmp_path, renderer=lambda: [])

    assert check.ok is False
    assert check.level is Level.DEGRADED
    assert "проверки кейса отработают" in check.why
    assert "assets" in check.fix


def test_подложка_есть_но_chromium_не_запускается(tmp_path: Path, monkeypatch) -> None:
    class Boom:
        def __enter__(self) -> Any:
            raise RuntimeError("нет chromium")

        def __exit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: Boom())
    check = doctor.check_frame_support(tmp_path, renderer=lambda: ["tg-bg.png"])

    assert check.ok is False
    assert check.level is Level.DEGRADED
    assert check.fix == "python -m playwright install chromium"


# ── Отчёт ───────────────────────────────────────────────────────────────────


def _report(*checks: Check) -> Report:
    return Report(checks=tuple(checks))


def test_блокеры_и_ухудшения_считаются_по_уровню() -> None:
    ok = Check("ok", True, Level.BLOCKER, "в порядке", "причина", "")
    block = Check("redis", False, Level.BLOCKER, "лежит", "важно", "поднять")
    worse = Check("frame", False, Level.DEGRADED, "нет кадра", "важно", "поставить")
    report = _report(ok, block, worse)

    assert report.ok is False
    assert [c.name for c in report.blockers] == ["redis"]
    assert [c.name for c in report.degraded] == ["frame"]


def test_одни_ухудшения_прогон_не_блокируют() -> None:
    worse = Check("frame", False, Level.DEGRADED, "нет кадра", "важно", "поставить")
    report = _report(worse)

    assert report.ok is True
    assert report.blockers == ()


def test_отчёт_печатает_команду_под_каждой_неудачей() -> None:
    """ПД: уберите команду из Check.fix — этот тест упадёт."""
    block = Check("redis", False, Level.BLOCKER, "лежит", "важно", "redis-server --port 6380")
    text = doctor.format_report(_report(block))

    assert "команда: redis-server --port 6380" in text
    assert "почему: важно" in text
    assert "Блокеры прежде" in text


def test_отчёт_разделяет_блокеры_и_ухудшения() -> None:
    ok = Check("token", True, Level.BLOCKER, "чисто", "причина", "")
    block = Check("redis", False, Level.BLOCKER, "лежит", "важно", "поднять")
    worse = Check("frame", False, Level.DEGRADED, "нет кадра", "важно", "поставить")
    text = doctor.format_report(_report(ok, block, worse))

    assert "Блокеры — без этого прогон не запустится (1)" in text
    assert "Ухудшения — проверки пройдут, результат будет беднее (1)" in text
    assert text.index("Блокеры") < text.index("Ухудшения")
    assert "1 из 3 проверок прошли" in text


def test_пустая_секция_говорит_что_пустой_она_не_из_за_ошибки() -> None:
    ok = Check("token", True, Level.BLOCKER, "чисто", "причина", "")
    text = doctor.format_report(_report(ok))

    assert "(нет)" in text
    assert "Готов к работе" in text


# ── Сборка и CLI ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_сборка_отчёта_считает_блокеры(tmp_path: Path, monkeypatch) -> None:
    _farm(tmp_path, _good_case())

    async def fake_redis(url: str = "") -> Check:
        return Check("redis", False, Level.BLOCKER, "лежит", "важно", "поднять")

    monkeypatch.setattr(doctor, "check_redis", fake_redis)
    monkeypatch.setattr(
        doctor,
        "check_isolation",
        lambda: Check("isolation", False, Level.DEGRADED, "нет песочника", "важно", "поставить"),
    )
    monkeypatch.setattr(
        doctor,
        "check_frame_support",
        lambda root: Check("frame", True, Level.DEGRADED, "кадр есть", "причина", ""),
    )

    report = await doctor.run_checks(tmp_path, env={})

    assert [c.name for c in report.blockers] == ["redis"]
    assert [c.name for c in report.degraded] == ["isolation"]
    assert [c.name for c in report.checks] == [
        "token",
        "redis",
        "cases",
        "dir:cases",
        "dir:bots",
        "dir:envs",
        "dir:reports",
        "frame",
        "isolation",
    ]


@pytest.mark.asyncio
async def test_сборка_передаёт_адрес_redis_и_окружение(tmp_path: Path, monkeypatch) -> None:
    _farm(tmp_path, _good_case())
    seen: dict[str, Any] = {}

    async def fake_redis(url: str = "") -> Check:
        seen["redis_url"] = url
        return Check("redis", True, Level.BLOCKER, "отвечает", "причина", "")

    monkeypatch.setattr(doctor, "check_redis", fake_redis)
    monkeypatch.setattr(
        doctor,
        "check_isolation",
        lambda: Check("isolation", True, Level.DEGRADED, "есть", "причина", ""),
    )
    monkeypatch.setattr(
        doctor,
        "check_frame_support",
        lambda root: Check("frame", True, Level.DEGRADED, "кадр есть", "причина", ""),
    )

    report = await doctor.run_checks(
        tmp_path, env={"TELEGRAM_BOT_TOKEN": FAKE_TOKEN}, redis_url="redis://127.0.0.1:6399/0"
    )

    assert seen["redis_url"] == "redis://127.0.0.1:6399/0"
    assert report.ok is True


def test_cli_печатает_отчёт_и_возвращает_код_блокера(monkeypatch, capsys) -> None:
    # Синхронный тест: cli.main сам зовёт asyncio.run, и внутри уже бегущего
    # цикла это упало бы — как и упало бы у владельца в notebook.
    report = _report(
        Check("redis", False, Level.BLOCKER, "лежит", "важно", "redis-server --port 6380")
    )

    async def fake_checks(root: Path, **kw: Any) -> Report:
        assert root == cli.ROOT
        assert kw["redis_url"] == cli.REDIS_URL
        return report

    monkeypatch.setattr(doctor, "run_checks", fake_checks)
    code = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert code == 1
    assert "команда: redis-server --port 6380" in out


def test_cli_ухудшения_код_не_ломают(monkeypatch, capsys) -> None:
    report = _report(Check("frame", False, Level.DEGRADED, "нет кадра", "важно", "поставить"))

    async def fake_checks(root: Path, **kw: Any) -> Report:
        return report

    monkeypatch.setattr(doctor, "run_checks", fake_checks)
    code = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Готов к работе" in out
