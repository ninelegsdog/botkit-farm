"""Проверки безопасности: токен, egress, песочница, секреты.

Здесь каждая проверка обязана уметь падать, иначе она ничего не охраняет.
Поэтому у security-кода обязателен «контроль»: положительный пример (код
проверки пропускает) и отрицательный (код проверки ловит).
"""

from __future__ import annotations

import socket

import pytest

from botkit_farm import security

REAL_TOKEN = "1234567890:AAH1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # noqa: S105 — фикстура для проверки, не секрет
FAKE = "424242:FAKE-TOKEN-FOR-LOCAL-STAND"


# ── токен ───────────────────────────────────────────────────────────────────


def test_настоящий_токен_останавливает_прогон():
    with pytest.raises(security.SecurityError) as e:
        security.assert_fake_token({"TELEGRAM_BOT_TOKEN": REAL_TOKEN})
    assert "настоящий токен" in str(e.value)


def test_фейковый_токен_проходит():
    security.assert_fake_token({"TELEGRAM_BOT_TOKEN": FAKE})


def test_токен_под_другим_именем_тоже_ловится():
    """Ключ мог остаться не в TELEGRAM_BOT_TOKEN. Проверка только одного имени
    была бы фикцией защиты."""
    with pytest.raises(security.SecurityError):
        security.assert_fake_token({"SOME_LEFTOVER_KEY": REAL_TOKEN})


def test_не_строки_в_окружении_не_ломают_проверку():
    """os.environ — строки, но вызывающий код может передать что угодно."""
    security.assert_fake_token({"WEIRD": 42, "OTHER": None})


def test_похожие_не_токены_не_дают_ложной_тревоги():
    """Проверка не должна ругаться на Base64/хэши — иначе ею перестанут
    пользоваться, отключив её одним флагом."""
    security.assert_fake_token(
        {
            "HASH": "a" * 64,
            "B64": "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=",
            "NUM": "12345678901234567890",
        }
    )


# ── egress ──────────────────────────────────────────────────────────────────


def test_egress_блокирует_внешний_хост():
    guard = security.block_egress()
    try:
        with pytest.raises(security.SecurityError) as e:
            socket.create_connection(("example.com", 80), timeout=1)
        assert "локальному" in str(e.value) or "заблокирована" in str(e.value)
    finally:
        security.allow_egress()
    assert guard.blocked


def test_loopback_остаётся_разрешённым():
    """Заблокировать 127.0.0.1 — значит заблокировать фейковый API и Redis,
    то есть сломать всю ферму."""
    security.block_egress()
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        # Соединение в разрешённый loopback должно дойти до сервера.
        client = socket.socket()
        client.connect(("127.0.0.1", port))  # не должно бросить SecurityError
        client.close()
        s.close()
    finally:
        security.allow_egress()


def test_egress_снимается():
    security.block_egress()
    security.allow_egress()
    # После снятия защиты настоящее подключение возможно — но мы его не делаем;
    # проверяем лишь, что флаг снят и connect вернулся к системной функции.
    assert security._guard.enabled is False


# ── песочница путей ─────────────────────────────────────────────────────────


def test_выход_из_песочницы_запрещён(tmp_path):
    with pytest.raises(security.SecurityError):
        security.safe_join(tmp_path, "..", "..", "etc")


def test_абсолютный_путь_не_подставляется(tmp_path):
    with pytest.raises(security.SecurityError):
        security.safe_join(tmp_path, "/etc/passwd")


def test_нормальный_путь_проходит(tmp_path):
    got = security.safe_join(tmp_path, "cases", "booking.yaml")
    assert got == (tmp_path / "cases" / "booking.yaml").resolve()


# ── URL репозитория ─────────────────────────────────────────────────────────


def test_подозрительная_схема_отклоняется(tmp_path):
    with pytest.raises(security.SecurityError) as e:
        security.check_repo_url("file:///etc/shadow", tmp_path)
    assert "схема" in str(e.value)


def test_https_разрешён(tmp_path):
    security.check_repo_url("https://github.com/a/b", tmp_path)  # не должно бросить


# ── секреты ──────────────────────────────────────────────────────────────────


def test_секреты_вычищаются_из_отчёта():
    text = f"бот ответил: мой токен {REAL_TOKEN} и пароль hunter2secret"
    clean, found = security.scrub(text)
    assert REAL_TOKEN not in clean
    assert found
    assert "[скрыто фермой]" in clean


@pytest.mark.parametrize(
    "secret",
    [
        "-----BEGIN RSA PRIVATE KEY-----",
        "AKIAIOSFODNN7EXAMPLE",
        "api_hash: 1234567890",
        "sk-live-abcdefghijklmnop",
    ],
)
def test_разные_виды_секретов_ловятся(secret):
    with pytest.raises(security.SecurityError):
        security.assert_no_secrets(f"найдено: {secret}", "отчёт")


def test_чистый_текст_проходит():
    security.assert_no_secrets("обычный текст ответа бота без секретов", "отчёт")


# ── имя репозитория из URL ──────────────────────────────────────────────────


def test_имя_репозитория_не_уходит_из_песочницы():
    from botkit_farm.source import _repo_name

    got = _repo_name("https://github.com/evil/../../etc/passwd.git")
    assert "/" not in got and ".." not in got


def test_имя_из_ssh_формы():
    from botkit_farm.source import _repo_name

    assert _repo_name("git@github.com:ninelegsdog/botkit-store.git") == "botkit-store"
