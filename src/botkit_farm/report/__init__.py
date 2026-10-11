"""Отчёты о прогонах: Markdown для человека, JSON и JUnit XML для машины."""

from botkit_farm.report.junit import render_junit, write_junit
from botkit_farm.report.markdown import (
    render_markdown,
    write_json,
    write_report,
    write_summary,
)

__all__ = [
    "render_junit",
    "render_markdown",
    "write_json",
    "write_junit",
    "write_report",
    "write_summary",
]
