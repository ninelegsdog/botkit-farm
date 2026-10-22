"""Результат прогона кейса: что произошло, где сломалось и что доказано.

Отдельный модуль, потому что результат — это данные, которые читают три разных
потребителя (CLI, репортер, тесты), и у них разные требования. Модель запрещает
смешивать «упал» и «упал с ожидаемым багом»: статус хранится один раз, и по нему
принимается решение, попадёт ли кадр в отчёт.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from botkit_farm.runner.contract import Media


class Status(enum.StrEnum):
    """Итог кейса. Наследует str, чтобы печатался как «passed» в отчёте."""

    PASSED = "passed"
    FAILED = "failed"  # ожидание не выполнено — это баг поведения
    ERRORED = "errored"  # бот упал/не собрался — это ошибка запуска
    SKIPPED = "skipped"  # кейс помечен skip или шаг optional сорвался


@dataclass
class Failure:
    """Одно несоответствие: ожидание против факта.

    Храним и ожидание, и факт целиком. В отчёте нужен не только вердикт
    «текст не найден», но и что именно бот ответил — иначе баг-репорт бесполезен:
    читатель не видит, что вместо ожидаемого пришло.
    """

    step_index: int
    step_label: str
    kind: str  # "text_missing" | "text_mismatch" | "silent" | ...
    expected: str
    actual: str
    hint: str = ""  # что делать автору кейса

    def summary(self) -> str:
        """Короткая строка для вывода в терминал."""
        return f"шаг {self.step_index + 1} ({self.step_label}): {self.kind}"


@dataclass
class Turn:
    """Реплика в диалоге — для кадра и для транскрипции."""

    text: str
    out: bool  # True — клиент, False — бот
    buttons: list[str] = field(default_factory=list)
    # Вложенные файлы ответа (фото, документ, голос…). Хранятся на реплике, а не
    # отдельным списком: кадр рисует медиа как часть того же сообщения бота.
    media: list[Media] = field(default_factory=list)
    # Всплывающее уведомление (answerCallbackQuery): клиент видит его как
    # отдельный элемент, и в кадре оно рисуется иначе, чем обычное сообщение.
    is_toast: bool = False


@dataclass
class CaseResult:
    """Итог одного кейса."""

    case_id: str
    title: str
    status: Status
    steps_done: int = 0
    steps_total: int = 0
    failures: list[Failure] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    frame_path: str | None = None
    error: str | None = None  # трейсбек/причина при ERRORED
    duration_s: float = 0.0
    repo: str = ""
    commit: str = ""
    builder: str = ""
    source: str = ""
    # Чем карантин остановил бота. Не влияет на статус: бот, получивший отказ,
    # мог отработать и пройти кейс, а сам факт попытки — находка для владельца
    # и повод спросить, зачем боту туда надо.
    quarantine: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Кейс прошёл.

        Кадр публикуется только при этом: упавший кейс может дать красивую, но
        неверную картинку, а это хуже отсутствия картинки.
        """
        return self.status is Status.PASSED

    def transcript(self) -> str:
        """Диалог текстом: то, что увидит человек в отчёте.

        Нумерация реплик нужна, чтобы баг-репорт ссылался на конкретный момент
        диалога, а не на «где-то тут».
        """
        lines = []
        for i, t in enumerate(self.turns):
            who = "клиент" if t.out else ("всплыв." if t.is_toast else "бот   ")
            extra = f"  [{'; '.join(t.buttons)}]" if t.buttons else ""
            media = ""
            if t.media:
                media = "  " + "; ".join(
                    f"‹{m.kind}: {m.caption}›" if m.caption else f"‹{m.kind}›" for m in t.media
                )
            lines.append(f"{i:>2} {who}: {t.text}{media}{extra}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Машиночитаемый вид для CI и агрегации отчётов."""
        return {
            "case": self.case_id,
            "title": self.title,
            "status": str(self.status),
            "steps": f"{self.steps_done}/{self.steps_total}",
            "frame": self.frame_path,
            "failures": [
                {
                    "step": f.step_index,
                    "kind": f.kind,
                    "expected": f.expected,
                    "actual": f.actual,
                    "hint": f.hint,
                }
                for f in self.failures
            ],
            "error": self.error,
            "duration_s": round(self.duration_s, 2),
            "repo": self.repo,
            "commit": self.commit,
            "builder": self.builder,
            "source": self.source,
            "quarantine": self.quarantine,
        }


__all__ = ["CaseResult", "Failure", "Status", "Turn"]
