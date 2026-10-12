"""Тесты карантина: бот получает отказ, ферма получает диагностику.

Проверка не «упало ли», а «что именно было запрещено и что об этом узнали»:
молчаливый запрет хуже отсутствия запрета — бот упал бы по другой причине, и
владелец гадал бы не о том.
"""

from __future__ import annotations

import os
import pathlib
import socket
import tempfile
from collections.abc import Iterator

import pytest

from botkit_farm.quarantine import Quarantine, Violation, arm, default_roots


def _home(name: str) -> pathlib.Path:
    """Путь в настоящем домашнем каталоге: писать туда бот не должен.

    Имя уникально на каждый тест: если проверка не сработает, тест обязан
    оставить след, а не спорить с файлом от прошлого запуска.
    """
    return pathlib.Path(os.path.expanduser("~")) / f"ferma-test-{os.getpid()}-{name}"


@pytest.fixture
def workdir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Каталог бота с базой — то, куда писать можно.

    Соседний `bot-backup` создаётся здесь же, до установки карантина: иначе
    `mkdir` самого соседа был бы запрещён, и тест проверял бы не то.
    """
    bot = tmp_path / "bot"
    bot.mkdir()
    (bot / "state.db").write_bytes(b"")
    (tmp_path / f"{bot.name}-backup").mkdir()
    return bot


@pytest.fixture
def guard(workdir: pathlib.Path) -> Iterator[Quarantine]:
    """Карантин, снятый после теста: перехваты бьют по всему процессу."""
    q = Quarantine([workdir]).install()
    try:
        yield q
    finally:
        q.disarm()


# ── запись ──────────────────────────────────────────────────────────────────


def test_в_каталог_бота_пишут_свободно(guard: Quarantine, workdir: pathlib.Path) -> None:
    (workdir / "new.db").write_text("данные", encoding="utf-8")

    assert (workdir / "new.db").read_text(encoding="utf-8") == "данные"
    assert guard.violations == []


def test_чтение_везде_разрешено(guard: Quarantine) -> None:
    """Бот обязан читать свои библиотеки и .env — это не запись.

    Читается файл самого теста, а не `/etc/hostname`: на macOS такого файла
    нет, и проверка падала бы не потому, что карантин сломан, а потому что
    платформа другая.
    """
    with open(__file__, encoding="utf-8") as fh:  # noqa: PTH123
        fh.read()

    assert guard.violations == []


def test_запись_в_домашний_каталог_запрещена(guard: Quarantine) -> None:
    """Г: бот, пишущий в $HOME, получает отказ, а ферма — запись о нарушении."""
    home = _home("ferma-should-not-write.txt")
    with pytest.raises(PermissionError) as err:
        home.write_text("взлом", encoding="utf-8")

    assert str(home) in str(err.value)
    assert not home.exists()
    assert [v.op for v in guard.violations] == ["open"]
    assert str(home) in guard.violations[0].path


def test_отказ_называет_разрешённые_каталоги(guard: Quarantine, workdir: pathlib.Path) -> None:
    """Отказ без подсказки бесполезен: владелец не знает, куда можно."""
    with pytest.raises(PermissionError) as err:
        _home("x.txt").write_text("", encoding="utf-8")

    assert str(workdir) in str(err.value)
    assert "BOTKIT_FARM_QUARANTINE" in str(err.value)


def test_выход_из_каталога_точками_запрещён(guard: Quarantine, workdir: pathlib.Path) -> None:
    """`../` не обходит проверку: realpath разрешает его до сравнения."""
    escape = workdir / ".." / ".." / "ferma-escape.txt"

    with pytest.raises(PermissionError):
        escape.write_text("мимо", encoding="utf-8")

    assert not escape.resolve().exists()


def test_os_open_на_запись_запрещён(guard: Quarantine) -> None:
    target = _home("ferma-os-open.db")

    with pytest.raises(PermissionError):
        os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o600)

    assert not target.exists()
    assert [v.op for v in guard.violations] == ["os.open"]


def test_os_open_на_чтение_разрешён(guard: Quarantine, workdir: pathlib.Path) -> None:
    fd = os.open(str(workdir / "state.db"), os.O_RDONLY)
    try:
        assert fd >= 0
    finally:
        os.close(fd)


def test_mkdir_вне_каталога_запрещён(guard: Quarantine) -> None:
    target = _home("ferma-mkdir")

    with pytest.raises(PermissionError):
        target.mkdir()

    assert not target.exists()
    assert [v.op for v in guard.violations] == ["mkdir"]


def test_makedirs_внутри_каталога_бота_разрешён(guard: Quarantine, workdir: pathlib.Path) -> None:
    (workdir / "data" / "nested").mkdir(parents=True)

    assert (workdir / "data" / "nested").is_dir()


# ── симлинки и соседи ───────────────────────────────────────────────────────


def test_симлинк_из_каталога_наружу_не_пролез(guard: Quarantine, workdir: pathlib.Path) -> None:
    """Проверяется реальный путь, поэтому ссылка наружу не помогает."""
    link = workdir / "out"
    link.symlink_to(os.path.expanduser("~"))

    with pytest.raises(PermissionError):
        (link / "ferma-through-link.txt").write_text("мимо", encoding="utf-8")


def test_соседний_каталог_с_общим_префиксом_не_проходит(
    guard: Quarantine, workdir: pathlib.Path
) -> None:
    """`bot-backup` не считается подкаталогом `bot`: сравнение по границе."""
    sibling = workdir.parent / f"{workdir.name}-backup"

    with pytest.raises(PermissionError):
        (sibling / "x.txt").write_text("мимо", encoding="utf-8")

    assert not (sibling / "x.txt").exists()


# ── сеть ────────────────────────────────────────────────────────────────────


def test_подключение_наружу_запрещено(guard: Quarantine) -> None:
    with socket.socket() as sock, pytest.raises(PermissionError) as err:
        sock.connect(("example.com", 80))

    assert "офлайн" in str(err.value)
    assert [v.op for v in guard.violations] == ["connect"]


def test_подключение_к_локальному_порту_разрешено(guard: Quarantine) -> None:
    """Фейковый Bot API и Redis стоят на loopback — их карантин не трогает."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        with socket.socket() as sock:
            sock.connect(("127.0.0.1", port))
    finally:
        server.close()

    assert guard.violations == []


def test_подключение_к_unix_сокету_запрещено(guard: Quarantine) -> None:
    """Адрес unix-сокета — это путь в файловой системе, а не loopback.

    Блокировка здесь правильная по другой причине: такой путь почти наверняка
    лежит вне рабочих каталогов, и запись в него — ровно то, что запрещено.
    """
    path = _home("ferma.sock")
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind(str(path))
        server.listen(1)
        with socket.socket(socket.AF_UNIX) as sock, pytest.raises(PermissionError):
            sock.connect(str(path))
    finally:
        server.close()
        path.unlink(missing_ok=True)

    assert [v.op for v in guard.violations] == ["connect"]


def test_create_connection_наружу_запрещено(guard: Quarantine) -> None:
    with pytest.raises(PermissionError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)

    assert [v.op for v in guard.violations] == ["connect"]


def test_create_connection_в_сеть_заблокирован_до_обращения(guard: Quarantine) -> None:
    """Проверка должна срабатывать до connect, а не после таймаута."""
    with pytest.raises(PermissionError) as err:
        socket.create_connection(("example.com", 443), timeout=30)

    assert "example.com" in str(err.value)


# ── состояние и отчёт ───────────────────────────────────────────────────────


def test_нарушения_переживают_отказ_и_читаются(guard: Quarantine) -> None:
    for _ in range(2):
        with pytest.raises(PermissionError):
            _home("y.txt").write_text("", encoding="utf-8")

    assert len(guard.violations) == 2
    assert len(guard.summary()) == 2


def test_список_нарушений_одной_строкой(guard: Quarantine) -> None:
    with pytest.raises(PermissionError):
        _home("z.txt").write_text("", encoding="utf-8")

    assert guard.summary() == [str(guard.violations[0])]
    assert "open →" in guard.summary()[0]


def test_disarm_возвращает_право_записи(guard: Quarantine) -> None:
    guard.disarm()
    target = _home("ferma-after-disarm.txt")
    target.write_text("уже можно", encoding="utf-8")
    target.unlink()


def test_повторная_установка_не_оборачивает_обёртку(guard: Quarantine) -> None:
    guard.install()

    with pytest.raises(PermissionError):
        _home("again.txt").write_text("", encoding="utf-8")

    assert len(guard.violations) == 1


def test_describe_печатает_разрешённые_каталоги(guard: Quarantine, workdir: pathlib.Path) -> None:
    text = guard.describe()

    assert str(workdir) in text
    assert "запись разрешена только в" in text


def test_violation_читается_через_str() -> None:
    assert str(Violation(op="open", path="/spool/x", detail="режим 'w'")) == (
        "open → /spool/x: режим 'w'"
    )
    assert str(Violation(op="connect", path="1.2.3.4:80")) == "connect → 1.2.3.4:80"


# ── корни и края ────────────────────────────────────────────────────────────


def test_корни_включают_каталог_бота_и_временный_каталог(workdir: pathlib.Path) -> None:
    roots = default_roots(workdir)

    assert str(workdir.resolve()) in roots
    assert str(pathlib.Path(tempfile.gettempdir()).resolve()) in roots


def test_корни_расширяются_переменной_окружения(workdir: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("BOTKIT_FARM_QUARANTINE", "/opt/данные")

    assert "/opt/данные" in default_roots(workdir)


def test_arm_сразу_закрывает_запись(workdir: pathlib.Path) -> None:
    q = arm(workdir)
    try:
        with pytest.raises(PermissionError):
            _home("arm.txt").write_text("", encoding="utf-8")
    finally:
        q.disarm()

    assert q.violations


def test_пустой_список_корней_запрещает_всё() -> None:
    q = Quarantine([]).install()
    try:
        with pytest.raises(PermissionError):
            (pathlib.Path.cwd() / "nope.txt").write_text("", encoding="utf-8")
    finally:
        q.disarm()

    assert len(q.violations) == 1


def test_дескриптор_пропускается_без_проверки(guard: Quarantine, workdir: pathlib.Path) -> None:
    """Открытый ранее файл не имеет имени — проверять нечего."""
    payload = "через дескриптор".encode()
    fd = os.open(str(workdir / "state.db"), os.O_RDWR)
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)

    assert guard.violations == []
    assert (workdir / "state.db").read_bytes() == payload


def test_нечитаемый_путь_не_проходит_молча() -> None:
    assert Quarantine(["/tmp"]).allows("\x00") is False  # noqa: S108 — путь не используется


def test_принимаются_строки_и_пути(tmp_path: pathlib.Path) -> None:
    q = Quarantine([tmp_path])

    assert q.allows(tmp_path / "x") is True
    assert q.allows("/etc/passwd") is False
