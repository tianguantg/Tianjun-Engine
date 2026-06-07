# Coding Agent Instructions

This file applies to the whole repository unless a deeper directory contains its own `AGENTS.md`.

## Before Editing

- Read `CONTRIBUTING.md` before making changes.
- Keep changes small and focused; do not mix unrelated refactors with the requested work.
- When changing commands, interfaces, configuration, examples, or documentation structure, update the relevant documentation in the same change.
- Read and write Chinese text as UTF-8. If text appears garbled, stop and reopen it with UTF-8 before editing.

## Repository Hygiene

- Do not commit `.venv`, `target`, caches, generated files, machine-local files, credentials, API keys, or secrets.
- Do not revert user changes unless explicitly asked.
- Do not claim validation passed unless you actually ran the command.
- If a validation command cannot be run because a tool or environment is missing, state that clearly.

## Validation

For general changes, use the smallest relevant validation set. The standard validation commands are:

```powershell
python -m pytest
python scripts\smoke_test.py
python scripts\convergence_check.py
```

For CloudSimPlus-related changes, also check the Java example when JDK and Maven are available:

```powershell
cd examples\cloudsimplus
mvn clean compile
```

Alternatively, run the repository smoke wrapper from the repository root:

```powershell
.\scripts\cloudsimplus_smoke.ps1
```

If Maven or JDK is unavailable, explicitly state that Java validation was not run.
