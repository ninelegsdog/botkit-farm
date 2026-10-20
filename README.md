# botkit-farm

A test-case farm for Telegram bots. It takes the **real code** of a bot, runs scenarios
against it **without Telegram**, captures frames of the dialogue and writes bug
reports.

[![ci](https://github.com/ninelegsdog/botkit-farm/actions/workflows/ci.yml/badge.svg)](https://github.com/ninelegsdog/botkit-farm/actions/workflows/ci) [![mutation](https://github.com/ninelegsdog/botkit-farm/actions/workflows/mutation.yml/badge.svg)](https://github.com/ninelegsdog/botkit-farm/actions/workflows/mutation) [![release](https://github.com/ninelegsdog/botkit-farm/actions/workflows/release.yml/badge.svg)](https://github.com/ninelegsdog/botkit-farm/actions/workflows/release) [![License](https://img.shields.io/github/license/ninelegsdog/botkit-farm.svg)](LICENSE) [![PyPI](https://img.shields.io/pypi/v/botkit-farm.svg)](https://pypi.org/project/botkit-farm/)

> **Status.** Published to PyPI and green on CI, including a mutation-testing workflow.
One gap, stated rather than hidden: **there is no Dependabot configuration** in
this repository, so dependency updates are not proposed automatically.

## Contents

- [What this is for](#what-this-is-for)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [A test case](#a-test-case)
- [Commands](#commands)
- [`doctor`: what is wrong, and what to run](#doctor-what-is-wrong-and-what-to-run)
- [Documentation](#documentation)
- [Development process](#development-process)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

## What this is for

A test-case farm for Telegram bots. It takes the **real code** of a bot, runs scenarios
against it **without Telegram**, captures frames of the dialogue and writes bug
reports.

**The bot's code is real** — the farm clones the repository and boots its dispatcher as `main()` would.
**Message delivery is faked** — a local server stands in for `api.telegram.org`, so no token and no network are involved.
**Assertions live in the case** — a case states what the bot should answer, not whether it answered at all.

## Requirements

- Python 3.11 or newer
- Redis on port 6380 — throttling and the bot's FSM do not work without it: `redis-server --port 6380 --daemonize yes`
- Chromium for frame capture: `python -m playwright install chromium`

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```

## Quick start

```bash
# 1. check the stand is healthy
.venv/bin/botkit-farm doctor

# 2. read the bot: commands, buttons, build generation
.venv/bin/botkit-farm study ../bots/botkit-bookingbot

# 3. write a case in cases/booking.yaml, then
.venv/bin/botkit-farm run cases/booking.yaml

# every case at once
.venv/bin/botkit-farm run --tag smoke

# проверять свои правки, а не GitHub: URL кейса → локальный bots/<bot>
.venv/bin/botkit-farm run --local cases/store-checkout-creates-order.yaml
```

> **`--local` — что он закрывает.** Кейс указывает `source: https://...`, и
> ферма клонирует этот репозиторий (`--depth 1`) в песочницу. Значит обычный
> `run` проверяет **GitHub main**, а не то, что лежит у вас в рабочем дереве:
> правка бота остаётся непроверенной, а прогон зелёный. `--local` заменяет URL
> на существующий чекаут `bots/<bot>`; если чекаута нет, кейс **падает** — тихий
> откат на клон вернул бы ту же иллюзию.

## A test case

A case is YAML. This is the whole shape:

```yaml
id: booking-start          # [a-z0-9_-] only; becomes the report and frame name
title: Booking start menu
source: ../bots/botkit-bookingbot   # a local path, or an https/git URL
builder: newgen            # legacy | newgen | reminder, or a third-party adapter
raw: false                 # true — the bot parses the raw JSON itself
frame: window              # window | phone | wide

steps:
  - send: /start
    expect:
      text_contains: ["Booking"]
      buttons_contains: ["Book"]
  - click: Book
    pause: 2.5             # only if the bot throttles
    expect:
      text_contains: ["Choose a service"]
```

`expect` states what **must** be in the reply. An empty expectation is a
validation error: a case that checks nothing cannot fail, and therefore proves
nothing.

`builder` may be omitted — the generation is detected from the code. A bot with
its own composition root can point at a third-party adapter:

```yaml
builder: botkit-farm.adapter:mypkg.adapters.mine   # the module is installed into the bot's environment
```

`raw: true` hands the bot the same step as the raw JSON Telegram sends
(`feed_raw_update`), so the dialogue is identical and **deserialisation** is
tested too — the path from polling/webhook in production, and the most common
class of bug: the bot relies on a field the raw JSON does not contain.

## Commands

| Command | What it does |
|---|---|
| `doctor` | Checks the stand and prints what is wrong, why it matters, and the command to run |
| `study <repo>` | Prints the bot's commands, buttons, reply texts and build generation |
| `list` | Shows the cases and their sources |
| `init <repo>` | Creates a `.botkit-farm` pack skeleton in the bot's repository |
| `run [cases...]` | Runs cases, writes reports and frames (`--pack` for a pack's cases, `--junit` for a JUnit XML report) |
| `migrate [dir]` | Brings cases up to the current schema version (`--check` for CI) |
| `catalog [dir]` | Shows the regression catalogue (`--check` for CI, default `examples`) |

## `doctor`: what is wrong, and what to run

Every failure `doctor` reports is a "what", a "why this matters" and **the command
to run**. The checks are split into two levels, and the split is not decorative:

| Level | Meaning | Example |
|---|---|---|
| Blocker | the run will not start, or will go somewhere wrong; exit code 1 | Redis is not up, a real token is in the environment, there are no cases |
| Degradation | the checks pass and the verdict is correct, but the result is poorer; exit code 0 | no substrate or Chromium, so no frame is captured; no `reports/` directory, so one is created |

## Documentation

- **[docs/adapters/README.md](docs/adapters/README.md)** — Readme.
- **[docs/authoring-cases.md](docs/authoring-cases.md)** — Authoring Cases.
- **[docs/limitations.md](docs/limitations.md)** — Limitations.
- **[docs/security.md](docs/security.md)** — Security.
- **[README-testing.md](README-testing.md)** — the layers of tests (offline farm, live infra smokes, live Telegram) and the boundary between them (Russian).
- **[CHANGELOG.md](CHANGELOG.md)** — what changed, and when.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to set up, what CI checks.
- **[SECURITY.md](SECURITY.md)** — how to report a vulnerability.

## Development process

This project was built in an AI-native process: the implementation was produced by
coding agents under human-led engineering control — requirement slots, constraints
and acceptance criteria defined in advance, then specification, generation, run,
check, fix.

My role in the project:

- product definition and user-facing scenarios;
- decomposition into independent engineering stages;
- context engineering: the instructions, constraints and working rules agents run under;
- management of the context window between iterations;
- the loop of specification → generation → run → verification → correction;
- validation, testing and review;
- control of repository structure, configuration, documentation and run reproducibility.

Implementation code was generated with AI coding agents under human-led engineering
control. The full description of the process, the `AGENTS.md` template, and the
review checklists for AI-written code and secrets are in
[agentic-development-playbook](https://github.com/ninelegsdog/agentic-development-playbook).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: run `pytest`, run `ruff check .`, and explain why rather than what.

## Security

Do not open a public issue for a security problem. See [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
