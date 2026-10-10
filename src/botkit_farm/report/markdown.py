"""Отчёты о прогонах: Markdown для человека, JSON для машины.

Репорт пишется всегда, а не только при падении. Зелёный прогон — тоже
результат: он доказывает, что кейс исполняется и что поведение не изменилось.
Отчёт, который появляется исключительно при ошибке, не отвечает на вопрос «что
изменилось со вчера».
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

from botkit_farm.runner.result import CaseResult, Status


def _status_label(status: Status) -> str:
    return {
        Status.PASSED: "ПРОШЁЛ",
        Status.FAILED: "ПРОВАЛЕН",
        Status.ERRORED: "ОШИБКА ЗАПУСКА",
        Status.SKIPPED: "ПРОПУЩЕН",
    }[status]


def render_markdown(result: CaseResult) -> str:
    """Человекочитаемый отчёт по одному кейсу."""
    lines: list[str] = []
    lines.append(f"# {_status_label(result.status)} — {result.title}")
    lines.append("")
    lines.append(f"- кейс: `{result.case_id}`")
    lines.append(f"- шагов: {result.steps_done}/{result.steps_total}")
    lines.append(f"- длительность: {result.duration_s:.1f} с")
    if result.repo:
        lines.append(
            f"- репозиторий: `{result.repo}`" + (f" @ {result.commit}" if result.commit else "")
        )
    if result.builder and result.builder != "?":
        lines.append(f"- сборка: {result.builder}")
    if result.frame_path:
        frame = pathlib.Path(result.frame_path)
        lines.append(f"- кадр: `{frame.name}` ({frame.parent})")
    lines.append(f"- время прогона: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    if result.error:
        lines.append("## Ошибка запуска")
        lines.append("")
        lines.append("```")
        lines.append(result.error.rstrip())
        lines.append("```")
        lines.append("")

    if result.failures:
        lines.append("## Что не совпало")
        lines.append("")
        for f in result.failures:
            lines.append(f"### Шаг {f.step_index + 1}: {f.step_label}")
            lines.append("")
            lines.append(f"- **причина**: `{f.kind}`")
            lines.append(f"- **ожидалось**: {f.expected}")
            lines.append(f"- **получено**: {f.actual}")
            if f.hint:
                lines.append(f"- **что делать**: {f.hint}")
            lines.append("")

    if result.turns:
        lines.append("## Диалог")
        lines.append("")
        lines.append("```")
        lines.append(result.transcript())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def write_report(result: CaseResult, reports_dir: pathlib.Path) -> pathlib.Path:
    """Записать отчёт по кейсу в reports/<id>.md и вернуть путь."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{result.case_id}.md"
    header = (
        "<!-- Сгенерировано botkit-farm. Правьте кейс, а не отчёт: "
        "отчёт перезаписывается при каждом прогоне. -->\n\n"
    )
    path.write_text(header + render_markdown(result), encoding="utf-8")
    return path


def write_summary(results: list[CaseResult], reports_dir: pathlib.Path) -> pathlib.Path:
    """Итог по всем кейсам: один файл, куда смотрят в первую очередь."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "_summary.md"
    passed = sum(1 for r in results if r.status is Status.PASSED)
    failed = sum(1 for r in results if r.status is Status.FAILED)
    errored = sum(1 for r in results if r.status is Status.ERRORED)

    lines = [f"# Итог прогона — {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    lines.append(
        f"Пройдено **{passed}**, провалено **{failed}**, "
        f"ошибок запуска **{errored}**, всего **{len(results)}**."
    )
    lines.append("")
    lines.append("| кейс | статус | шагов | что не так |")
    lines.append("|---|---|---|---|")
    for r in results:
        if r.failures:
            why = "; ".join(f.step_label and f.kind or f.kind for f in r.failures[:3])
        elif r.error:
            why = (r.error.strip().splitlines() or ["ошибка"])[0][:80]
        else:
            why = "—"
        mark = {"passed": "✅", "failed": "❌", "errored": "💥", "skipped": "⏭"}[str(r.status)]
        lines.append(
            f"| `{r.case_id}` | {mark} {r.status} | {r.steps_done}/{r.steps_total} | {why} |"
        )
    lines.append("")
    lines.append("Подробности — в `<id>.md` рядом; кадры — в каталоге `frames/`.")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_json(results: list[CaseResult], path: pathlib.Path) -> pathlib.Path:
    """Машиночитаемый итог — для CI и агрегации."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "passed": sum(1 for r in results if r.status is Status.PASSED),
        "failed": sum(1 for r in results if r.status is Status.FAILED),
        "errored": sum(1 for r in results if r.status is Status.ERRORED),
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = ["render_markdown", "write_json", "write_report", "write_summary"]
