"""Готовность стенда: что сломано, почему это важно и что сделать.

Половина «ферма не работает» — не баг, а неподнятый Redis, отсутствующая
подложка или нет каталога `envs/`. Диагностика, которая называет причину,
экономит больше времени, чем любой отчёт о прогоне. Поэтому у каждой
проверки тут есть ровно три поля: **что** не так, **почему** это важно и
**команда**, которая это чинит.

Проверки разделены на два уровня, и разделение не декоративное:

* **блокер** — без этого прогон не запустится или уедет не туда (настоящий
  токен, мёртвый Redis, кейсов нет);
* **ухудшение** — кейсы отработают и вердикт будет верным, но результат будет
  беднее: не снимется кадр, не найдётся подложка, каталог создастся заново.

Пользователю это различие нужно, чтобы решить, «стоп, я не начинаю» или
«начинаю и потом поставлю». Показывать им оба уровня одной строкой — значит
заставить гадать, где действительно не работает.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from botkit_farm import security

# Порт по умолчанию — тот же, что и в cli.REDIS_URL: отдельный от системного
# 6379, чтобы стенд не мешал локальному Redis разработчика.
DEFAULT_REDIS_URL = "redis://127.0.0.1:6380/0"
REDIS_PORT = 6380


class Level(StrEnum):
    """Насколько неудачная проверка мешает прогону."""

    BLOCKER = "blocker"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class Check:
    """Одна проверка вместе с инструкцией по исправлению."""

    name: str
    ok: bool
    level: Level
    what: str
    why: str
    fix: str


@dataclass(frozen=True, slots=True)
class Report:
    """Сводка проверок. `ok` — можно начинать прогон."""

    checks: tuple[Check, ...]

    @property
    def blockers(self) -> tuple[Check, ...]:
        """Непройденные блокеры: без них прогон не имеет смысла."""
        return tuple(c for c in self.checks if not c.ok and c.level is Level.BLOCKER)

    @property
    def degraded(self) -> tuple[Check, ...]:
        """Непройденные ухудшения: прогон пойдёт, но результат будет беднее."""
        return tuple(c for c in self.checks if not c.ok and c.level is Level.DEGRADED)

    @property
    def ok(self) -> bool:
        """Можно начинать прогон: блокеров нет (ухудшения ему не мешают)."""
        return not self.blockers


# ── 1. Токен ────────────────────────────────────────────────────────────────


def check_token(env: dict[str, str] | None = None) -> Check:
    """Настоящий токен Telegram в окружении — прогон не начинается."""
    try:
        security.assert_fake_token(env)
    except security.SecurityError as exc:
        return Check(
            name="token",
            ok=False,
            level=Level.BLOCKER,
            what=str(exc),
            why=(
                "С настоящим токеном бот отвечает живому пользователю, а ферма "
                "искривляет его работу всеми остальными проверками."
            ),
            fix="unset TELEGRAM_BOT_TOKEN  # или замените значение на фейковое 424242:...",
        )
    return Check(
        name="token",
        ok=True,
        level=Level.BLOCKER,
        what="настоящего токена Telegram в окружении нет",
        why="значит, бот не достучится до живого Telegram и ответит только подделке.",
        fix="",
    )


# ── 2. Redis ────────────────────────────────────────────────────────────────


def redis_command(port: int = REDIS_PORT) -> str:
    """Команда, которой владелец поднимает Redis нужного порта."""
    return f"redis-server --port {port} --daemonize yes"


async def check_redis(url: str = DEFAULT_REDIS_URL) -> Check:
    """Redis отвечает на PING — состояние прошлого прогона можно стирать."""
    try:
        import redis.asyncio as aioredis
    except ImportError as exc:  # pragma: no cover - зависимость из основного набора
        return Check(
            name="redis",
            ok=False,
            level=Level.BLOCKER,
            what=f"пакет redis не установлен ({exc})",
            why="без него не стираются FSM и троттлинг прошлого прогона.",
            fix="pip install -e .",
        )

    port = _port_of(url)
    command = redis_command(port)
    try:
        client = aioredis.from_url(url)
    except Exception as exc:  # noqa: BLE001 — неверный URL тоже должен стать текстом
        return Check(
            name="redis",
            ok=False,
            level=Level.BLOCKER,
            what=f"адрес Redis не разбирается ({exc}): {url}",
            why="без него прогон не стартует: состояние бота негде чистить.",
            fix=f"BOTKIT_FARM_REDIS={url}  # проверьте адрес",
        )
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 — сюда приходит ConnectionError
        return Check(
            name="redis",
            ok=False,
            level=Level.BLOCKER,
            what=f"Redis не отвечает на {url} ({type(exc).__name__})",
            why=(
                "Redis переживает прогон, а база — нет. Без стирки второй запуск "
                "продолжил бы диалог с середины, и кейс падал бы без единой ошибки."
            ),
            fix=command,
        )
    finally:
        await client.aclose()
    return Check(
        name="redis",
        ok=True,
        level=Level.BLOCKER,
        what=f"Redis отвечает на {url}",
        why="состояние прошлого прогона будет стёрто, кейсы не поедут по старой ветке.",
        fix="",
    )


def _port_of(url: str) -> int:
    """Порт из адреса Redis; при неудаче — порт по умолчанию.

    Команда в подсказке должна называть тот порт, о котором заговорил
    владелец, а не зашитый: стенд запускают и на другом порту.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    try:
        return parsed.port or REDIS_PORT
    except ValueError:
        return REDIS_PORT


# ── 3. Каталоги ─────────────────────────────────────────────────────────────


def check_dirs(root: Path) -> list[Check]:
    """Каталоги фермы на месте и в них можно писать."""
    return [
        _dir_check(root, "cases", "каталог кейсов", "botkit-farm init <репозиторий-бота>"),
        _dir_check(root, "bots", "песочница чужих репозиториев", "mkdir -p bots"),
        _dir_check(root, "envs", "виртуальные окружения ботов", "mkdir -p envs"),
        _dir_check(root, "reports", "отчёты и кадры", "mkdir -p reports"),
    ]


def _dir_check(root: Path, name: str, what: str, fix: str) -> Check:
    path = root / name
    if not path.exists():
        return Check(
            name=f"dir:{name}",
            ok=False,
            # Каталоги создаются на лету почти везде, кроме cases: без кейсов
            # запускать нечего.
            level=Level.BLOCKER if name == "cases" else Level.DEGRADED,
            what=f"нет каталога {name}/ — {what} не найден",
            why=(
                "без кейсов запускать нечего."
                if name == "cases"
                else "прогон отработает и создаст каталог сам, но пустая папка — "
                "признак, что ферму ещё не касались руками."
            ),
            fix=fix,
        )
    if not os.access(path, os.W_OK):
        return Check(
            name=f"dir:{name}",
            ok=False,
            level=Level.BLOCKER,
            what=f"каталог {name}/ недоступен для записи",
            why="ферма не сможет положить туда отчёт, клон репозитория или окружение.",
            fix=f"sudo chown -R $(whoami) {path}",
        )
    return Check(
        name=f"dir:{name}",
        ok=True,
        level=Level.DEGRADED,
        what=f"каталог {name}/ на месте и доступен для записи",
        why="ферма сможет работать с ним.",
        fix="",
    )


# ── 4. Подложка и браузер ───────────────────────────────────────────────────


def check_frame_support(root: Path, renderer: Callable[[], list[str]] | None = None) -> Check:
    """Есть ли подложка кадра и запускается ли Chromium.

    Обе вещи нужны только картинке: проверки кейса отрабатывают и без них, а
    вердикт остаётся верным. Поэтому это ухудшение, а не блокер.
    """
    assets = renderer() if renderer is not None else _frame_assets(root)
    if not assets:
        return Check(
            name="frame",
            ok=False,
            level=Level.DEGRADED,
            what="нет подложки assets/tg-bg.* — кадр в режиме window не снимется",
            why=(
                "проверки кейса отработают и вердикт будет верным, но вместо "
                "кадра в отчёте окажется пустое место — читать баг-репорт будет нечем."
            ),
            fix="положите PNG/JPG в assets/ с именем tg-bg.* (или снимите кадр в режиме phone)",
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return Check(
            name="frame",
            ok=False,
            level=Level.DEGRADED,
            what=f"Playwright не установлен ({exc}) — кадр не снимется",
            why="проверки кейса отработают, но в отчёте не будет картинки диалога.",
            fix="pip install playwright && python -m playwright install chromium",
        )
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 — сюда приходит Error от playwright
        return Check(
            name="frame",
            ok=False,
            level=Level.DEGRADED,
            what=f"Chromium не запускается ({type(exc).__name__}) — кадр не снимется",
            why="проверки кейса отработают, но рендер отчёта упадёт на каждом прогоне.",
            fix="python -m playwright install chromium",
        )
    return Check(
        name="frame",
        ok=True,
        level=Level.DEGRADED,
        what=f"подложка {assets[0]} и Chromium запускаются",
        why="кадр диалога будет приложен к отчёту.",
        fix="",
    )


def _frame_assets(root: Path) -> list[str]:
    assets_dir = root / "assets"
    if not assets_dir.is_dir():
        return []
    return sorted(p.name for p in assets_dir.iterdir() if p.name.startswith("tg-bg."))


# ── 5. Изоляция ─────────────────────────────────────────────────────────────


def check_isolation(probe: Callable[[], str | None] | None = None) -> Check:
    """Доступен ли песочник исполнения чужого кода.

    Ферма обещает «любого бота», а бот исполняется с правами владельца. Если
    песочника нет, обещание держится только на честном слове — об этом нужно
    сказать прямо, а не промолчать.
    """
    backend = (probe or detect_isolation)()
    if backend is None:
        return Check(
            name="isolation",
            ok=False,
            level=Level.DEGRADED,
            what="песочник исполнения недоступен — код бота идёт с вашими правами",
            why=(
                "чужой бот сможет писать куда угодно от вашего имени, и это "
                "главный риск фермы; сейчас его прикрывает только запрет egress."
            ),
            fix="apt install bubblewrap  # затем прогоны идут под песочницей",
        )
    return Check(
        name="isolation",
        ok=True,
        level=Level.DEGRADED,
        what=f"песошник доступен через {backend}",
        why="код бота исполняется изолированно от вашей файловой системы.",
        fix="",
    )


def detect_isolation() -> str | None:
    """Имя первого доступного песочника или None.

    Порядок — от дешёвого к дорогому: bubblewrap стоит в каждом дистрибутиве
    и поднимается за миллисекунды, Docker/Podman тяжелее, но дают полноценную
    изоляцию и сетевые правила.
    """
    for backend, probe in (
        ("bubblewrap", "bwrap"),
        ("unshare", "unshare"),
        ("podman", "podman"),
        ("docker", "docker"),
    ):
        path = shutil.which(probe)
        if path is None:
            continue
        if backend == "unshare" and not _unshare_works():
            continue
        if backend in ("podman", "docker") and not _container_works(backend):
            continue
        return backend
    return None


def _abs(name: str) -> str:
    """Полный путь к бинарнику.

    subprocess ищет программу по PATH, а doctor — не то место, где стоит
    подставлять путь из чужой переменной окружения: ищем сами через shutil.
    """
    found = shutil.which(name)
    return found if found else name


def _unshare_works() -> bool:
    """Есть ли unshare, который действительно создаёт namespace.

    Бинарник в системе может быть, а user namespaces — выключены в ядре. Тогда
    подсказка «изоляция доступна» была бы враньём, поэтому проверяем запуск,
    а не наличие файла.
    """
    try:
        # Команда и аргументы тут — константы модуля, путь найден сам.
        done = subprocess.run(  # noqa: S603
            [_abs("unshare"), "--user", "--map-root-user", "true"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _container_works(backend: str) -> bool:
    """Есть ли рабочий демон: бинарник без запущенного dockerd не изоляция."""
    try:
        # Список backend'ов — константа модуля, путь найден сам.
        done = subprocess.run(  # noqa: S603
            [_abs(backend), "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


# ── 6. Кейсы ────────────────────────────────────────────────────────────────


def _stale_files(cases_dir: Path) -> list[str]:
    """Файлы кейсов, которым ещё нужна миграция.

    Проверяются файлы, а не загруженные объекты: загрузка применяет миграции на
    лету, и кейс без `schema_version` прочитался бы нормально. Незакрытая
    версия в файле — это ровно то, на что споткнётся `migrate --check` в CI, а
    doctor должен говорить о том же заранее.
    """
    import yaml

    from botkit_farm.case.migration import SchemaError, needs_migration

    stale: list[str] = []
    for path in sorted(cases_dir.rglob("*.y*ml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and needs_migration(raw):
                stale.append(path.name)
        except (SchemaError, OSError, yaml.YAMLError):
            # Ошибку чтения уже показала проверка загрузки кейсов выше;
            # здесь она была бы вторым упоминанием одной проблемы.
            continue
    return stale


def check_cases(root: Path) -> Check:
    """Кейсы грузятся и лежат на текущей версии схемы.

    Битый файл — блокер: он останется битым и в прогоне, а молча пропущенный
    кейс выглядит как успешный и ничего не доказывает.
    """
    from botkit_farm.case import load_dir

    cases_dir = root / "cases"
    if not cases_dir.is_dir():
        return Check(
            name="cases",
            ok=False,
            level=Level.BLOCKER,
            what=f"нет каталога {cases_dir}",
            why="без кейсов ферма не знает, что проверять у бота.",
            fix="botkit-farm init <репозиторий-бота>  # затем положите YAML в кейсы",
        )
    cases, errors = load_dir(cases_dir)
    if errors:
        first = errors[0]
        more = f" (и ещё {len(errors) - 1})" if len(errors) > 1 else ""
        return Check(
            name="cases",
            ok=False,
            level=Level.BLOCKER,
            what=f"кейсы не грузятся{more}: {first}",
            why="сломанная схема одного кейса останавливает прогон всех остальных.",
            fix=f"botkit-farm migrate --check {cases_dir}  # покажет версию и файл",
        )
    if not cases:
        return Check(
            name="cases",
            ok=False,
            level=Level.BLOCKER,
            what=f"в {cases_dir} нет кейсов",
            why="пустой прогон выглядит как успешный и ничего не доказывает.",
            fix="botkit-farm init <репозиторий-бота>",
        )
    # Файлы, а не загруженные объекты: load_file применяет миграции на лету, и
    # кейс на старой схеме прочитался бы нормально. Незакрытая версия в файле —
    # это то, на что споткнётся `migrate --check` в CI и другая ферма.
    stale = _stale_files(cases_dir)
    if stale:
        shown = ", ".join(stale[:3]) + ("…" if len(stale) > 3 else "")
        return Check(
            name="cases",
            ok=False,
            level=Level.BLOCKER,
            what=f"кейсы без актуальной версии схемы: {shown}",
            why="старый формат прочитается не так, и падать будет не на том поле.",
            fix=f"botkit-farm migrate {cases_dir}",
        )
    return Check(
        name="cases",
        ok=True,
        level=Level.BLOCKER,
        what=f"кейсов {len(cases)}, все на текущей версии схемы",
        why="прогон будет опираться на тот формат, который ферма ожидает.",
        fix="",
    )


# ── Сборка ──────────────────────────────────────────────────────────────────


async def run_checks(
    root: Path,
    env: dict[str, str] | None = None,
    redis_url: str = DEFAULT_REDIS_URL,
) -> Report:
    """Прогнать все проверки и собрать отчёт.

    `env` и `redis_url` передаются параметрами, а не берутся из окружения:
    doctor должен уметь рассказать о чужой машине, не becoming ею.
    """
    checks: list[Check] = [check_token(env)]
    checks.append(await check_redis(redis_url))
    checks.append(check_cases(root))
    checks.extend(check_dirs(root))
    checks.append(check_frame_support(root))
    checks.append(check_isolation())
    return Report(checks=tuple(checks))


def format_report(report: Report) -> str:
    """Человекочитаемый отчёт: сначало блокеры, потом ухудшения.

    Порядок неслучаен: блокеры объясняют, почему не начать, ухудшения — почему
    результат получится беднее, чем обычно. В конце — команда, если починить
    можно одной строкой.
    """
    lines: list[str] = ["Проверка стенда botkit-farm", ""]
    passed = [c for c in report.checks if c.ok]

    lines.append(f"Блокеры — без этого прогон не запустится ({len(report.blockers)})")
    lines.extend(_section(report.checks, failed_only=True, level=Level.BLOCKER))
    lines.append("")
    lines.append(f"Ухудшения — проверки пройдут, результат будет беднее ({len(report.degraded)})")
    lines.extend(_section(report.checks, failed_only=True, level=Level.DEGRADED))
    lines.append("")
    lines.append(f"В порядке: {len(passed)} из {len(report.checks)} проверок прошли.")

    if report.ok:
        lines.append("Готов к работе: botkit-farm run")
    else:
        lines.append("Блокеры прежде: чините команды ниже, потом возвращайтесь.")
    return "\n".join(lines)


def _section(
    checks: tuple[Check, ...],
    failed_only: bool,
    level: Level,
) -> list[str]:
    """Строки одной секции отчёта."""
    rows = [c for c in checks if c.level is level and (not failed_only or not c.ok)]
    if not rows:
        return ["  (нет)"]
    lines: list[str] = []
    for c in rows:
        mark = "OK  " if c.ok else "FAIL"
        lines.append(f"  [{mark}] {c.name}: {c.what}")
        if not c.ok:
            lines.append(f"         почему: {c.why}")
            lines.append(f"         команда: {c.fix}")
    return lines


__all__ = [
    "Check",
    "DEFAULT_REDIS_URL",
    "Level",
    "Report",
    "check_cases",
    "check_dirs",
    "check_frame_support",
    "check_isolation",
    "check_token",
    "detect_isolation",
    "format_report",
    "redis_command",
    "run_checks",
]
