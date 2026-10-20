# Changelog

Все заметные изменения проекта собираются в этом файле.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/),
нумерация — [Semantic Versioning](https://semver.org/lang/ru/).

Схема кейса и формат отчёта — публичный API: их несовместимая поломка возможна
только в мажорной версии (версия схемы — поле `schema_version` в кейсе).

## [Unreleased]
### Added

- **`run --local` — прогон своих правок, а не GitHub.** Кейс указывает
  `source: https://...`, и ферма клонирует репозиторий: обычный `run` проверяет
  удалённый main, а правка в рабочем дереве остаётся непроверенной при зелёном
  прогоне. Флаг заменяет URL на существующий чекаут `bots/<bot>`; нет чекаута —
  кейс падает, а не тихо уходит в клон. Гейт `tests/test_local_source.py`
  держит, что у каждого URL-кейса чекаут есть.
- **Гейт покрытия ботов кейсами (`coverage --check`, ТЗ D-coverage).** Манифест
  `cases/coverage.json` объявляет обязательный поток каждого бота, а `positive`
  требует кейс на **работу**, не только на админ-гейт. Так нашёлся и закрыт
  пробел: у `support` не было счастливого пути — добавлен
  `support-ticket-lifecycle` (создание тикета и карточка в «Мои тикеты»).
  Вид кейса (`gate`/`positive`) берётся из поведения (`silent: true`), а не из
  заголовка.

- **Шаг `callback` — сырые `callback_data` (issue #6).** Кейс шлёт нажатие,
  которого бот в кнопках не предлагал: `click` жмёт только показанное и потому
  не видит подделку, а атака начинается там, где клиент посылает несуществующую
  кнопку (IDOR). Так впервые проверяется отказ на подделку `callback_data`.
  Шаг разрешён только в кейсах с тегом `security` или `edge` — тот же принцип,
  что allowlist в `ops/e2e`: недостижимое обычным кейсом действие проходит
  под контролем, а не по умолчанию.
- **Ферма тест-кейсов.** Прогон YAML-сценария по настоящему коду бота без
  Telegram: фейковый Bot API, кадр диалога и отчёт с фактами (ожидалось / пришло
  / на каком шаге). Пустое ожидание и кейс без проверок отвергаются.
- **B1.** Версия схемы кейса и миграции: сломанная схема ломает чужие
  репозитории, поэтому она версионируется явно.
- **B2.** Медиа: настоящие объекты Bot API (photo/document и др.), контракт,
  проверки в кейсах и отрисовка в кадре.
- **B3.** Типизированный контракт хост↔движок (`StepTurn`/`NoButtonTurn`/
  `CrashTurn` + golden-набор), чтобы хост и движок не расходились молча.
- **B4.** Мутационное тестирование критичных модулей (`case/model.py`,
  `runner/engine.py`, `security.py`) — 0 выживших мутантов.
- **B5.** Изоляция исполнения: карантин по whitelist `realpath`, песочница
  bubblewrap, egress только на loopback; в CI включается всегда.
- **B6.** `doctor` называет причину и команду на каждый блокер, отделяя блокеры
  от ухудшений.
- **C1.** Сторонние адаптеры через точку `botkit-farm.adapter` — без правки
  фермы.
- **C2.** Режим `raw`: боту отдаётся JSON как от Telegram (`feed_raw_update`).
- **C3.** Мультипользовательские кейсы (`actors`/`chat`/`as`): роли, группы,
  треды.
- **C4.** Режим webhook: update идёт HTTP POST'ом через сервер бота.
- **C5.** Регресс-каталог на публичных ботах (`examples` + `catalog --check`).
- **C6.** Шаблоны кейсов: `repeat`, `choice`, Jinja2 в песочнице с load-time
  whitelist переменных.
- **D1.** Параллельный прогон (`-n auto --dist worksteal`); состояние Redis
  изолировано по воркеру xdist.
- **D4.** Отчёт в JUnit XML — любая CI-система видит кейсы как тесты.
- **D5.** Кэш venv бота по хэшу `pyproject.toml`/`requirements.txt`: окружение
  переиспользуется, пока зависимости не менялись.
- **D6.** `CHANGELOG.md` по Keep a Changelog.
- **D2.** Матрица CI: Python 3.11/3.12/3.13 × Linux/macOS, `fail-fast: false`;
  линтеры вынесены в отдельный job. Windows и песочница на macOS описаны как
  известные ограничения.
- **D3.** Публикация на PyPI по тегу `v*` через trusted publishing (OIDC, без
  токена в репозитории): сборка sdist+wheel, `twine check --strict`, тесты,
  релиз на GitHub, загрузка с attestations (PEP 740). Гейт сверяет тег с
  версией в `pyproject.toml`.
- **Пак `.botkit-farm`.** Кейсы можно держать рядом с ботом; `--pack` переносит
  каталог кейсов в репозиторий бота.
- Рендер кадров диалога поверх снимка окна (портрет и макет), упавший кадр
  получает суффикс `-fail`.

- The repository presents itself as it is. The README became a front page — pitch,
  badges, a table of contents and user-facing sections — and issue forms plus a
  pull-request template now ask for the evidence this project acts on: what was
  run, what was expected, and how "done" would be able to fail.
- Governance layer brought to the family standard: `CONTRIBUTING.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, `AGENTS.md`, `.editorconfig`,
  `.gitattributes` and `CODEOWNERS`.
- `scripts/check-repo-contract.py` checks this repository against the shared
  contract, and `scripts/check-repo-contract_test.py` proves that checker can
  fail. A gate that has never gone red is an assumption, not a gate.
- Long-form documentation that already existed but was not linked is now linked from the front page: `docs/adapters/README.md`, `docs/authoring-cases.md`, `docs/limitations.md`, `docs/security.md`.

### Changed


- Окружение бота — отдельный venv на бота (у ботов разные зависимости), а не
  общее на ферму.

- Line endings are pinned to LF by `.gitattributes`, so a CRLF contributor no
  longer turns every touched line into a diff.

### Fixed


- `doctor` всегда показывал ложный DEGRADED по кадру: `run_checks` (async) звал
  `check_frame_support` с синхронным API Playwright прямо в работающем цикле, а
  тот падает с «Sync API inside the asyncio loop». Chromium при этом запускался.
  Блокирующая проверка теперь уходит в отдельный поток (`asyncio.to_thread`).
- Путь БД ломался при относительном `bot_dir`: `work_dir` теперь передаётся
  явно, а `bot_dir` приводится к абсолютному пути.
- Всплывающие уведомления (`answerCallbackQuery`) терялись и не попадали в
  проверки; теперь собираются отдельно, попадают в отчёт и в кадр.
- Устранено дублирование тоста обычным сообщением в ленте диалога.

- `README-testing.md` was unreachable from the README in every bot of the family
  except one. A document nobody can reach is a document nobody reads; it is linked
  from the front page now.

### Security


- Настоящий токен Telegram останавливает прогон; секреты вычищаются из отчётов.
- Во время прогона egress заблокирован (только loopback).
