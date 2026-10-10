"""Пак подключения бота к ферме: `bot.yaml`, манифест и каркас пака.

Пак — это каталог `.botkit-farm` внутри репозитория бота. Он несёт один файл
`bot.yaml` (манифест: имя бота, поколение сборки, env, режим кадра) и три
каталога: `cases/manual`, `cases/generated`, `fixtures`. Смысл — чтобы репозиторий
бота хранил свои кейсы рядом с кодом; ферма находит пак по `.botkit-farm/bot.yaml`
и берёт кейсы из него, а не из своего `cases/`.

Манифест версионируется (`schema_version`): формат `bot.yaml` — публичный
контракт, и его изменение без версии сломало бы чужие паки молча.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from botkit_farm.inspector.repo import detect_builder

# Имена фиксированы: их ищет `resolve_pack_path`, и опечатка здесь означала бы,
# что пак есть, но ферма его «не видит».
PACK_DIRNAME = ".botkit-farm"
MANIFEST_NAME = "bot.yaml"

# Подкаталоги каркаса. Разделение manual/generated — не косметика: кейс,
# написанный человеком, и кейс, сгенерированный агентом, правят по-разному.
CASE_SUBDIRS = ("manual", "generated")


class BotCfg(BaseModel):
    """Описание бота в манифесте: имя и как его собирать.

    `builder`, `entry`, `returns`, `state_fields` — подсказки адаптеру сборки;
    `reason` хранит, почему поколение определено именно так (для диагностики,
    когда адаптер не найдёт свои символы).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    builder: str | None = None
    entry: str | None = None
    returns: str | None = None
    state_fields: list[str] | None = None
    reason: str | None = None


class FrameCfg(BaseModel):
    """Как снимать кадр для кейсов этого бота."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "window"
    title: str | None = None
    handle: str | None = None


class Manifest(BaseModel):
    """Содержимое `bot.yaml`: версия схемы, бот, Redis-БД, env и режим кадра."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    bot: BotCfg
    redis_db: int = 0
    env: dict[str, str] = Field(default_factory=dict)
    frame: FrameCfg = Field(default_factory=FrameCfg)


@dataclass(frozen=True)
class Pack:
    """Найденный пак: корень `.botkit-farm` и разобранный манифест."""

    root: pathlib.Path
    manifest: Manifest

    @property
    def cases_dir(self) -> pathlib.Path:
        """Каталог кейсов пака (`cases/`)."""
        return self.root / "cases"

    @property
    def manual_dir(self) -> pathlib.Path:
        """Каталог кейсов, написанных человеком."""
        return self.cases_dir / "manual"

    @property
    def generated_dir(self) -> pathlib.Path:
        """Каталог кейсов, сгенерированных агентом."""
        return self.cases_dir / "generated"

    @property
    def fixtures_dir(self) -> pathlib.Path:
        """Каталог фикстур пака."""
        return self.root / "fixtures"

    @property
    def bot_dir(self) -> pathlib.Path:
        """Каталог репозитория бота, содержащий этот пак.

        Пак лежит в `.botkit-farm` внутри репозитория, поэтому корень бота —
        родитель каталога пака. Для пака, положенного отдельно, — сам его корень.
        """
        if self.root.name == PACK_DIRNAME:
            return self.root.parent
        return self.root


def resolve_pack_path(
    target: str | pathlib.Path,
    farm_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """Найти корень пака для цели.

    Принимает: сам каталог `.botkit-farm`, корень репозитория бота (внутри
    которого есть `.botkit-farm`), каталог с `bot.yaml` напрямую или имя пака в
    `farm_root/packs/`. Неоднозначно только имя — путь разрешается по факту
    наличия `bot.yaml`.
    """
    t = pathlib.Path(target).expanduser().resolve()
    if t.name == PACK_DIRNAME and (t / MANIFEST_NAME).is_file():
        return t
    nested = t / PACK_DIRNAME
    if (nested / MANIFEST_NAME).is_file():
        return nested
    if (t / MANIFEST_NAME).is_file():
        return t
    if farm_root is not None:
        cand = farm_root / "packs" / t.name
        if (cand / MANIFEST_NAME).is_file():
            return cand
    raise FileNotFoundError(f"пак не найден для {target}")


def load_pack(target: str | pathlib.Path, farm_root: pathlib.Path | None = None) -> Pack:
    """Загрузить пак и провалидировать `bot.yaml`.

    Битый манифест — понятная ошибка с корнем пака, а не стек pydantic: пак
    пишут руками, и «поле bot.name обязательно» полезнее, чем traceback.
    """
    root = resolve_pack_path(target, farm_root=farm_root)
    mf = root / MANIFEST_NAME
    raw = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"невалидный {MANIFEST_NAME} в {root}: {exc}") from exc
    return Pack(root=root, manifest=manifest)


def scaffold_pack(
    bot_root: str | pathlib.Path,
    *,
    farm_root: pathlib.Path | None = None,
    builder: str | None = None,
) -> Pack:
    """Создать каркас пака в `bot_root/.botkit-farm` и вернуть загруженный Pack.

    Идемпотентен: существующий `bot.yaml` дополняется недостающими полями, но
    уже заданные значения не перезатираются — повторный запуск не портит ручные
    правки. Поколение сборки определяется по структуре репозитория, если не
    задано явно.
    """
    br = pathlib.Path(bot_root).expanduser().resolve()
    br.mkdir(parents=True, exist_ok=True)
    root = br / PACK_DIRNAME
    for sub in CASE_SUBDIRS:
        (root / "cases" / sub).mkdir(parents=True, exist_ok=True)
    (root / "fixtures").mkdir(parents=True, exist_ok=True)

    reason: str | None = None
    if builder is None:
        builder, reason = detect_builder(br)

    name = br.name
    mf = root / MANIFEST_NAME
    if mf.is_file():
        existing = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
        existing.setdefault("schema_version", 1)
        bot = existing.setdefault("bot", {})
        bot.setdefault("name", name)
        if builder and not bot.get("builder"):
            bot["builder"] = builder
        if reason and not bot.get("reason"):
            bot["reason"] = reason
        existing.setdefault("frame", {"mode": "window"})
        mf.write_text(
            yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    else:
        manifest = Manifest(bot=BotCfg(name=name, builder=builder, reason=reason))
        mf.write_text(
            yaml.safe_dump(
                manifest.model_dump(mode="json", exclude_none=True),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    return load_pack(root, farm_root=farm_root)


__all__ = [
    "MANIFEST_NAME",
    "PACK_DIRNAME",
    "BotCfg",
    "FrameCfg",
    "Manifest",
    "Pack",
    "load_pack",
    "resolve_pack_path",
    "scaffold_pack",
]
