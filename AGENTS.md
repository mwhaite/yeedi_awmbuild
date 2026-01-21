# Repository Guidelines

## Project Structure & Module Organization
- Current repository state is planning-focused: `project_state/` holds the specification, MVP contract, and stack constraints; `generated_prompts/` contains prompt artifacts.
- The intended Home Assistant integration layout (per `project_state/execution_shape.yaml`) is:
  - `custom_components/yeedi/` for the integration code (`api.py`, `config_flow.py`, `coordinator.py`, `vacuum.py`, etc.)
  - `custom_components/yeedi/translations/en.json` for UI strings
  - `tests/` for unit tests (optional at MVP)
  - `README.md` and `pyproject.toml` at repo root for docs and dev tooling

## Build, Test, and Development Commands
- No build/test commands are defined yet. When the integration code lands, typical Home Assistant workflows apply (e.g., run the Core environment and load `custom_components/yeedi`).
- If/when tests are added under `tests/`, run them with `pytest` from the repo root.

## Coding Style & Naming Conventions
- Follow Home Assistant patterns: async-first, use `DataUpdateCoordinator`, and keep I/O in `api.py`.
- Indentation: 4 spaces; filenames use snake_case (`config_flow.py`, `vacuum.py`).
- Keep secrets out of logs and diagnostics; redact tokens and device IDs where appropriate.
- If linting is added, prefer `ruff` and optional `mypy` as dev tooling (no runtime pins).

## Testing Guidelines
- Target unit tests for: API response parsing, token refresh logic, and diagnostics redaction.
- Suggested naming: `tests/test_api.py`, `tests/test_diagnostics.py`.
- Minimum expectation: tests should not require real Yeedi credentials.

## Commit & Pull Request Guidelines
- No Git history exists yet, so no established commit convention. Recommend Conventional Commits (e.g., `feat: add config flow`, `fix: redact tokens in logs`).
- PRs should include a short summary, linked issues if applicable, and a test note (e.g., `pytest` run or manual HA verification).

## Security & Configuration Tips
- Store credentials/tokens only in Home Assistant config entries; never in source or logs.
- Assume region-specific endpoints; keep base URL selection configurable.
