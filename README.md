# Cutgraph Studio

Cutgraph is a local, transcript-driven video editor. A Workspace groups Videos
and a shared asset library. Edit words in Story, arrange scenes, add animation
and captions, and export through ffmpeg. Editing and rendering run locally;
optional online features are described in [Privacy](docs/privacy.md).

The official source repository is [cutgraph/cutgraph](https://github.com/cutgraph/cutgraph).

v0.2.0 includes an unsigned Windows installer and a portable runtime bundle.
See [Installing on Windows](docs/installing.md) for download verification and
Windows security guidance. Keep backups of important work.

## Platform support

Windows 11 + NVIDIA is the tested platform. Linux is best-effort, with Ubuntu
covered by CI and the fixture browser harness. macOS is unsupported.
On Linux, Show in folder needs `xdg-open` and a desktop session. Without CUDA,
an installed WhisperX capability uses CPU int8 inference; the NVIDIA dependency
lock is optional, and CI does not download speech models or run transcription.

## Quick start

Install Python 3.12 or later, Node 24 (the version in `.nvmrc`), and ffmpeg plus
ffprobe on PATH. From a source checkout in PowerShell:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-core.txt
npm --prefix ui ci
npm --prefix motion-core ci
& .\.venv\Scripts\python.exe scripts/bootstrap.py
npm --prefix ui run build
& .\.venv\Scripts\python.exe scripts/studio_app.py --no-browser --port 8765
```

Open `http://127.0.0.1:8765/editor/`. Follow [Getting started](docs/getting-started.md)
for Linux commands, first use, state locations and troubleshooting.

Windows installations offer on-demand transcription and Assistant downloads in
Settings → Add-ons. For source installations, the optional assistant needs
`requirements-agent.txt` and your own Anthropic API
key saved in Settings or supplied through `ANTHROPIC_API_KEY`
(see [API keys](#api-keys)).
Source transcription uses `requirements-local-ai-cpu.txt` or the optional NVIDIA
pack in `requirements-local-ai-nvidia.txt`;
see [optional capabilities](docs/getting-started.md#optional-capabilities) and
[model sources and licences](docs/models.md).

## API keys

API keys are optional; local editing and export need none. Connect your own
Anthropic API key in Settings for the assistant. Optional stock search accepts
Pexels and Pixabay keys in Settings; either provider works on its own.

Settings saves keys in `runtime/secrets/` under the
[Studio state home](docs/getting-started.md#local-state), separate from the source
checkout. Windows uses current-user DPAPI encryption. Linux restricts the
directory to mode `0700` and files to `0600`, without encryption at rest.
Never commit keys or include them in shared logs or screenshots.

Alternatively, set `ANTHROPIC_API_KEY`, `PEXELS_API_KEY` or `PIXABAY_API_KEY` in
the environment before starting Studio. Non-empty environment values take
precedence over the corresponding saved keys. See
[Key storage](docs/security-model.md#key-storage) for the protection limits.

## Fair Source licence

Cutgraph uses the Functional Source License, FSL-1.1-ALv2. You can use, copy,
modify and redistribute it for permitted purposes, including internal use,
non-commercial education and research, and professional services for a licensee.
Redistribution must retain the terms and copyright notices.

The restriction is Competing Use: offering it to others in a commercial product
or service that substitutes for Cutgraph, substitutes for another product or
service the licensor offers using it when that version is released, or provides
the same or substantially similar functionality. Each version additionally
becomes available under Apache License 2.0 on the second anniversary of that
version's release. Fair Source is not an unrestricted open-source licence during
those first two years. The [LICENSE](LICENSE) controls; see [NOTICE](NOTICE)
for attribution and [TRADEMARK](TRADEMARK.md) for naming rules.

## Support and contributions

Use this repository's GitHub Issues for bugs and feature requests, and Discussions
for questions and ideas. Support is best-effort, with no service levels or response
time guarantees. Cutgraph accepts issues and discussions; code pull requests are not
accepted while the contributor licence agreement is pending.
See [Contributing](CONTRIBUTING.md) and the [contributor guide](AGENTS.md).

Read [Architecture](docs/architecture.md) for the code map and
[Security model](docs/security-model.md) for the local trust boundary.
Report vulnerabilities privately through the Security tab's **Report a
vulnerability** action, following [SECURITY](SECURITY.md).
