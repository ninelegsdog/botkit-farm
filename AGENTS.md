# AGENTS.md

Rules for coding agents working in this repository. Read before changing code.

## What this project is

botkit-farm: a test harness. It runs the **real code of other repositories**
(the nine bots) offline — against a fake Bot API, in a sandbox, with its own
database fixtures — so a bot's behaviour can be checked without Telegram.

It is not a bot. It has no webhook, no token and no production traffic, and a
change here does not change something live. What it does touch is other
repositories' code: the farm loads it, runs it and reports on it — it never
edits it.

## Hard rules

These are enforced in CI (ci.yml, mutation.yml, release.yml). Do not work around them.

1. **Never commit secrets.** Use `.env`; keep `.env.example` free of real values.
   gitleaks scans the history, and a leaked token is an incident, not a revert.
2. **Never leave a test that needs the network unmarked.** A test that requires
   Telegram or an outbound connection is marked `req` and stays skipped in CI. An
   unmarked network test is a flaky red on someone else's runner.
3. **Never leave a floating promise.** A missing `await` on an aiogram call makes
   a test pass while asserting nothing.
4. **Never sleep.** No `time.sleep` in a test. Wait on a real condition, so the
   test retries instead of guessing.
5. **Never weaken a gate to make a build green.** Load-bearing: `ruff`,
   `ruff format --check`, `mypy` (strict), `bandit`, `gitleaks`, `Trivy`,
   `scripts/check-repo-contract.py`, `botkit-farm coverage --check cases`,
   `botkit-farm catalog --check examples`, `botkit-farm migrate --check`, and
   the sandbox check. `pre-commit` mirrors part of this locally; it is not a CI
   gate, so "pre-commit is green" does not stand in for the CI list.
6. **Never run foreign code outside the sandbox.** The farm executes other
   repositories' code. Isolation is `BOTKIT_FARM_SANDBOX=1` (bubblewrap), and
   CI checks that the sandbox actually works before the cases run — a sandbox
   that silently does not work means the farm runs unknown code unconfined.
   Never disable isolation to make a case pass; fix the case.
7. **Dependencies are declared in `pyproject.toml`.** CI installs
   `pip install -e ".[dev]"` and resolves versions afresh on every run: there
   is no `uv.lock` in this repository and CI does not check one. So a build can
   turn red without a single change here. When a dependency breaks the build,
   pin the version explicitly in `pyproject.toml` and say why in the pull
   request; do not rely on "it will resolve itself in CI".
8. **Never commit an evidence or run artifact.** `.env`, `*.session` and coverage
   output are output, not source.

## Before you finish

Run and show the output of:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/botkit_farm
.venv/bin/bandit -c pyproject.toml -r src/botkit_farm
python3 scripts/check-repo-contract.py . scripts/repo-contract.json
.venv/bin/botkit-farm migrate --check cases
.venv/bin/botkit-farm migrate --check examples
.venv/bin/botkit-farm catalog --check examples
.venv/bin/botkit-farm coverage --check cases
```

`uv run --frozen pytest -q` does **not** work in this repository: there is no
`uv.lock`, so `uv` exits with code 2 (`Unable to find lockfile`). CI installs
dependencies with pip, and the list above is the same set of gates CI runs.

**The contract gate takes the repository name from the directory name.** The
script looks up `contract["repos"][<directory name>]`, so it is green only where
the checkout directory is called `botkit-farm` — which is what CI sees, because
`actions/checkout` names the directory after the repository. In a clone or a
worktree under any other name it exits 1 with `repository '<name>' has no type
in the contract`, and the contract is fine: the lookup key was wrong. Outside CI,
run this gate in a checkout whose directory is named `botkit-farm`
(`git worktree add /tmp/verify/botkit-farm` does exactly that). The vendored
copy of the script is byte-identical to the canonical one in `qualityforge`, so
the fix belongs there — not here.

Do not report a task as done on the basis of a green build alone. Confirm the
observable behaviour: the test ran, and it failed when the thing it guards was
broken.

**Check the exit code, not a line of output.** Grepping for the string you expect
is a claim that you looked, not a check: it passes while the build is green and
stays silent when the build is red.

## Tests must be able to fail

Every new test gets a negative test: break the thing it guards and confirm it goes
red. A test that has never failed is unverified. The suite runs offline by default,
so this must be demonstrable without a Telegram account.

## Structure

`src/botkit_farm/` — the harness:

- `case/` — model, loader and migration of a case (`case/model.py`,
  `case/loader.py`, `case/migration.py`);
- `runner/` — execution of a case against the bot's code (`runner/engine.py`,
  `runner/host.py`, `runner/result.py`);
- `adapters/` — the three generations of bot builds (`legacy`, `newgen`,
  `reminder`) behind one contract (`adapters/contract.py`);
- `fixtures/` — database fixtures, one per bot;
- `inspector/` — parsing of a bot repository (`study`);
- `report/` — markdown report and JUnit XML;
- `fake_api.py` — the fake Bot API; `scene.py` — the stand; `sandbox.py` —
  isolation; `coverage.py` — coverage obligations; `catalog.py` — regression
  catalogue; `cli.py` — the `botkit-farm` command.

`cases/` — the corpus: YAML cases plus the `coverage.json` manifest.
`examples/` — regression examples plus the `catalog.json` catalogue.

Do not add a module that belongs in none of these without saying so in the
pull request.

## Style

- Python >=3.11, aiogram 3 (the fake Bot API speaks its protocol).
- Imports ordered: stdlib, then third-party, then local.
- Type hints on public functions.
- Line length ≤120. PEP 8, `ruff check .`.
- Conventional Commits: `feat`, `fix`, `docs`, `test`, `chore`, `security`, `refactor`.
- One concern per commit. A commit that fixes a bug and reformats a file is two
  commits.

## Parallel work

Several agents may work on this repository at once, but not in one working tree.
`CHANGELOG.md`, `AGENTS.md` and `pyproject.toml` are touched by most changes; two
agents in one tree collide on the first commit that touches documentation.

Give each agent its own worktree and branch. The integrator's verification is
serial and cannot be delegated — that is the real ceiling on how far this
repository parallelises.

## Sandbox and release

The farm executes foreign code: `BOTKIT_FARM_SANDBOX=1` puts it in a
bubblewrap namespace. CI installs bubblewrap and checks that it works before
any case runs — an unavailable sandbox is an error, not a quiet skip.

`release.yml` publishes the package to PyPI on a `v*` tag, via trusted
publishing (OIDC; no token lives in the repository). The version in
`pyproject.toml` must match the tag — bump it in the same commit as the
change, never afterwards.
