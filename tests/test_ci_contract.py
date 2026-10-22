"""Контракт CI: что именно ферма гоняет для бота.

Дефект, который этот файл закрывает. CI выбирал кейсы глобом по короткому
имени бота (`bookingbot`), а кейсы фермы названы по потокам (`booking-…`).
Глоб молча выбрасывал всё, что не начиналось с короткого имени: три кейса бота
bookingbot не исполнялись ни в одном CI, а гейт покрытия считал поток закрытым
по обязательству на один из них. «Зелёный CI» в этом случае означал «не
проверяли».

Теперь набор кейсов бота берётся из манифеста покрытия (`cases/coverage.json`):
обязательства плюс `extras`. Файл держит три вещи:

* **набор равен** — для каждого бота из реестра флота то, что CI прогоняет,
  совпадает с тем, что объявлено в манифесте;
* **регрессия на исходный дефект** — в workflow больше нет отбора кейсов по
  имени файла;
* **негативные близнецы** — сдвинутый манифест и объявленный, но отсутствующий
  на диске кейс обязаны ронять отбор, а не проходить молча.

Ожидания вычисляются здесь независимо от `botkit_farm.selection`. Если тест
возьмёт ключ сопоставления из самого модуля, сломанный модуль и сломанный тест
сойдутся: оба станут «пустыми», и проверка останется зелёной на сломанном коде.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import subprocess
import sys

from botkit_farm import cli, coverage

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
WORKFLOW = ROOT / ".github" / "workflows" / "farm-smoke.yml"

#: Команда отбора в том виде, в каком её зовёт CI.
SELECT_LINE = 'botkit-farm select --bot "$BOT" --cases-dir cases'
#: Прогон, в который уходят выбранные кейсы.
RUN_LINE = 'botkit-farm run "${cases[@]}"'

#: Отбор кейсов по имени файла — исходный дефект. Ловится и префиксная форма
#: (имя бота в имени файла), и любая другая выборка кейсов глобом.
GLOB_В_WORKFLOW = re.compile(r"cases/[^\s]*\*|cases/[\"']?\$\{?BOT")


# ── независимое вычисление ожиданий ──────────────────────────────────────────


def _key(name: str) -> str:
    """Ключ сопоставления имён ботов — здесь, а не из модуля отбора.

    Короткое имя (`bookingbot`) и полное (`botkit-bookingbot`) — один бот.
    """
    cleaned = name.strip().lower()
    return cleaned[len("botkit-") :] if cleaned.startswith("botkit-") else cleaned


def _объявлено(cases_dir: pathlib.Path, bot: str) -> set[str]:
    """Имена файлов кейсов бота по манифесту — ожидание для CI."""
    manifest = coverage.load(cases_dir)
    key = _key(bot)
    names = {ob.case for ob in manifest.obligations if _key(ob.bot) == key}
    names |= {extra.case for extra in manifest.extras if _key(extra.bot) == key}
    return names


def _ожидаемые_пути(cases_dir: pathlib.Path, bot: str) -> set[str]:
    """Пути, которые CI обязан передать в прогон для этого бота."""
    return {str(cases_dir / name) for name in _объявлено(cases_dir, bot)}


def _отбор(bot: str, cases_dir: pathlib.Path) -> tuple[int, set[str], str]:
    """Что выдаёт команда отбора: код возврата, набор файлов, stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["select", "--bot", bot, "--cases-dir", str(cases_dir)])
    файлы = {line.strip() for line in out.getvalue().splitlines() if line.strip()}
    return code, файлы, err.getvalue()


def _проблемы_контракта(cases_dir: pathlib.Path, bot: str) -> list[str]:
    """Расхождения между манифестом и тем, что CI прогоняет."""
    problems: list[str] = []
    code, файлы, err = _отбор(bot, cases_dir)
    if code != 0:
        problems.append(f"отбор упал (код {code}): {err.strip()}")
    ожидаемые = _ожидаемые_пути(cases_dir, bot)
    if файлы != ожидаемые:
        problems.append(
            f"CI выбрал не то: лишние {sorted(файлы - ожидаемые)}, "
            f"потеряны {sorted(ожидаемые - файлы)}"
        )
    if not ожидаемые:
        problems.append(f"бот {bot} не объявлен в манифесте: CI прогоняет для него пустоту")
    return problems


def _проблемы_workflow(text: str) -> list[str]:
    """Что в workflow не так с отбором кейсов."""
    problems: list[str] = []
    if GLOB_В_WORKFLOW.search(text):
        problems.append("workflow отбирает кейсы по имени файла (глоб), а не по манифесту")
    if SELECT_LINE not in text:
        problems.append("workflow не зовёт команду отбора из манифеста")
    if RUN_LINE not in text:
        problems.append("workflow не передаёт выбранные кейсы в прогон")
    return problems


def _не_выбранные(cases_dir: pathlib.Path, bots: list[str]) -> set[str]:
    """Кейсы корпуса, которых не выбирает ни один бот, — мёртвые кейсы."""
    корпус = {path.name for path in cases_dir.glob("*.yaml")}
    выбранные: set[str] = set()
    for bot in bots:
        code, файлы, _ = _отбор(bot, cases_dir)
        if code != 0:
            continue
        выбранные |= {pathlib.Path(f).name for f in файлы}
    return корпус - выбранные


# ── вспомогательные файлы для негативных тестов ──────────────────────────────


def _кейс(dir_: pathlib.Path, имя: str) -> None:
    (dir_ / имя).write_text(
        f"id: {имя.removesuffix('.yaml')}\n"
        "title: Кейс\n"
        "source: https://example.invalid/bot\n"
        "steps:\n"
        "  - send: /start\n"
        "    expect:\n"
        '      text_contains: ["Ок"]\n',
        encoding="utf-8",
    )


def _манифест(
    dir_: pathlib.Path,
    обязанности: list[dict[str, str]],
    extras: list[dict[str, str]] | None = None,
) -> None:
    (dir_ / coverage.MANIFEST).write_text(
        json.dumps(
            {"schema_version": 2, "obligations": обязанности, "extras": extras or []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _обязательство(bot: str, кейс: str) -> dict[str, str]:
    return {"bot": bot, "flow": "поток", "case": кейс, "kind": "positive"}


# ── набор CI = манифест ───────────────────────────────────────────────────────


def test_для_каждого_бота_флота_набор_равен_набору_ci():
    """Набор, который прогоняет CI, обязан совпадать с манифестом.

    Проверка идёт по каждому боту из реестра флота: реестр — список тех, кто
    вообще обязан быть проверен. Если CI выберет другой набор, «зелёный» прогон
    перестаёт значить «проверен».
    """
    fleet = coverage.load_fleet(CASES)
    for bot in fleet.bots:
        assert _проблемы_контракта(CASES, bot) == [], f"бот {bot}"


def test_все_кейсы_корпуса_попадают_в_прогон():
    """Ни один кейс не остаётся мёртвым: наборы ботов покрывают весь корпус.

    Это исходный дефект в его сущности: кейс, который не выбирает ни один бот,
    не исполняется никогда, а гейт покрытия считает поток закрытым.
    """
    fleet = coverage.load_fleet(CASES)
    assert _не_выбранные(CASES, fleet.bots) == set()


def test_имя_бота_нормализуется():
    """Короткое имя и полное — один и тот же бот.

    CI зовёт ботов коротким именем, манифест пишет полным. Если различие в
    написании решало, чей это кейс, половина корпуса выпадала бы из прогона
    молча.
    """
    код_короткий, короткий, _ = _отбор("bookingbot", CASES)
    код_полный, полный, _ = _отбор("botkit-bookingbot", CASES)
    assert код_короткий == 0 and код_полный == 0
    assert короткий == полный
    assert len(короткий) == 4


def test_чужое_имя_бота_не_выбирает_чужие_кейсы():
    """Имя, которого нет в манифесте, не должно молча получить «все кейсы».

    Негативный близнец проверки выше: если бы отбор при незнакомом имени
    выдавал весь корпус, проверка «набор равен манифесту» стала бы
    неразличимой для любого бота.
    """
    код, файлы, err = _отбор("booking", CASES)
    assert код == 0
    assert файлы == set()
    assert "не объявлен" in err


def test_команда_в_процессе_даёт_те_же_кейсы():
    """Настоящая командная строка, а не только вызов `main()`.

    `main()` подменяет вывод, а CI зовёт команду из шелл-скрипта отдельным
    процессом. Здесь проверяется именно процесс: код возврата и файлы в
    stdout.
    """
    proc = subprocess.run(  # noqa: S603 — аргументы фиксированы, ввод не участвует
        [
            sys.executable,
            "-m",
            "botkit_farm.cli",
            "select",
            "--bot",
            "bookingbot",
            "--cases-dir",
            str(CASES),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    файлы = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    assert файлы == _ожидаемые_пути(CASES, "bookingbot")
    assert len(файлы) == 4


# ── workflow ─────────────────────────────────────────────────────────────────


def test_в_workflow_нет_отбора_по_имени_файла():
    """Регрессия на исходный дефект: workflow больше не отбирает кейсы глобом.

    Глоб по короткому имени бота не видел кейсов, названных по потоку, — они
    не исполнялись ни в одном CI, а гейт покрытия считал поток закрытым.
    """
    assert _проблемы_workflow(WORKFLOW.read_text(encoding="utf-8")) == []


def test_проверка_workflow_ловит_глоб():
    """Негативный близнец: проверка обязана краснеть на глобе.

    Без этого теста проверка выше — зелёная лампочка: она искала бы то, чего
    в файле нет, и всегда проходила бы.
    """
    текст = "\n".join([SELECT_LINE, 'cases=(cases/"$BOT"-*.yaml)', RUN_LINE])
    проблемы = _проблемы_workflow(текст)
    assert проблемы, "проверка не заметила отбор по имени файла"
    assert any("глоб" in p for p in проблемы), проблемы


def test_проверка_workflow_ловит_отсутствие_команды_отбора():
    """Негативный близнец: workflow без команды отбора — тоже расхождение.

    Иначе «нет глоба» можно выполнить, убрав отбор совсем: CI продолжит
    прогон, но кейсы перестанут приходить из манифеста.
    """
    текст = "\n".join(['cases=(cases/"$BOT"-*.yaml)', RUN_LINE])
    проблемы = _проблемы_workflow(текст)
    assert any("не зовёт команду отбора" in p for p in проблемы), проблемы


def test_workflow_отдаёт_выбранные_кейсы_в_прогон():
    """Выбранные кейсы должны дойти до прогона, а не остаться в переменной."""
    текст = WORKFLOW.read_text(encoding="utf-8")
    assert SELECT_LINE in текст
    assert RUN_LINE in текст


# ── негативные близнецы отбора ────────────────────────────────────────────────


def test_сдвинутый_манифест_ломает_контракт(tmp_path):
    """Манифест ссылается на файл, которого нет, — контракт CI нарушен.

    Сдвиг имени (файл переименовали, манифест забыли) обязан быть виден: иначе
    CI продолжит прогон и останется зелёным — то есть «не проверяли».
    """
    _кейс(tmp_path, "happy.yaml")
    _манифест(tmp_path, [_обязательство("botkit-x", "сдвинутый.yaml")])
    проблемы = _проблемы_контракта(tmp_path, "x")
    assert проблемы, "сдвинутый манифест не замечен"
    assert any("отбор упал" in p for p in проблемы), проблемы


def test_объявленный_кейс_без_файла_роняет_команду(tmp_path):
    """Файл кейса удалён, а манифест о нём помнит — команда обязана упасть.

    Молчаливый пропуск здесь — ровно тот дефект, который команда пришла
    чинить: «зелёный CI» при отсутствующей проверке.
    """
    _манифест(tmp_path, [_обязательство("botkit-x", "нет.yaml")])
    код, файлы, err = _отбор("x", tmp_path)
    assert код != 0, "команда обязана упасть на отсутствующем кейсе"
    assert файлы == set()
    assert "нет.yaml" in err, err


def test_кейс_не_объявленный_в_манифесте_остаётся_мёртвым(tmp_path):
    """Негативный близнец проверки «все кейсы попадают в прогон».

    Кейс, который не объявлен в манифесте, не выбирает ни один бот — значит,
    он не исполняется никогда. Проверка обязана называть его, а не молчать.
    """
    _кейс(tmp_path, "orphan.yaml")
    _кейс(tmp_path, "happy.yaml")
    _манифест(tmp_path, [_обязательство("botkit-x", "happy.yaml")])
    assert _не_выбранные(tmp_path, ["x"]) == {"orphan.yaml"}
