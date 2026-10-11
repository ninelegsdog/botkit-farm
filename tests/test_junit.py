"""JUnit XML (ТЗ D4): кейсы как тесты для любой CI-системы.

Проверки точные и через парсер, а не по подстроке: XML с неверным экранированием
или запрещённым символом выглядит «почти правильным», но падает в CI — то есть
именно там, где он нужен.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from botkit_farm.report.junit import render_junit, write_junit
from botkit_farm.runner.result import CaseResult, Failure, Status

_BOT_TITLE = "Тест «кавычки» & <скобки>"


def _passed() -> CaseResult:
    return CaseResult(
        case_id="ok-case",
        title="Всё хорошо",
        status=Status.PASSED,
        steps_done=2,
        steps_total=2,
        duration_s=1.5,
    )


def _failed() -> CaseResult:
    return CaseResult(
        case_id="bad-case",
        title=_BOT_TITLE,
        status=Status.FAILED,
        steps_done=1,
        steps_total=3,
        duration_s=2.25,
        failures=[
            Failure(0, "написать «/start»", "text_missing", "Запись", "Добро пожаловать", "сверьте")
        ],
    )


def _errored() -> CaseResult:
    return CaseResult(
        case_id="crash",
        title="Бот упал",
        status=Status.ERRORED,
        duration_s=0.5,
        error="AttributeError: 'NoneType' has no attribute 'answer'",
    )


def _skipped() -> CaseResult:
    return CaseResult(case_id="skip", title="Пропущен", status=Status.SKIPPED, duration_s=0.0)


def _suite(results: list[CaseResult]) -> ET.Element:
    return ET.fromstring(render_junit(results))  # noqa: S314 — парсим свой же вывод


def test_прошедший_кейс_становится_testcase_без_пометок() -> None:
    root = _suite([_passed()])
    cases = root.findall("./testsuite/testcase")

    assert len(cases) == 1
    assert cases[0].get("name") == "Всё хорошо"
    assert cases[0].get("classname") == "ok-case"
    assert cases[0].get("time") == "1.500"
    assert cases[0].find("failure") is None
    assert cases[0].find("error") is None


def test_провал_становится_failure_с_фактами() -> None:
    root = _suite([_failed()])
    case = root.find("./testsuite/testcase")
    failure = case.find("failure")

    assert failure is not None
    assert failure.get("message") == "шаг 1: text_missing"
    assert "Запись" in failure.text  # что ожидалось
    assert "Добро пожаловать" in failure.text  # что пришло
    assert "сверьте" in failure.text  # что делать


def test_падение_бота_становится_error() -> None:
    root = _suite([_errored()])
    case = root.find("./testsuite/testcase")
    error = case.find("error")

    assert error is not None
    assert "AttributeError" in error.get("message")
    assert "AttributeError" in error.text


def test_пропуск_становится_skipped() -> None:
    root = _suite([_skipped()])
    case = root.find("./testsuite/testcase")

    assert case.find("skipped") is not None
    assert case.find("failure") is None


def test_агрегаты_в_корне_и_сьюте() -> None:
    root = _suite([_passed(), _failed(), _errored(), _skipped()])
    expected = {"tests": "4", "failures": "1", "errors": "1", "skipped": "1", "time": "4.250"}

    for node in (root, root.find("./testsuite")):
        assert node is not None
        for key, value in expected.items():
            assert node.get(key) == value, key


def test_кавычки_и_скобки_экранируются_и_читаются_обратно() -> None:
    # Заголовок с «», & и <> должен пережить запись и вернуться тем же текстом.
    root = _suite([_failed()])
    assert root.find("./testsuite/testcase").get("name") == _BOT_TITLE


def test_управляющие_символы_вырезаются() -> None:
    """`\\x00` в ответе бота не должен ломать XML-парсер."""
    bad = CaseResult(
        case_id="ctrl",
        title="Текст\x00с нулём\x1b",
        status=Status.FAILED,
        failures=[Failure(0, "шаг", "text_missing", "а\x00", "б\x07", "")],
    )
    root = _suite([bad])  # падает, если XML невалиден

    case = root.find("./testsuite/testcase")
    assert case.get("name") == "Текстс нулём"
    assert "\x00" not in case.find("failure").text


def test_объявление_и_запись_в_файл(tmp_path) -> None:
    text = render_junit([_passed()])
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')

    path = write_junit([_passed()], tmp_path / "nested" / "junit.xml")
    assert path.read_text(encoding="utf-8") == text
