"""Раннер кейсов: гоняет бота и собирает результат."""

from botkit_farm.runner.engine import RunError, run_case
from botkit_farm.runner.result import CaseResult, Failure, Status, Turn

__all__ = ["CaseResult", "Failure", "RunError", "Status", "Turn", "run_case"]
