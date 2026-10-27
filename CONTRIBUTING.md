# Contributing

Cutgraph accepts issues and discussions. Code pull requests
are not accepted yet while the contributor license agreement (CLA) is pending.
Please follow the [code of conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and requesting features

Use the bug report or feature request form in this repository's Issues tab.
For bugs, include the version, operating system, steps to reproduce, expected
behavior, observed behavior, and relevant logs or screenshots with personal data
and credentials removed. A small synthetic example helps us reproduce the problem.
Use Discussions for questions and ideas. Report security vulnerabilities through
the private process in [SECURITY.md](SECURITY.md).

## Local development checks

See [Getting started](docs/getting-started.md) for core and optional capabilities,
and [AGENTS.md](AGENTS.md) for conventions and the complete gate list. Create
a virtual environment, then install the full core, co-pilot and development lock:

```powershell
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
```

Keep hash verification enabled for all dependency sets. The optional CUDA
command in Getting started uses the PyTorch extra index safely by accepting only
the exact versions and SHA-256 artifacts recorded in its lock. See
[dependency-audit.md](docs/dependency-audit.md) for lock regeneration and audits.
Run these checks from the repository root with the development environment active:

```text
python -m pytest -q -p no:cacheprovider
python -m ruff check .
python -m pyright
```

In `ui/`, run:

```text
npm test
npm run typecheck
npm run build
```

In `motion-core/`, run:

```text
node --test test/*.test.ts
```

The fixture end-to-end suite requires system Chrome, ffmpeg and Node. After
building the UI, run it from the repository root:

```text
python tests/e2e/run.py
```

Tests use throwaway state. For manual testing, set `CUTGRAPH_STATE_HOME` to a
disposable fixture directory before starting Studio.
