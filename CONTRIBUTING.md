# Contributing to Tianjun Engine

Tianjun Engine is a research and architecture prototype. Contributions should keep the project easy to review, easy to run locally, and easy to explain through its documentation.

## Before changing code

Prefer small, focused changes. A pull request should usually have one clear purpose, such as fixing a bug, extracting a service, adding an endpoint, updating the Dashboard, or improving documentation.

Before editing, identify the affected maintenance surface:

* CLI entry points and commands
* HTTP API and compatibility routes
* Dashboard static assets
* Control-plane facade and application services
* Scheduling, policy, node, lease, or execution flows
* MCP integration
* Tests, scripts, configuration, or model/data assets

Avoid mixing unrelated refactors with behavior changes.

## Keep documentation maintained

When a change affects how the project is used, reviewed, or understood, update the relevant documentation in the same change.

Check these files when applicable:

* `README.md` for quick start, core entry points, repository layout, and high-level project positioning
* `docs/README.md` for the documentation index
* `docs/architecture.md` for subsystem boundaries and control-plane responsibilities
* `docs/api.md` for HTTP API behavior
* `docs/deprecation.md` for compatibility routes and migration guidance
* `docs/security-boundary.md` for confirmation, execution, secrets, and safety boundaries
* `docs/dashboard-test-checklist.md` for Dashboard behavior that needs manual verification
* `docs/experiments-dci.md` for experiment data, model assets, and reproducibility notes
* `docs/convergence-checklist.md` for the final architecture convergence validation commands and static checks
* `AGENTS.md` for coding-agent instructions, validation claims, encoding rules, and repository hygiene

Documentation does not need to be long. Prefer short updates that keep future readers from guessing why a code path exists or how it should be used.

This file is also part of the maintained documentation. If the contribution process changes, update `CONTRIBUTING.md` together with the code or workflow change.

When contribution rules, validation commands, encoding requirements, documentation layout, generated-file rules, or agent instructions change, update both `CONTRIBUTING.md` and `AGENTS.md` as needed.

## Coding agents

If you are an AI coding agent or automated editing tool, read `AGENTS.md` before making changes.

`CONTRIBUTING.md` describes the human contribution and review process. `AGENTS.md` contains operational rules for automated edits, encoding, validation claims, and repository hygiene.

## Architecture expectations

Keep `CentralControlPlane` as a stable facade for HTTP, chat, MCP, and tests. Do not move already-extracted business flows back into the facade.

When adding or changing behavior, prefer the existing application-service boundaries:

* node registration and heartbeat lifecycle
* task submission, scheduling, leases, progress, and results
* requirement dialogue lifecycle
* policy draft, comparison, simulation, commit, and feedback
* CLI command handlers

New public behavior should have a clear owner and should not duplicate logic across HTTP, CLI, chat, MCP, and tests.

## Review-friendly changes

Make changes easy to inspect:

* Keep diffs narrow and named clearly.
* Add or update tests for changed behavior.
* Update examples when commands, configuration, routes, or response shapes change.
* Remove dead code only when the replacement path is clear.
* Keep compatibility behavior documented when old routes or wrappers remain available.
* Do not commit secrets, local credentials, generated caches, or machine-specific files.

## Encoding

All project files that contain Chinese text must be saved as UTF-8.

When using command-line tools, scripts, editors, or coding agents to inspect or modify the repository, make sure Chinese text is read and written with UTF-8 encoding. If Chinese content appears garbled, do not edit based on the garbled output. Re-open the file using UTF-8 or adjust the tool, shell, terminal, or script encoding first.

For Python scripts, prefer explicit UTF-8 file access when reading or writing text files that may contain Chinese:

```python
from pathlib import Path

text = Path("README.md").read_text(encoding="utf-8")
Path("README.md").write_text(text, encoding="utf-8")
```

For PowerShell, prefer UTF-8-aware commands when writing files:

```powershell
Get-Content README.md -Encoding UTF8
Set-Content README.md -Value $content -Encoding UTF8
```

Do not replace Chinese text with mojibake or remove it just because a local tool displayed it incorrectly.

## Local validation

Use the smallest validation set that matches the change. For general changes, run:

```powershell
python -m pytest
python scripts\smoke_test.py
python scripts\convergence_check.py
```

For Dashboard, HTTP, or node-runtime behavior, also perform the relevant manual checks and update the checklist when behavior changes. README must keep the complete local startup path current, including the control-plane server and the simulated node backend.

## Pull request notes

A useful pull request description should answer:

* What changed?
* Why was it needed?
* What user-facing behavior, API, documentation, or compatibility surface changed?
* What validation was run?
* Which documentation was updated, or why no documentation update was needed?

The goal is not more process. The goal is to keep Tianjun understandable as the codebase grows.
