r"""Отчёт в JUnit XML (ТЗ D4): любая CI-система видит кейсы как тесты.

Markdown и JSON читает человек и скрипт агрегации; JUnit XML читает CI
(Jenkins, GitLab, GitHub Actions через сторонние action'ы, pytest-совместимые
приёмники). Один кейс — один `<testcase>`: провал ожидания становится
`<failure>`, падение бота — `<error>`, пропуск — `<skipped>`.

XML 1.0 запрещает управляющие символы, а бот может прислать их в ответе
(`\\x00`, `\\x1b`). Такие символы вырезаются: иначе отчёт не разберёт ни один
парсер, и «зелёный» прогон окажется нечитаемым ровно тогда, когда он нужен.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

from botkit_farm.runner.result import CaseResult, Status

# Управляющие символы, недопустимые в XML 1.0. Табуляция, перевод строки и
# возврат каретки разрешены — их оставляем.
_FORBIDDEN = frozenset(chr(c) for c in range(0x20)) - {"\t", "\n", "\r"}


def _clean(text: str) -> str:
    """Убрать из текста символы, недопустимые в XML."""
    return "".join(ch for ch in text if ch not in _FORBIDDEN)


def _failure_body(result: CaseResult) -> str:
    """Подробности провала: по каждому шагу — что ждали и что пришло."""
    lines: list[str] = []
    for f in result.failures:
        lines.append(f"шаг {f.step_index + 1} ({f.step_label}): {f.kind}")
        lines.append(f"  ожидалось: {f.expected}")
        lines.append(f"  получено: {f.actual}")
        if f.hint:
            lines.append(f"  что делать: {f.hint}")
    if result.error:
        lines.append(result.error.rstrip())
    return _clean("\n".join(lines))


def _message(result: CaseResult) -> str:
    """Однострочная причина для атрибута message."""
    if result.failures:
        f = result.failures[0]
        return _clean(f"шаг {f.step_index + 1}: {f.kind}")
    if result.error:
        first = (result.error.strip().splitlines() or ["ошибка запуска"])[0]
        return _clean(first)
    return _clean(str(result.status))


def _testcase(result: CaseResult) -> ET.Element:
    case = ET.Element(
        "testcase",
        {
            "classname": _clean(result.case_id),
            "name": _clean(result.title),
            "time": f"{result.duration_s:.3f}",
        },
    )
    if result.status is Status.FAILED:
        node = ET.SubElement(case, "failure", {"message": _message(result)})
        node.text = _failure_body(result)
    elif result.status is Status.ERRORED:
        node = ET.SubElement(case, "error", {"message": _message(result)})
        node.text = _failure_body(result)
    elif result.status is Status.SKIPPED:
        ET.SubElement(case, "skipped", {"message": _message(result)})
    return case


def _counts(results: list[CaseResult]) -> dict[str, str]:
    """Агрегаты для корня и сьюта: их читают CI-парсеры, а не человек."""
    return {
        "tests": str(len(results)),
        "failures": str(sum(1 for r in results if r.status is Status.FAILED)),
        "errors": str(sum(1 for r in results if r.status is Status.ERRORED)),
        "skipped": str(sum(1 for r in results if r.status is Status.SKIPPED)),
        "time": f"{sum(r.duration_s for r in results):.3f}",
    }


def render_junit(results: list[CaseResult], *, name: str = "botkit-farm") -> str:
    """Собрать JUnit XML по прогону и вернуть строкой."""
    root = ET.Element("testsuites", {"name": name, **_counts(results)})
    suite = ET.SubElement(root, "testsuite", {"name": name, **_counts(results)})
    for result in results:
        suite.append(_testcase(result))
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def write_junit(
    results: list[CaseResult], path: pathlib.Path, *, name: str = "botkit-farm"
) -> pathlib.Path:
    """Записать JUnit XML в файл и вернуть путь."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_junit(results, name=name), encoding="utf-8")
    return path


__all__ = ["render_junit", "write_junit"]
