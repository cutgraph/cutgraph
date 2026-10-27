# Windows installer development

The local compile check supports Inno Setup 6.0.5. Release builds should use the
Inno Setup 6.7.3 pin in `compiler.json`; compiler warnings fail the build. The
script selects `x64os` from 6.3 and the revised custom-form API from 6.6. Older
compilers use their corresponding syntax. Signing and `SignedUninstaller` remain
disabled together until Authenticode signing is configured.

The launcher owns a kill-on-close job with no breakaway allowance. Update
support asks the launcher to verify and spawn the installer outside this job.
The update service writes a verified request to `updates/pending.json` and invokes
`--request-update`. The running launcher acknowledges its per-instance update
event, holds the installer and parent folder against replacement, checks the
path, size, SHA-256 and version, stops its job, then spawns Setup itself with
`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /CUTGRAPHUPDATE=1`.
`--verify-update` performs only the native verification and never executes Setup.
The final parameter makes Setup relaunch Studio after a successful silent update.
Authenticode publisher verification is disabled until signed releases exist.

Set `UPDATE_REPOSITORY` and `UPDATE_PUBLIC_KEY` in `config.py` to the public
repository and independently pinned base64 Ed25519 public key. Empty values
disable all network requests. The shared release verifier is shipped with the
core runtime and uses its hash-locked cryptography dependency.

## Release metadata

The installed update verifier uses the hash-locked `cryptography` dependency in
`requirements-core.txt`. The privileged release job instead installs only the
minimal hash-locked `requirements-release.txt`, generated with
`scripts/lock_requirements.py` and constrained to the development lock's reviewed
versions, as wheels only.
`python installer/release_manifest.py keygen --private-key <new-private-file> --public-key <new-public-file>`
writes base64-encoded raw 32-byte Ed25519 keys to distinct, new files. The private
key is never printed. Choose a protected private-file destination outside the
source tree. On Windows the destination directory's ACL governs access; the file
mode alone does not protect the private key. Store its contents as the `RELEASE_ED25519_KEY` release-environment
secret, and the public file's contents as `RELEASE_ED25519_PUBLIC_KEY`, a variable
in the same environment. Consumers must pin that public key independently.
After vaulting the private key, remove the plaintext file or keep it only on
protected offline media.

`generate` requires `--installer`, `--version`, `--url`, `--minimum-version`,
`--release-notes-url` and `--out`; `--published-at` optionally fixes the UTC date.
The stable version format is `major.minor.patch`. The manifest fields are
`version`, `channel` (`stable`), `publishedAt` (UTC), `minimumVersion`,
`releaseNotesUrl`, and `installer` (`url`, byte `size`, lowercase `sha256`).
`publishedAt` records manifest generation after environment approval, rather than
the time the draft is published. The workflow reads `minimumVersion` from
`pyproject.toml`'s `[tool.cutgraph] minimum-supported-version`. Its initial
`0.2.0` floor supports the first stable Windows release and later versions.
It must be stable and no higher than the product version.
`sign <release.json> --out <release.json.sig>` reads `RELEASE_ED25519_KEY` only
from the environment and writes a raw 64-byte detached signature over the exact
manifest bytes, including its final newline. `verify <release.json> --signature
<release.json.sig> --public-key <public-file> --installer <setup.exe>` checks both
authenticity and installer contents.

The release workflow accepts version tags and dispatches from `main`.
Set the product version to a stable version and create its matching `v` tag
on a commit reachable from `origin/main` before running it. The workflow checks
that ancestry and re-resolves the remote tag immediately before draft creation.
Draft creation uses `--verify-tag` and never creates a tag.
Configure the `release` environment with a required reviewer and restricted
deployment refs before enabling releases. The reviewer approves signing and
draft creation, then separately publishes the draft. Private copies may opt in
to unsigned hosted builds with `HOSTED_CI=true`; attestation and drafting require
a public repository. Authenticode remains disabled.
Unsigned artifacts are retained for the 30-day environment approval window;
expired approvals require a fresh build. One release concurrency group serializes
tag and dispatch runs.

Before enabling releases, configure a `v*` tag ruleset restricting creation to
release maintainers and preventing updates and deletion, alongside the protected
`main` branch and the environment restrictions above.

Installer builds can retain their verified `payload/` with `--keep-payload` for
bundle and launcher smoke checks. `python installer/release_assets.py collect
<installer-output> --out <new-assets-directory>` selects the installer, notices,
SBOM, matching source archive, payload manifest and installer receipt.
`checksums <assets-directory>` writes `SHA256SUMS.txt` for every other asset,
including the release manifest and its signature when present. The checksum file
excludes itself; the workflow attests it along with every other release asset.

Distribution licensing is generated from the shipped files by `licensing.py`
and `scripts/sbom.py`. Wheel RECORDs and installed npm metadata identify package
ownership; runtime archives supply upstream notices. `sources.json` pins the
additional source and notice inputs and records the canvas binary scan. A missing
licence text, SBOM component or file owner fails the build. The CycloneDX 1.5
schemas in `tests/fixtures/cyclonedx/` validate the output offline.

`python installer/build.py --sources-only --out <new-directory> --cache <cache>`
produces `ffmpeg-corresponding-source.zip` and `manifest.json`. The archive includes
the exact FFmpeg tree, build recipe and patches, configure line, and all pinned
dependency trees, including permissive libraries and the libiconv gnulib input.
The manifest records the public recipe-build run. Notice-only inputs stay in
the distribution's licence texts; omitted dependency sources fail the build.
Publish this archive beside the binary and SBOM. Normal bundle builds also
produce the source ZIP beside their output directory, prefixing its filename with
the directory name, and record its hash in `bundle-manifest.json`. Installer
assembly requires that matching adjacent archive and copies it into its release
output together with the SBOM and installer receipt.

Coordination is per user, installation and Windows session. Events are specific
to the active launcher process creation FILETIME, so a request handle cannot keep a prior
instance's event alive under a replacement owner's name. Activation is acknowledged
by the UI loop; an unacknowledged click waits for that launcher to exit and retries
the instance mutex. Stop waits for the observed process to exit. Setup must be
run after Studio is closed in every other Windows session.

Path-check exit codes are 0 for success, 2 for an invalid `CUTGRAPH_STATE_HOME`,
3 for overlapping program and data folders, and 4 for an internal symbolic link,
junction or mount point. Other failures return 1. Cleanup opens each entry itself
and holds its handle through validation and deletion. Cloud and other non-link
reparse tags are allowed; symbolic links and mount-point tags are refused.
