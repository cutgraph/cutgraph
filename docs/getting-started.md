# Getting started

Cutgraph v0.2.0 includes a [Windows installer](installing.md). This guide covers
source installations. Windows 11 + NVIDIA is tested, Linux is best-effort, and
macOS is unsupported. See the
[README](../README.md#platform-support) for the platform statement.

## Prerequisites

Use Python 3.12 or later (`.python-version` records the CI version), Node 24
(`.nvmrc` records the exact version), npm, and ffmpeg/ffprobe. Source builds with
libx264 use it for CPU encoding and NVENC where supported. Windows LGPL builds
without libx264 try NVENC, QSV, AMF and then Windows Media Foundation. Both npm directories
need their locked dependencies even when the assistant and transcription are off.
Run commands from the checkout root. No system font installation is needed:
the Inter, Liberation Sans and Cascadia Code files are bundled in `assets/fonts/`.
API keys are optional; see [API keys](../README.md#api-keys) for online features.

## Windows setup

In PowerShell, run each command separately and check its exit code before continuing:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-core.txt
npm --prefix ui ci
npm --prefix motion-core ci
& .\.venv\Scripts\python.exe scripts/bootstrap.py
npm --prefix ui run build
& .\.venv\Scripts\python.exe scripts/studio_app.py --no-browser --port 8765
```

## Linux setup

In bash, stop if any command fails:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-core.txt
npm --prefix ui ci
npm --prefix motion-core ci
.venv/bin/python scripts/bootstrap.py
npm --prefix ui run build
.venv/bin/python scripts/studio_app.py --no-browser --port 8765
```

The optional CUDA lock requires compatible NVIDIA hardware and drivers; Linux
without it can edit, preview and render using the core installation.

## First run

Open `http://127.0.0.1:8765/editor/` in a current browser. Create a Workspace,
then a Video. A script Video can be edited without transcription. Import media
into the Workspace Library to use it in scenes. For a footage Video, install
transcription before requesting a transcript. Story's Script, Board, Timeline
and Source lenses edit the same Video document. Save and export from Story;
keep the terminal running until jobs finish. Stop Studio with Ctrl+C.

The server accepts loopback addresses only. Choose another unused port with
`--port` if the default is busy. Rebuild the UI after changing frontend code.

## Optional capabilities

### Assistant

Install `requirements-agent.txt` in the same virtual environment:

```powershell
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-agent.txt
```

On Linux, use `.venv/bin/python` instead. Restart Studio after installing, then
open Settings and connect your own Anthropic API key. Settings verifies the
connection over the network; the assistant becomes available after verification.
API use is billed by the provider. Alternatively, set `ANTHROPIC_API_KEY` in the
environment before starting Studio. A non-empty environment value takes
precedence over a saved key; unset it and restart Studio to manage the key in
Settings. See [API keys](../README.md#api-keys) for storage and protection.
The doctor reports only key presence and source (environment or Settings),
never the value or validity.

The assistant proposes edits for review in Story. Accept applies them; Plan mode
allows inspection only. Script and expression drafting also use the API.
Read [Privacy](privacy.md) before sending sensitive material and
[Security model](security-model.md#assistant-boundary) for tool restrictions.

### Stock search

Add a Pexels or Pixabay key in Settings under Stock B-roll, or set
`PEXELS_API_KEY` or `PIXABAY_API_KEY` in the environment before starting Studio.
Either key works alone. Non-empty environment values override the corresponding
saved keys, which use the same protection as the assistant key. Importing local
media into the Library needs no key.

### Transcription

Install the optional NVIDIA pack, keeping hash verification enabled:

```powershell
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-local-ai-nvidia.txt --extra-index-url https://download.pytorch.org/whl/cu128
```

On Linux, replace the interpreter with `.venv/bin/python`. Exact versions and
SHA-256 hashes restrict the extra index to reviewed CUDA 12.8 artifacts.
Only torch, torchaudio and torchvision use CUDA-specific builds; torchcodec
uses the matching PyPI wheel on Windows.
Restart Studio after installing. The first English transcription downloads the
pinned speech snapshot and alignment checkpoint; reserve several GB of disk
space. Inference runs locally. Only the reviewed English large-v3 configuration
is supported. See [Models](models.md) for pins and licensing limitations and
[dependency audits](dependency-audit.md) for accepted optional-pack advisories.

## Local state

Windows uses `%LOCALAPPDATA%\Cutgraph Studio`. Linux uses
`$XDG_STATE_HOME/cutgraph-studio` when XDG_STATE_HOME is absolute, otherwise
`~/.local/state/cutgraph-studio`. Set `CUTGRAPH_STATE_HOME` before starting Studio
to select another directory. The doctor reports that location without creating it.

The state home contains imported media, Video documents, transcripts, Workspace
libraries, exports, caches, jobs, conversation logs and protected keys. Back it
up while Studio is stopped. It is separate from the source checkout; removing
the checkout does not remove state. Hugging Face uses its own model cache by
default; `HF_HOME` can select a different cache. The alignment checkpoint lives
in `models/torchaudio/` under the Studio state home.

For development, install `requirements-dev.txt` instead of the core lock and
follow [AGENTS](../AGENTS.md). Tests and manual fixture runs must use disposable
`CUTGRAPH_STATE_HOME` directories.

## Doctor and troubleshooting

Run `python scripts/bootstrap.py` with the virtual environment's Python.
`--json` emits structured results. The doctor is offline and read-only; it
does not install dependencies, download models, create state or verify keys.
Required failures return exit code 1. Missing optional capabilities are
advisories and return 0 when all required checks pass.

| Check | Fix |
| --- | --- |
| Python | Install a version satisfying `requires-python` in `pyproject.toml`, then recreate the virtual environment. |
| Core imports | Use the virtual environment interpreter and install `requirements-core.txt` with `--require-hashes`; contributors use `requirements-dev.txt`. |
| Node | Use the Node major in `.nvmrc`, then restart the terminal. |
| npm dependencies | Run `npm --prefix ui ci` and `npm --prefix motion-core ci` from the checkout root. |
| ffmpeg / ffprobe | Put both executables on PATH, or set `CUTGRAPH_FFMPEG` and `CUTGRAPH_FFPROBE` to existing absolute executable paths. `CUTGRAPH_TOOLS_DIR` can name their directory. |
| Fonts | Restore all tracked files under `assets/fonts/`; preview and export need the same fonts. |
| libx264 | Optional for Windows LGPL builds, which use hardware or Windows Media Foundation. Source installs can use libx264 for CPU encoding. |
| No video encoder | If your Windows edition name ends in "N", install the Media Feature Pack from Settings > System > Optional features and restart. |
| NVIDIA GPU | Check drivers and `nvidia-smi` for CUDA transcription; a GPU is optional for core editing. |
| Transcription / assistant pack | Install the corresponding optional hash lock, then restart Studio. Package presence does not prove GPU compatibility or downloaded model availability. |
| Assistant key | Connect your key in Settings or set `ANTHROPIC_API_KEY` before starting Studio, then verify in Settings; the doctor reports presence and source, not validity. |
| State home | Select a writable directory with `CUTGRAPH_STATE_HOME`; for a new directory the doctor checks the nearest existing parent with `os.access`, without writing. |

If the browser shows an unavailable editor, run `npm --prefix ui run build`.
If a key cannot be verified, check connectivity and the provider's key permissions.
For other problems, use the [support process](../CONTRIBUTING.md) and remove
credentials and personal data from logs before sharing them.
