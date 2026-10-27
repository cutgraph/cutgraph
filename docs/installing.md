# Installing on Windows

Windows 11 x64 is required. Download `CutgraphStudio-<version>-setup.exe` and its
`SHA256SUMS.txt` from the official GitHub release page, then verify the download
below. Browsers label internet downloads with **Mark of the Web** so Windows can
apply extra security checks.

v0.2 is unsigned. SmartScreen may show **Windows protected your PC** because this
is a new, unsigned app without an established reputation; that prompt alone is
not a finding of malware. After verifying the file and confirming you trust its
source, choose **More info** → **Run anyway**, if offered. Managed computers may
prevent continuing.

Check **Windows Security → App & browser control → Smart App Control** for
**On**, **Evaluation** or **Off**. When it is **On**, expect this new unsigned
installer to be blocked, with no per-app override. v0.2 does not support
installation in that mode; keep your protection settings in place.

Future installer signing would display a verified publisher but could still
trigger SmartScreen while reputation builds. A future Microsoft Store install
would avoid SmartScreen download warnings. See Microsoft's
[SmartScreen guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
and [Smart App Control guidance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview)
(both accessed 2026-10-26), and its
[per-app override FAQ](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)
(accessed 2026-10-26).

## Verify your download

Download `SHA256SUMS.txt` beside the installer. In PowerShell, run
`Get-FileHash .\CutgraphStudio-<version>-setup.exe -Algorithm SHA256` and compare
the complete hash with that installer's line in `SHA256SUMS.txt`. To verify its
GitHub build provenance, run
`gh attestation verify .\CutgraphStudio-<version>-setup.exe --repo cutgraph/cutgraph`.
Confirm the official repository named in the product
README or website independently of the download page.
Checksums verify file integrity; attestations verify which repository and workflow
built the file. Neither removes Windows security prompts.
The release also includes a detached Ed25519 signature for `release.json`,
intended for update verification with an independently trusted public key.

## Setup

Run Setup as your normal Windows user. It installs to
`%LOCALAPPDATA%\Programs\Cutgraph Studio` without requiring administrator rights.
Setup shows the licence and third-party notices and creates a Start-menu shortcut.
A desktop shortcut is optional and starts unchecked. Open **Cutgraph Studio**
from Start. Setup also offers to launch it when installation finishes.

The launcher opens an Edge app window with a separate profile in the data folder.
Opening the shortcut again focuses the existing window. Closing the window stops
Studio and its child processes. If Edge is unavailable, Studio opens in the
default browser and shows a **Stop Studio** window. Keep that window open while
editing and close it to stop Studio; closing a browser tab alone does not stop it.
The fallback has no automatic session timeout. Edge can group the running window
under its own taskbar identity, separately from a pinned Studio shortcut.

Studio listens only on `127.0.0.1`, preferring port 8765 and choosing another free
port if necessary. Errors appear in a dialog and in `runtime\launcher.log` and
`runtime\backend.log` in the data folder. Save work before stopping or upgrading:
the launcher allows Edge a short close grace period, then terminates its process tree.

## Assistant

In **Settings, Add-ons, Assistant**, review Anthropic's
[Commercial Terms](https://www.anthropic.com/legal/commercial-terms), accept the
download terms and choose **Install assistant**. The add-on contains Anthropic
software, fetched directly from PyPI using the hash-locked `requirements-agent.txt`;
the base installer does not include it. Settings shows download and installed sizes.
Restart Studio, connect your own Anthropic API key and verify it to enable Ask.
Saved keys use Windows DPAPI; `ANTHROPIC_API_KEY` takes precedence when set.
Ask sends prompts, conversation context and requested document/transcript tool
results to Anthropic, which bills API use. See [Privacy](privacy.md).

## Upgrade and uninstall

Run the new installer to upgrade in place. It stops the running launcher, waits
for it to exit and replaces program files, including obsolete files. Workspaces,
Videos, imported media and saved keys remain in the separate data folder.
Uninstall through **Settings → Apps → Installed apps → Cutgraph Studio**.
Uninstall removes the application and shortcuts and keeps your data.

The unchecked **Also remove downloaded add-ons** option removes only the data
folder's `addons` tree. Workspaces, Videos, media, keys and other model caches stay.
Relocated profiles and linked parent folders are supported. Links inside the
application or add-on deletion tree are refused. Setup and uninstall inherit
`CUTGRAPH_STATE_HOME`; use the same absolute path used to run Studio if removing
add-ons from a custom data folder. Keep data separate from the application folder.
Cloud-file placeholders in the data folder are supported. A relative path or `~`
in `CUTGRAPH_STATE_HOME` must be changed to an absolute path or unset before Setup
or uninstall can continue. If Setup reports an internal symbolic link, junction
or mount point, remove that link from the application folder and retry.

Close Studio in every Windows sign-in session before upgrading or uninstalling.
The launcher coordinates instances within one session; a console session and an
RDP session can each run Studio against the same data folder. Avoid using the
same data folder simultaneously in multiple sessions.

For automation, `"<install-directory>\Cutgraph Studio.exe" --stop` quietly waits
for the launcher and its owned tree to exit. Repeating it when Studio is stopped
is harmless. The installation identity is independent of the data-folder override.

## Data and backup

Back up the entire data folder while Studio is stopped. Restore to the same path,
or select the restored folder with an absolute `CUTGRAPH_STATE_HOME` before launch.
Protected keys require the same Windows account; reconnect providers on another account.

## Portable runtime

The portable bundle contains CPython, Node.js, FFmpeg and FFprobe, the built
editor, the motion renderer and its native canvas module, and the fonts used by
preview and export. System Python, Node.js and FFmpeg are unnecessary. The
assistant and transcription engines are separate optional downloads.

Windows 11 x64 is required. Reserve additional disk space for
imported media, temporary renders and exports. H.264 export tries NVIDIA NVENC,
Intel QSV, AMD AMF, then Windows Media Foundation for CPU encoding. Keep the
appropriate graphics driver current for hardware encoding. Windows N editions
need the Media Feature Pack for the Media Foundation fallback; install it from
Settings > System > Optional features and restart. ARM64 and macOS are unsupported.

Application files stay separate from mutable data. By default, Workspaces,
Videos, imported media, transcripts, jobs, exports, caches and protected keys
live in `%LOCALAPPDATA%\Cutgraph Studio`. Set `CUTGRAPH_STATE_HOME` before launch
to select another writable directory. Back up that directory while Studio is
stopped. Replacing a portable bundle does not remove these files.

## Building

From a committed source checkout on Windows x64, run:

```text
python installer/build.py --out <new-directory> --cache <cache-directory>
python installer/smoke.py <new-directory>
python installer/build.py --installer --out <new-installer-directory> --cache <cache-directory> --iscc <ISCC.exe>
python installer/launcher/smoke.py <new-directory>
```

Both output and cache must be outside every source checkout. The output directory
must not exist. The builder reads raw product blobs from committed `HEAD`, ignores
uncommitted changes with a warning, and records the commit in its manifest. A missing
`HEAD` fails the build. `--revision <commit>` selects another committed snapshot.
The launcher, icon, manifest, Setup script and product version come from that same
snapshot. The installer receipt retains `sourceRevision` and compiler SHA-256 hashes.
It copies those files into temporary staging, installs hash-locked Python wheels,
builds the editor there and installs the locked production canvas module.
Downloads for the runtimes are pinned in `installer/inputs.json`, with source and
notice downloads in `installer/sources.json`; Python and npm
package downloads use `requirements-core.txt` and the npm lockfiles respectively.
Hash mismatches stop the build. No package installation occurs in the checkout.
Use `--offline` to rebuild entirely from a populated cache. A failed build removes
its staging payload; only a verified complete bundle is renamed into place.

The Node archive hash is cross-checked against the pinned release
`SHASUMS256.txt` and its signed checksum document. Builds verify both documents'
pinned hashes and matching checksum lines. They do not verify an OpenPGP signature
or download a release key. The checksum documents are build-provenance inputs,
not shipped components.

`bundle-manifest.json` inventories every payload file with its size, SHA-256 and
origin. The manifest excludes itself. Verification rejects unexpected files,
private material, virtual environments, model caches and links. The smoke tool
uses temporary state, a free port and a system-only PATH; it exports synthetic
footage with animation and burned captions and then stops its process tree.

Launch the portable runtime with `run-studio.cmd`; this developer entry point is
omitted from installed payloads, which use the native launcher. It runs the bundled interpreter with `-B`, keeping
application files read-only. Dependencies and application modules are precompiled
with reproducible unchecked-hash bytecode and stable relative filenames for fast
startup. Source checkouts retain Python's normal bytecode behavior. The pip wheel
is kept separately in `addons/` for optional downloads, outside the runtime import path.

The unpacked size target is under 450 MB (decimal). Notices, licence texts and
`SBOM.cdx.json` are generated during the build. **Settings → About** links to
the bundled notices and SBOM. The installer displays `LICENSE` and the notices.
FFmpeg runs as a separate program under LGPL-3.0-or-later. Its matching
`ffmpeg-corresponding-source.zip` is published beside the binary download.
The builder records that artifact's SHA-256 in `bundle-manifest.json`.

## What each add-on downloads and under which terms

Before downloading, Settings shows the terms and requires acceptance of the
current declaration. Changing its text, links, model pins or package lock
requires fresh acceptance. The install record lists resolved package versions
and the model revisions selected for first use; it does not claim those models
have already downloaded.

- **CPU transcription:** PyTorch and speech packages from PyPI and the PyTorch
  CPU index, under their package licences, including the
  [PyTorch licence and notices](https://github.com/pytorch/pytorch/blob/v2.8.0/LICENSE).
  Recognition, alignment and Silero models use MIT terms; exact sources,
  revisions and licence links are in [Local inference models](models.md).
- **NVIDIA GPU transcription:** CUDA-enabled PyTorch packages, with the same
  models, plus NVIDIA runtime files fetched by the user's computer under the
  [CUDA EULA](https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html) and
  [cuDNN licence](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.10.2/reference/eula.html).
- **Assistant:** the MIT-licensed SDK and its bundled Anthropic software from
  PyPI. The latter and API use are subject to
  [Anthropic's Commercial Terms](https://www.anthropic.com/legal/commercial-terms).
  Each user supplies their own API key; provider usage is billed separately.

The installer includes none of these add-on packages or first-use speech downloads.

## Installer verification

`installer/lifecycle_smoke.py <setup.exe> --i-am-on-a-disposable-machine` performs
real installation, upgrade and both uninstall options. Run it only on a disposable
Windows machine. `CI` and `GITHUB_ACTIONS` never authorize installation. It refuses
existing Studio registrations, application folders or shortcuts, and waits for the
uninstaller's temporary second phase to remove the application directory and registry key.
Hosted Windows runners can check lifecycle behavior, but their administrator
accounts cannot prove standard-user operation; that requires a separate standard-user VM.

Compiler compatibility and launcher development notes are in
[`installer/README.md`](../installer/README.md).
## Updates and rollback

Settings and Home show available stable updates with a version and release-notes
link. Automatic checking is on by default and limited to once a day, including
failed attempts and manual checks. Turn it off in **Settings → Updates** to stop
all update requests. Until the public release feed and its verification key are
configured, Settings shows **Updates not configured** and makes no requests.

Save your work before choosing **Update**. Installed Studio downloads the
installer into the state home's `updates` folder, authenticates the Ed25519-signed
release manifest and verifies the installer size and SHA-256. The native launcher
checks its location and hash again, closes Studio, and starts the per-user silent
installer. Studio reopens when installation finishes. Publisher verification is
disabled while releases are unsigned. A source checkout offers a release link
and must be updated manually. Versions below a release's minimum supported
version also require a manual installation. Automatic updates refuse downgrades.

To roll back, close Studio in every Windows sign-in session, download the previous
release's installer, verify it using the download instructions, and install it
manually. Installation in either direction leaves Workspaces, Videos, keys and
add-ons in the state home. Back up that whole folder before updating or rolling
back. An older application may require different add-on versions.

Video documents currently use schema version 5, with forward migrations from
versions 0, 1, 2, 3 and 4. There is no reverse migration. An older Studio may reject a
newer document or lose fields when saving it; do not assume rollback can edit
documents already saved by the newer version. Restore the matching backup or
return to the newer Studio instead. Preserving files does not guarantee document
compatibility.
