"""CLI фермы: study, run, list, doctor, init, migrate, catalog, coverage, select.

Один вход, девять команд, и каждая отвечает на один вопрос владельца:
`study` — «что этот бот умеет», `list` — «какие кейсы есть», `run` — «работает
ли бот», `doctor` — «почему не работает стенд», `init` — «как завести кейсы
рядом с ботом», `migrate` — «схема кейсов ещё та, что мы ждём», `catalog` —
«целы ли регрессы, которыми мы обещаем ловить возвраты», `coverage` — «у каждого
бота есть кейс на ключевой поток», `select` — «какие кейсы CI прогоняет для
этого бота».

Про `doctor` отдельно: почти каждая «ферма не работает» — это не баг, а
неподнятый Redis или отсутствующая подложка. Диагностика, которая называет
причину, экономит больше времени, чем любой отчёт о прогоне.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import time
from typing import Any

from botkit_farm import security
from botkit_farm.case import load_dir, load_file
from botkit_farm.inspector.repo import inspect_repo, render_report
from botkit_farm.report.junit import write_junit
from botkit_farm.report.markdown import write_json, write_report, write_summary
from botkit_farm.runner.engine import run_case
from botkit_farm.runner.result import CaseResult, Status
from botkit_farm.source import prepare

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Тот же Redis, что и у прежнего стенда: отдельный порт, чтобы не мешать
# системному redis на 6379.
REDIS_URL = os.environ.get("BOTKIT_FARM_REDIS", "redis://127.0.0.1:6380/0")
CASES_DIR = ROOT / "cases"
EXAMPLES_DIR = ROOT / "examples"
BOTS_DIR = ROOT / "bots"
REPORTS_DIR = ROOT / "reports"
ENVS_DIR = ROOT / "envs"
WORK_DIR = ROOT / ".work"


# ── doctor ──────────────────────────────────────────────────────────────────


async def _doctor() -> int:
    """Проверить готовность стенда и сказать, что именно не так.

    Разбор проверок живёт в botkit_farm.doctor: там же лежит и решение, что
    считать блокером, а что ухудшением. Здесь только печать и код возврата —
    блокеры дают 1, одни ухудшения оставляют прогон возможным.
    """
    from botkit_farm import doctor

    report = await doctor.run_checks(ROOT, redis_url=REDIS_URL)
    print(doctor.format_report(report))
    return 0 if report.ok else 1


# ── study ───────────────────────────────────────────────────────────────────


def _study(target: str, as_json: bool) -> int:
    """Разобрать репозиторий бота: команды, кнопки, тексты, поколение сборки."""
    src = pathlib.Path(target).expanduser()
    if not src.exists():
        print(f"путь не найден: {src}")
        return 2
    rep = inspect_repo(src)
    if as_json:
        import json

        print(
            json.dumps(
                {
                    "root": str(src),
                    "builder": rep.builder,
                    "reason": rep.builder_reason,
                    "commands": rep.command_names(),
                    "buttons": [{"label": b.label, "callback": b.callback} for b in rep.buttons],
                    "texts": [t.text for t in rep.texts],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_report(rep))
    return 0


# ── init ────────────────────────────────────────────────────────────────────


def _init(target: str, builder: str | None) -> int:
    """Создать каркас пака `.botkit-farm` в репозитории бота.

    Находит поколение сборки по структуре репозитория (если не задано явно) и
    заводит каталоги кейсов и фикстур. Идемпотентно: повторный запуск не
    перезатирает уже написанный `bot.yaml`.
    """
    from botkit_farm.pack import scaffold_pack

    try:
        pack = scaffold_pack(target, builder=builder)
    except (OSError, ValueError) as exc:
        print(f"не удалось создать пак: {exc}")
        return 2

    m = pack.manifest
    print(f"пак: {pack.root}")
    print(f"  бот:        {m.bot.name} · сборка: {m.bot.builder or '? (укажите вручную)'}")
    if m.bot.reason:
        print(f"              ({m.bot.reason})")
    print(f"  кейсы:      {pack.manual_dir}")
    print(f"  генерация:  {pack.generated_dir}")
    print(f"  фикстуры:   {pack.fixtures_dir}")
    print("\nПоложите YAML-кейсы в каталог кейсов и запустите: botkit-farm run --pack .")
    return 0


# ── migrate ─────────────────────────────────────────────────────────────────


def _migrate(target: str, check: bool) -> int:
    """Привести кейсы к текущей версии схемы.

    `--check` ничего не пишет и падает, если хоть один кейс не на текущей версии
    — это шаг CI: схема кейса не должна незаметно разъехаться с фермой. Кейс с
    более новой схемой — ошибка с номером версии, а не молчаливый пропуск.
    """
    import yaml

    from botkit_farm.case.migration import (
        SCHEMA_VERSION,
        SchemaError,
        migrate_file,
        needs_migration,
        version_of,
    )

    root = pathlib.Path(target).expanduser()
    if not root.exists():
        print(f"путь не найден: {root}")
        return 2
    paths = sorted(root.rglob("*.y*ml")) if root.is_dir() else [root]
    if not paths:
        print(f"кейсов не найдено: {root}")
        return 0

    changed = stale = errored = 0
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise SchemaError("кейс должен быть словарём")
            version = version_of(raw)
            if not needs_migration(raw):
                continue
        except (SchemaError, OSError) as exc:
            print(f"  ✗ {path}: {exc}")
            errored += 1
            continue

        if check:
            shown = raw.get("schema_version", f"не проставлена (читается как {version})")
            print(f"  ✗ {path}: схема {shown}, ферма ждёт {SCHEMA_VERSION}")
            stale += 1
            continue
        try:
            if migrate_file(path):
                shown = raw.get("schema_version", f"не проставлена; читалась как {version}")
                print(f"  ✓ {path}: схема {shown} → {SCHEMA_VERSION}")
                changed += 1
        except SchemaError as exc:
            print(f"  ✗ {path}: {exc}")
            errored += 1

    if check:
        print(f"проверено {len(paths)}: устаревших {stale}, ошибок {errored}")
        return 1 if (stale or errored) else 0
    print(f"проверено {len(paths)}: обновлено {changed}, ошибок {errored}")
    return 1 if errored else 0


# ── catalog ─────────────────────────────────────────────────────────────────


def _catalog(target: str, check: bool) -> int:
    """Показать каталог регрессов и проверить его целостность.

    `--check` — шаг CI: манифест и файлы обязаны сойтись в обе стороны. Кейс,
    который удалили из каталога, но забыли в манифесте, — это регресс, о котором
    думают, что он есть. Проверка называет и то, и другое.
    """
    from botkit_farm import catalog as catalog_mod

    root = pathlib.Path(target).expanduser()
    if not root.exists():
        print(f"каталог не найден: {root}")
        return 2

    problems = catalog_mod.check(root)
    try:
        cat = catalog_mod.load(root)
    except catalog_mod.CatalogError:
        cat = None

    if check:
        for p in problems:
            print(f"  ✗ {p}")
        if problems:
            print(f"каталог не цел: {len(problems)} проблем(ы)")
            return 1
        if cat is not None:
            print(f"каталог цел: {len(cat.regressions)} регрессов в {root}")
        return 0

    if cat is None:
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print(f"Регрессов в каталоге: {len(cat.regressions)} ({root})")
    for e in cat.regressions:
        print(f"  • {e.id:32} {e.case}")
        print(f"    зачем: {e.why}")
    for p in problems:
        print(f"  ⚠️  {p}")
    return 1 if problems else 0


# ── list ────────────────────────────────────────────────────────────────────


def _list_cases() -> int:
    cases, errors = load_dir(CASES_DIR)
    if not cases and not errors:
        print(f"Кейсов нет. Положите .yaml в {CASES_DIR}")
        return 0
    if errors:
        print(f"Ошибки в {len(errors)} кейсах:")
        for e in errors:
            print(f"  ✗ {e}")
    print(f"\nКейсов в каталоге: {len(cases)}")
    for c in cases:
        tags = f"  [{','.join(c.tags)}]" if c.tags else ""
        print(f"  • {c.id:28} {c.title}{tags}")
        print(f"    источник: {c.source}   шагов: {len(c.steps)}")
    return 1 if errors else 0


# ── coverage ────────────────────────────────────────────────────────────────


def _coverage(target: str, check: bool) -> int:
    """Показать и проверить обязательства покрытия (кейс на поток каждого бота).

    Отличие от `catalog`: там «регресс X существует», здесь «у каждого бота
    есть кейс на ключевой поток, а не только на админ-гейт». `--check` — шаг
    CI: бот, покрытый одним отказом, не доказывает, что умеет работать.
    """
    from botkit_farm import coverage as coverage_mod

    root = pathlib.Path(target).expanduser()
    if not root.exists():
        print(f"каталог кейсов не найден: {root}")
        return 2

    problems = coverage_mod.check(root)
    try:
        ob = coverage_mod.load(root)
    except coverage_mod.CoverageError:
        ob = None

    if check:
        for p in problems:
            print(f"  ✗ {p}")
        if problems:
            print(f"покрытие не выполнено: {len(problems)} проблем(ы)")
            return 1
        if ob is not None:
            bots = {o.bot for o in ob.obligations}
            print(f"покрытие выполнено: {len(ob.obligations)} обязательств на {len(bots)} ботов")
        return 0

    if ob is None:
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print(f"Обязательств покрытия: {len(ob.obligations)} ({root})")
    for o in ob.obligations:
        mark = "＋" if o.kind == "positive" else "⛔"
        print(f"  {mark} {o.bot:22} {o.flow:28} {o.case}")
    for p in problems:
        print(f"  ⚠️  {p}")
    return 1 if problems else 0


# ── select ───────────────────────────────────────────────────────────────────


def _select(bot: str, cases_dir: str) -> int:
    """Выбрать кейсы бота по манифесту покрытия — ровно то, что прогонит CI.

    Печатает по одному пути к файлу кейса, в порядке по имени. Имя бота
    нормализуется: `bookingbot` и `botkit-bookingbot` — один и тот же бот.

    Кейс, объявленный в манифесте, но отсутствующий на диске, — ошибка с
    ненулевым кодом: пропуск молча вернул бы «зелёный» CI без проверки, то
    есть ровно тот дефект, который команда пришла чинить.
    """
    from botkit_farm import selection

    root = pathlib.Path(cases_dir).expanduser()
    if not root.is_dir():
        print(f"каталог кейсов не найден: {root}")
        return 2

    try:
        paths = selection.cases_for_bot(bot, root)
    except selection.SelectionError as exc:
        print(f"отбор не удался: {exc}", file=sys.stderr)
        return 1

    if not paths:
        # Не ошибка: у бота может не быть кейсов в cases/ (примеры живут в
        # examples/ и отбираются отдельно). Но молчать нельзя — решение «ни
        # одного кейса» принимает CI, и оно должно быть видимым.
        print(f"бот {bot} не объявлен в манифесте покрытия: ни одного кейса", file=sys.stderr)
        return 0

    for path in paths:
        print(path)
    return 0


# ── run ─────────────────────────────────────────────────────────────────────


async def _shoot(case: Any, result: CaseResult) -> None:
    """Снять кадр диалога.

    Кадр снимается и для упавшего кейса — он объясняет, что произошло. Но имя
    получает суффикс `-fail`, чтобы годный и негодный кадр не спутать даже по
    имени файла. Ошибка рендера не должна затирать вердикт кейса.
    """
    if not result.turns:
        return
    from botkit_farm import render

    turns = [
        {
            "text": t.text,
            "out": t.out,
            "buttons": t.buttons,
            "is_toast": t.is_toast,
            "media": [
                {"kind": m.kind, "file_id": m.file_id, "caption": m.caption} for m in t.media
            ],
        }
        for t in result.turns
    ]
    frames = REPORTS_DIR / "frames"
    png = frames / f"{case.id}{'-fail' if not result.ok else ''}.png"
    mode: dict[str, Any] = {
        "window": {"window": True},
        "phone": {"phone": True},
        "wide": {},
    }[case.frame]

    def _render() -> pathlib.Path:
        return render.render(
            turns,
            png,
            title=case.frame_title or case.title,
            handle=case.frame_handle or "",
            **mode,
        )

    try:
        # Рендер синхронный (Playwright Sync API) и не должен крутиться в
        # event loop текущего прогона — уводим в поток.
        result.frame_path = str(await asyncio.to_thread(_render))
    except Exception as exc:  # noqa: BLE001
        print(f"      ⚠️  кадр не снялся: {type(exc).__name__}: {exc}")


async def _run_one(
    case_path: pathlib.Path,
    ref: str | None,
    source: str | None = None,
    local: bool = False,
) -> CaseResult | None:
    from botkit_farm.case.loader import CaseError

    try:
        case = load_file(case_path)
    except CaseError as exc:
        print(f"  ✗ {exc}")
        return None

    print(f"▶ {case.id}: {case.title}")
    security.block_egress()  # после клона — офлайн
    try:
        # source с командной строки выше case.source: CI уже выкачал репозиторий
        # и клонировал бы дефолтную ветку — то есть проверял не то, что меняется
        # в PR. Зелёный прогон обязан доказывать код из чекаута, а не main.
        chosen = source or case.source
        # --local: URL превращаем в локальный чекаут того же бота. Иначе кейс с
        # `source: https://...` клонирует GitHub main и не видит правку в рабочем
        # дереве — разработчик проверяет чужой код. Отсутствие чекаута — ошибка,
        # а не тихий откат на клон: молчание здесь и есть та ловушка, которую
        # флаг закрывает.
        if local and source is None:
            from botkit_farm.source import is_url, local_for

            if is_url(case.source):
                found = local_for(case.source, BOTS_DIR)
                if found is None:
                    print(f"  ✗ --local: нет локального чекаута для {case.source} в {BOTS_DIR}")
                    return None
                chosen = str(found)
        src = await asyncio.to_thread(prepare, chosen, BOTS_DIR, ref=ref)
        print(f"  код: {src.path}{' (клон)' if src.was_cloned else ' (локальная копия)'}")
        result = await asyncio.to_thread(
            run_case,
            case,
            bot_dir=src.path,
            cache_dir=ENVS_DIR,
            work_dir=WORK_DIR,
        )
        if not getattr(result, "source", ""):
            import contextlib

            with contextlib.suppress(Exception):
                result.source = getattr(case, "source", "") or ""
        await _shoot(case, result)
        report = write_report(result, REPORTS_DIR)
        print(f"  → {result.status.value.upper()}  {result.steps_done}/{result.steps_total} шагов")
        for f in result.failures[:5]:
            print(f"      ✗ {f.summary()}: ожидали {f.expected!r}, получили {f.actual[:60]!r}")
        if result.error:
            print(f"      💥 {result.error.strip().splitlines()[0][:160]}")
        for v in result.quarantine[:3]:
            print(f"      🔒 карантин: {v[:140]}")
        if len(result.quarantine) > 3:
            print(f"      🔒 карантин: ещё {len(result.quarantine) - 3} в отчёте")
        print(f"  отчёт: {report.name}")
        if result.frame_path:
            print(f"  кадр:  {result.frame_path}")
        return result
    finally:
        security.allow_egress()


async def _run(args: argparse.Namespace) -> int:
    security.assert_fake_token()  # до всего, что может коснуться сети

    # Каталог кейсов: свой cases/ или кейсы пака (`--pack`), которые бот хранит
    # рядом со своим кодом. Пак найден — источник кейсов меняется целиком.
    cases_root = CASES_DIR
    if args.pack:
        from botkit_farm.pack import load_pack

        try:
            pack = load_pack(args.pack)
        except (FileNotFoundError, ValueError) as exc:
            print(f"пак не загрузился: {exc}")
            return 2
        cases_root = pack.cases_dir
        print(f"пак: {pack.root} (кейсы: {cases_root})")

    if args.case:
        paths = [pathlib.Path(a) for a in args.case]
        for i, p in enumerate(paths):
            if not p.exists():
                paths[i] = cases_root / p
    else:
        paths = sorted(cases_root.rglob("*.y*ml"))
        if args.tag:
            loaded, errs = load_dir(cases_root)
            for e in errs:
                print(f"  ⚠️  {e}")
            all_cases = sorted(cases_root.rglob("*.y*ml"))
            paths = []
            for c in loaded:
                if args.tag in c.tags:
                    paths.append(
                        next(x for x in all_cases if x.stem == c.id or x.name.startswith(c.id))
                    )
        if not paths:
            print(f"кейсы не найдены в {cases_root}")
            return 2

    started = time.monotonic()
    results: list[CaseResult] = []
    for path in paths:
        r = await _run_one(path, args.ref, args.source, args.local)
        if r is not None:
            results.append(r)

    if not results:
        print("\nНи один кейс не отработал.")
        return 1

    summary = write_summary(results, REPORTS_DIR)
    write_json(results, REPORTS_DIR / "_results.json")
    if args.junit:
        junit = write_junit(results, pathlib.Path(args.junit))
        print(f"JUnit XML: {junit}")
    failed = [r for r in results if r.status is not Status.PASSED]
    print(
        f"\nИтог за {time.monotonic() - started:.0f} с: "
        f"{len(results) - len(failed)}/{len(results)} прошли. Сводка: {summary.name}"
    )
    return 1 if failed else 0


# ── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI: разобрать аргументы и передать команду нужному обработчику."""
    ap = argparse.ArgumentParser(
        prog="botkit-farm", description="Ферма тест-кейсов для Telegram-ботов"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="проверить готовность стенда")
    p_doctor.set_defaults(fn=_doctor_cmd)

    p_study = sub.add_parser("study", help="разобрать репозиторий бота")
    p_study.add_argument("target", help="путь к репозиторию")
    p_study.add_argument("--json", action="store_true", help="вывод в JSON")
    p_study.set_defaults(fn=None)

    p_list = sub.add_parser("list", help="показать кейсы")
    p_list.set_defaults(fn=None)

    p_init = sub.add_parser("init", help="создать каркас пака в репозитории бота")
    p_init.add_argument("target", nargs="?", default=".", help="путь к репозиторию бота")
    p_init.add_argument(
        "--builder",
        choices=["legacy", "newgen", "reminder"],
        help="поколение сборки (по умолчанию определяется по коду)",
    )
    p_init.set_defaults(fn=None)

    p_run = sub.add_parser("run", help="прогнать кейсы")
    p_run.add_argument("case", nargs="*", help="файлы кейсов (по умолчанию — все)")
    p_run.add_argument("--tag", help="только кейсы с этим тегом")
    p_run.add_argument("--ref", help="ветка/тег для клонирования")
    p_run.add_argument("--pack", help="каталог пака или репозиторий бота с .botkit-farm")
    p_run.add_argument(
        "--source",
        help="код бота брать отсюда, а не из case.source. Нужно CI: репозиторий "
        "уже в чекауте, а клон из case.source дал бы дефолтную ветку и прогон "
        "не того кода",
    )
    p_run.add_argument(
        "--local",
        action="store_true",
        help="URL в case.source заменить на локальный чекаут bots/<bot> — прогон "
        "незакоммиченных правок. Без чекаута кейс падает, а не уходит в клон",
    )
    p_run.add_argument(
        "--junit",
        metavar="ПУТЬ",
        help="куда записать отчёт JUnit XML (для CI: кейсы видны как тесты)",
    )
    p_run.set_defaults(fn=_run)

    p_migrate = sub.add_parser("migrate", help="привести кейсы к текущей версии схемы")
    p_migrate.add_argument(
        "target", nargs="?", default=str(CASES_DIR), help="файл или каталог кейсов"
    )
    p_migrate.add_argument(
        "--check", action="store_true", help="не менять файлы, только проверить (для CI)"
    )
    p_migrate.set_defaults(fn=None)

    p_catalog = sub.add_parser("catalog", help="показать и проверить каталог регрессов")
    p_catalog.add_argument(
        "target", nargs="?", default=str(EXAMPLES_DIR), help="каталог с манифестом catalog.json"
    )
    p_catalog.add_argument(
        "--check",
        action="store_true",
        help="ничего не печатать лишнего, упасть при разрыве (для CI)",
    )
    p_catalog.set_defaults(fn=None)

    p_coverage = sub.add_parser("coverage", help="показать и проверить обязательства покрытия")
    p_coverage.add_argument(
        "target", nargs="?", default=str(CASES_DIR), help="каталог кейсов с coverage.json"
    )
    p_coverage.add_argument(
        "--check",
        action="store_true",
        help="ничего не печатать лишнего, упасть при непокрытом потоке (для CI)",
    )
    p_coverage.set_defaults(fn=None)

    p_select = sub.add_parser("select", help="выбрать кейсы бота по манифесту покрытия")
    p_select.add_argument(
        "--bot",
        required=True,
        help="имя бота: bookingbot или botkit-bookingbot — это один и тот же бот",
    )
    p_select.add_argument(
        "--cases-dir",
        default=str(CASES_DIR),
        help="каталог кейсов с манифестом coverage.json",
    )
    p_select.set_defaults(fn=None)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "doctor":
            return asyncio.run(_doctor())
        if args.cmd == "study":
            return _study(args.target, args.json)
        if args.cmd == "list":
            return _list_cases()
        if args.cmd == "init":
            return _init(args.target, args.builder)
        if args.cmd == "migrate":
            return _migrate(args.target, args.check)
        if args.cmd == "catalog":
            return _catalog(args.target, args.check)
        if args.cmd == "coverage":
            return _coverage(args.target, args.check)
        if args.cmd == "select":
            return _select(args.bot, args.cases_dir)
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nпрервано")
        return 130


def _doctor_cmd(_a: Any) -> int:
    return asyncio.run(_doctor())


if __name__ == "__main__":
    sys.exit(main())
