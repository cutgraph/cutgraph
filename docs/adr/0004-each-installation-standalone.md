---
status: accepted
date: 2026-09-24
superseded-by:
decision-drivers:
  - Media, credentials and editing state belong to the local installation.
  - Editing and rendering must not require a shared server or an account.
---

# Each installation is standalone

Each installation runs Studio on loopback and owns its local state home:
Workspaces, Videos, media, jobs and credentials. Compute runs on this computer, using CUDA
where supported and CPU fallbacks where available. Optional transcription
requires its dependency pack; core editing and rendering do not require a GPU.
The optional assistant uses the user's own provider API key.

**Consequences**

- Studio refuses non-loopback binding.
- Moving a Workspace or Video between installations requires copying its state
  and media; there is no shared library.
- ffmpeg builds with libx264 retain NVENC → libx264 for exports, proxies and opaque
  scenes, and libx264 alone for script stills and frame-rate conforming. Builds
  without it try NVENC → QSV → AMF → Windows Media Foundation. Transcription
  selects CUDA when available, otherwise CPU with an appropriate compute type.
- Tests and manual verification use disposable or copied state homes.

**Evidence**

- `runtime.py`: `default_state_home` and `resolve_runtime` select local state.
- `config.py`: `STATE_HOME` supplies the mutable path root.
- `scripts/studio_app.py`: `main` accepts only loopback listening addresses.
- `studio/compute.py`: `_run_compute_action` dispatches local work.
- `pipeline/compile.py`: `compile_project` implements local ffmpeg export.
- `pipeline/transcribe.py`: `transcribe` selects the local inference device.
- `tests/conftest.py`: `_STATE_HOME` isolates product tests.
