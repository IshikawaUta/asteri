# Contributing to Asteri

Thanks for your interest in contributing! Asteri is built on a high-quality bar: **100% test coverage**, strict `mypy` typing, and a `ruff`-clean codebase. This guide keeps that bar reachable.

## Development setup

```bash
git clone https://github.com/IshikawaUta/asteri.git
cd asteri
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pip install pytest pytest-cov coverage ruff mypy types-psutil types-setuptools
```

Optional integrations used by examples/tests:

```bash
pip install flask fastapi tornado gunicorn uvicorn gevent watchdog h2
```

## Before you open a Pull Request

1. Create a feature branch from `main`.
2. Make your change, keeping the code style consistent with the surrounding code.
3. Run the full quality gate — all three must pass:

   ```bash
   ruff check .
   mypy asteri tests
   pytest tests/ -q --cov=asteri --cov-report=term-missing
   ```

4. Add or update tests for any new behavior and keep coverage at **100%**
   (`ruff` may also flag unused imports in tests — remove them).
5. If you change the CLI or public behavior, update the README and `CHANGELOG.md`.
6. Run the CLI regression suite if your change touches argument handling or the arbiter:

   ```bash
   ./test_asteri_cli.sh
   ```

## Code style

- Follow PEP 8 (enforced by `ruff`).
- Type-annotate everything; do not add `# type: ignore` unless truly necessary
  (untyped third-party imports are the common exception).
- Do not add comments unless they clarify non-obvious logic.
- Match the existing error-handling and logging patterns.

## Commit convention

Write clear, imperative commit messages (e.g. `fix: drain keep-alive on shutdown`).
Small, focused commits are preferred over large mixed ones.

## Testing notes

- The suite is plain `pytest` plus a consolidated import-fallback test file
  (`tests/test_zz_import_fallbacks.py`) that deliberately reloads modules, so it
  must stay alphabetically last.
- Coverage is tracked in `coverage.json`; regenerate it after changing coverage
  so the `100%` badge and Codecov stay accurate.

## Questions?

Open a GitHub issue or start a discussion. For security issues, use the policy
in [SECURITY.md](SECURITY.md).
