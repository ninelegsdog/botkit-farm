"""CLI фермы: study, run, list, doctor.

Один вход, четыре команды, и каждая отвечает на один вопрос владельца:
`study` — «что этот бот умеет», `list` — «какие кейсы есть», `run` — «работает
ли бот», `doctor` — «почему не работает стенд».

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
from botkit_farm.report.markdown import write_json, write_report, write_summary
from botkit_farm.runner.engine import run_case
from botkit_farm.runner.result import CaseResult, Status
from botkit_farm.source import prepare

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Тот же Redis, что и у прежнего стенда: отдельный порт, чтобы не мешать
# системному redis на 6379.
REDIS_URL = os.environ.get("BOTKIT_FARM_REDIS", "redis://127.0.0.1:6380/0")
CASES_DIR = ROOT / "cases"
BOTS_DIR = ROOT / "bots"
REPORTS_DIR = ROOT / "reports"
ENVS_DIR = ROOT / "envs"


# ── doctor ──────────────────────────────────────────────────────────────────


async def _doctor() -> int:
    """Проверить готовность стенда и сказать, что именно не так."""
    ok = True
    print("Проверка стенда botkit-farm\n")

    # 1. Токен
    try:
        security.assert_fake_token()
        print("  ✅ нет настоящего токена Telegram в окружении")
    except security.SecurityError as exc:
        ok = False
        print(f"  ❌ {exc}")

    # 2. Redis
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(REDIS_URL)
        try:
            await r.ping()
            print(f"  ✅ Redis отвечает на {REDIS_URL}")
        finally:
            await r.aclose()
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(
            f"  ❌ Redis недоступен на {REDIS_URL} ({type(exc).__name__}). "
            f"Поднять: redis-server --port 6380 --daemonize yes"
        )

    # 3. Подложка кадра
    from botkit_farm.render import _bg_assets

    if _bg_assets():
        print(f"  ✅ подложка кадра: {_bg_assets()[0].name}")
    else:
        print(
            "  ⚠️  нет подложки assets/tg-bg.* — кадр в режиме window не снимется "
            "(другие режимы работают)"
        )

    # 4. Playwright
    try:
        # Chromium проверяем в рабочем потоке: Playwright Sync API нельзя
        # вызывать изнутри работающего event loop, а doctor — async. Ровно та
        # же причина, по которой рендер уводится в to_thread в движке.
        def _chromium_ok() -> None:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                p.chromium.launch().close()

        await asyncio.to_thread(_chromium_ok)
        print("  ✅ Chromium запускается")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(
            f"  ❌ Chromium не запускается ({type(exc).__name__}). "
            f"Починить: python -m playwright install chromium"
        )

    print()
    print("Готов к работе." if ok else "Есть блокеры — см. выше.")
    return 0 if ok else 1


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

    turns = [{"text": t.text, "out": t.out, "buttons": t.buttons} for t in result.turns]
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


async def _run_one(case_path: pathlib.Path, ref: str | None) -> CaseResult | None:
    from botkit_farm.case.loader import CaseError

    try:
        case = load_file(case_path)
    except CaseError as exc:
        print(f"  ✗ {exc}")
        return None

    print(f"▶ {case.id}: {case.title}")
    security.block_egress()  # после клона — офлайн
    try:
        src = await asyncio.to_thread(prepare, case.source, BOTS_DIR, ref=ref)
        print(f"  код: {src.path}{' (клон)' if src.was_cloned else ' (локальная копия)'}")
        result = await asyncio.to_thread(
            run_case,
            case,
            bot_dir=src.path,
            cache_dir=ENVS_DIR,
        )
        await _shoot(case, result)
        report = write_report(result, REPORTS_DIR)
        print(f"  → {result.status.value.upper()}  {result.steps_done}/{result.steps_total} шагов")
        for f in result.failures[:5]:
            print(f"      ✗ {f.summary()}: ожидали {f.expected!r}, получили {f.actual[:60]!r}")
        if result.error:
            print(f"      💥 {result.error.strip().splitlines()[0][:160]}")
        print(f"  отчёт: {report.name}")
        if result.frame_path:
            print(f"  кадр:  {result.frame_path}")
        return result
    finally:
        security.allow_egress()


async def _run(args: argparse.Namespace) -> int:
    security.assert_fake_token()  # до всего, что может коснуться сети

    if args.case:
        paths = [pathlib.Path(a) for a in args.case]
        for p in paths:
            if not p.exists():
                p = CASES_DIR / p
    else:
        paths = sorted(CASES_DIR.rglob("*.y*ml"))
        if args.tag:
            loaded, errs = load_dir(CASES_DIR)
            for e in errs:
                print(f"  ⚠️  {e}")
            paths = []
            for c in loaded:
                if args.tag in c.tags:
                    paths.append(
                        next(
                            x
                            for x in sorted(CASES_DIR.rglob("*.y*ml"))
                            if x.stem == c.id or x.name.startswith(c.id)
                        )
                    )
        if not paths:
            print(f"кейсы не найдены в {CASES_DIR}")
            return 2

    started = time.monotonic()
    results: list[CaseResult] = []
    for path in paths:
        r = await _run_one(path, args.ref)
        if r is not None:
            results.append(r)

    if not results:
        print("\nНи один кейс не отработал.")
        return 1

    summary = write_summary(results, REPORTS_DIR)
    write_json(results, REPORTS_DIR / "_results.json")
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

    p_run = sub.add_parser("run", help="прогнать кейсы")
    p_run.add_argument("case", nargs="*", help="файлы кейсов (по умолчанию — все)")
    p_run.add_argument("--tag", help="только кейсы с этим тегом")
    p_run.add_argument("--ref", help="ветка/тег для клонирования")
    p_run.set_defaults(fn=_run)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "doctor":
            return asyncio.run(_doctor())
        if args.cmd == "study":
            return _study(args.target, args.json)
        if args.cmd == "list":
            return _list_cases()
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nпрервано")
        return 130


def _doctor_cmd(_a: Any) -> int:
    return asyncio.run(_doctor())


if __name__ == "__main__":
    sys.exit(main())
