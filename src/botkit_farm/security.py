"""Безопасность фермы: секреты, egress, песочница путей.

Модель угроз здесь простая и конкретная. Ферма берёт чужой репозиторий (код
бота), исполняет его и рисует по его ответам картинки. Значит опасности ровно
три:

1. **Настоящий токен Telegram в окружении.** Тогда «локальный стенд» одним
   запросом уедет в сеть и бот начнёт отвечать живому человеку. Стоп — до
   запуска, а не «разберёмся потом».
2. **Исходящие запросы.** Бот подключается к фейковому API на 127.0.0.1, но
   его код может попытаться пойти куда угодно ещё. Запрещаем egress мимо
   localhost: ферме не нужен интернет во время прогона.
3. **Путь кода/кейса/репо.** Кейс читает файлы по строке, репо клонируется по
   URL. Проверяем, что пути остаются внутри проекта, а клон — в песочнице.

Всё это — проверки «до того, как что-то начало работать». Обнаружение
post-mortem не защищает.
"""

from __future__ import annotations

import ipaddress
import os
import pathlib
import re
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

# Токен Telegram — длинная цифро-буквенная строка с двоеточием. Наш фейковый
# начинается с 424242 — по префиксу и отсекаем сомнительное.
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
FAKE_PREFIX = "424242"


class SecurityError(Exception):
    """Нарушение правил безопасности. Прогон не начинается."""


# ── 1. Токен ────────────────────────────────────────────────────────────────


def assert_fake_token(env: Mapping[str, object] | None = None) -> None:
    """Убедиться, что в окружении нет настоящего токена Telegram.

    Проверяется всё, что похоже на токен, а не только TELEGRAM_BOT_TOKEN: бот мог
    остаться с ключом в .env или в переменной с другим именем, и «мой стенд
    случайно отвечает живому пользователю» — самая дорогая ошибка здесь.

    Тип значений — object, а не str: os.environ строки, но тесты и вызывающий
    код могут передать что угодно, и пропуск не-строки молча пропустил бы токен.
    """
    env = env if env is not None else dict(os.environ)
    for name, value in env.items():
        if not isinstance(value, str):
            continue
        v = value.strip()
        if not TOKEN_RE.match(v):
            continue
        if not v.startswith(FAKE_PREFIX):
            raise SecurityError(
                f"переменная {name} содержит настоящий токен Telegram — прогон "
                f"остановлен. Ферма работает только с фейковым токеном "
                f"({FAKE_PREFIX}:...). Очистите переменную или замените значение."
            )


# ── 2. Egress ───────────────────────────────────────────────────────────────


# Локальные адреса, куда egress разрешён: фейковый API и Redis стоят на loopback.
def _is_local(host: str) -> bool:
    # "localhost" — единственное имя, которое не разбирается как IP; числовые
    # loopback-адреса (127.0.0.1, ::1) проверяет ipaddress. Дублировать их в
    # множестве значило бы держать ветку, которую мутационный тест не может
    # отличить (её результат совпадает с is_loopback).
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _EgressGuard:
    """Подменяет connect так, что наружу не уйти.

    aiogram ходит в сеть через aiohttp, но низкоуровневые клиенты (requests,
    urllib, сырой socket) обходят его. Поэтому защита ставится на socket.connect
    — последнем рубеже перед реальным пакетом.
    """

    def __init__(self) -> None:
        self._orig = socket.socket.connect
        self.enabled = False
        self.blocked: list[str] = []

    def install(self) -> None:
        guard = self

        def guarded(sock: socket.socket, address: Any) -> Any:  # noqa: ANN401
            host = address[0] if isinstance(address, tuple) else address
            if not guard.enabled:
                return guard._orig(sock, address)
            if not (isinstance(host, str) and _is_local(host)):
                guard.blocked.append(str(address))
                raise SecurityError(
                    f"попытка исходящего подключения к {address!r} заблокирована: "
                    f"ферма работает офлайн. Бот должен обращаться только к "
                    f"поддельному API на 127.0.0.1."
                )
            return guard._orig(sock, address)

        socket.socket.connect = guarded  # type: ignore[assignment, method-assign]
        self.enabled = True

    def disable(self) -> None:
        socket.socket.connect = self._orig  # type: ignore[method-assign]
        self.enabled = False


_guard = _EgressGuard()


def block_egress() -> _EgressGuard:
    """Включить запрет исходящих подключений (кроме loopback)."""
    _guard.install()
    return _guard


def allow_egress() -> None:
    """Снять запрет — нужно на клонирование репозитория по сети."""
    _guard.disable()


# ── 3. Песочница путей ──────────────────────────────────────────────────────


def safe_join(root: pathlib.Path, *parts: str) -> pathlib.Path:
    """Собрать путь внутри root, не дав выйти наружу.

    Абсолютный путь и «..» в частях — два способа уйти из каталога; оба
    запрещены. Это защита не от злого умысла (кейсы пишет владелец), а от
    опечатки, которая тихо перезапишет файл не там.
    """
    root = root.resolve()
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise SecurityError(f"не удалось разрешить путь {candidate}: {exc}") from exc
    if resolved != root and root not in resolved.parents:
        raise SecurityError(f"путь {candidate} выходит за пределы {root}")
    return resolved


def check_repo_url(url: str, sandbox: pathlib.Path) -> None:
    """Проверить, что репозиторий тянуть безопасно.

    Разрешены git/https/ssh и локальный путь; любая другая схема — отказ.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "git", "ssh", ""):
        raise SecurityError(
            f"источник «{url}»: схема {parsed.scheme!r} не поддерживается. "
            f"Ожидается https/ssh/git-URL либо локальный путь."
        )
    sandbox.mkdir(parents=True, exist_ok=True)


# ── 4. Секреты в отчётах и репозитории ───────────────────────────────────────

# Подстроки, которые не должны попадать в отчёт или в git: токены, ключи.
SECRET_PATTERNS = (
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),  # токен Telegram
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # приватный ключ
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key
    re.compile(r"(?i)\b(api_hash|api_id)\s*[:=]\s*\d{5,}\b"),  # telegram api_hash
    re.compile(r"(?i)\b(?:sk|pk)-live-[A-Za-z0-9]{16,}\b"),  # stripe
    re.compile(r"(?i)\bpassword\s*[:=]\s*\S{8,}"),
)


def scrub(text: str) -> tuple[str, list[str]]:
    """Вычистить секреты из текста, вернуть (текст, найденное).

    Отчёт о прогоне может содержать текст, который бот прислал в ответ, — а бот
    вполне может прочитать свой .env и показать токен. Отчёт попадает в git, и
    токен уедет в историю навсегда. Поэтому вычистка обязана быть до записи.
    """
    found: list[str] = []
    out = text
    for pat in SECRET_PATTERNS:
        for m in pat.findall(out):
            # findall отдаёт либо строку, либо кортеж групп; приводим к строке,
            # чтобы запись о находке не зависела от формы конкретной регулярки.
            found.append(str(m))
            out = pat.sub("[скрыто фермой]", out)
    return out, found


def assert_no_secrets(text: str, where: str) -> None:
    """Бросить, если в тексте есть секрет. Для кода, который идёт в репозиторий."""
    _, found = scrub(text)
    if found:
        raise SecurityError(
            f"{where}: обнаружено {len(found)} секретоподобных строк(и). "
            f"Отчёт не пишу — вычистите текст или токен."
        )


__all__ = [
    "SecurityError",
    "allow_egress",
    "assert_fake_token",
    "assert_no_secrets",
    "block_egress",
    "check_repo_url",
    "safe_join",
    "scrub",
    "SECRET_PATTERNS",
]
