# Agent contributor guide

Inspect the relevant code and tests before editing. Keep changes focused and
preserve work created by others. Use one branch per task in an isolated checkout.
Do not push, merge, deploy, install dependencies, or modify external state without
authorization.

Cutgraph accepts issues and discussions, not code pull requests while the contributor
licence agreement is pending. See [CONTRIBUTING](CONTRIBUTING.md).
Start with [Getting started](docs/getting-started.md) and
[Architecture](docs/architecture.md).

## Layout

- `scripts/studio_app.py`: loopback launcher for the FastAPI application.
- `studio/`: application assembly, route handlers, compute workers and events.
- `pipeline/`: video documents, editing, library, co-pilot and rendering logic.
- `ui/src/v3/`: Home, Workspace, Library, Settings and Story views.
- `ui/src/`: shared stores, persistence and preview.
- `motion-core/`: TypeScript drawing engine shared by preview and export.
- `schema/`: generated video-document schema.
- `tests/`: product tests and the fixture-based browser harness.
- `config.py`, `runtime.py`: application constants and local state paths.

## Conventions

A Workspace groups Videos; use those terms in user-facing text. Match surrounding
code, keep comments for non-obvious reasons, and delete dead code rather than
adding compatibility shims. Put domain logic in `pipeline/` and keep route handlers
small. Edits must preserve revision checks and preview/export parity.

`pipeline/project.py` and `scripts/export_schema.py` own the document schema.
After a model change, run `python scripts/export_schema.py` and
`npm --prefix ui run codegen`; never hand-edit `ui/src/types/project.ts`.

No issue references, machine or user names, absolute personal paths, or
brand/client names in code, comments, docstrings, tests, fixtures or docs;
history belongs in commit and PR text.

## Gates

For setup, create a virtual environment and install with
`python -m pip install --require-hashes -r requirements-dev.txt`. The full
development lock includes `requirements-core.txt` and `requirements-agent.txt`;
use those individual locks with `--require-hashes` for a smaller installation.
Optional transcription uses
`python -m pip install --require-hashes -r requirements-local-ai-nvidia.txt --extra-index-url https://download.pytorch.org/whl/cu128`.
Exact versions and SHA-256 hashes restrict that extra index to reviewed CUDA
artifacts. See `docs/dependency-audit.md` for regeneration and audit exceptions.

Use the repository's Python environment. Run each command separately and stop
on a nonzero exit code:

```text
python -m pytest -q -p no:cacheprovider
python -m ruff check .
python -m pyright <changed Python files>
npm --prefix ui test
npm --prefix ui run typecheck
npm --prefix ui run build
python tests/e2e/run.py
```

From `motion-core/` in PowerShell, run
`node --test (Get-ChildItem test\*.test.ts).FullName`.
For UI changes, build first and run `python tests/e2e/run.py`. The harness uses
synthetic media, temporary state and a free port, then stops its server tree.

Pytest's `conftest.py` files select throwaway `CUTGRAPH_STATE_HOME` before
application imports. Never test against a person's live state. Other live checks
must use a copied fixture and a free port. Never transcribe unless required by
the task. Do not weaken tests or add skips or suppressions to make a gate pass.
