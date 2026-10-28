# Dependency locks and audits

The `requirements-*.in` files own top-level pins and their explanatory comments.
Their `.txt` counterparts contain every resolved dependency, exact versions,
environment markers and SHA-256 hashes. The sets are core, agent (includes core),
dev (includes agent and test tools),
optional local-ai-nvidia and local-ai-cpu (include core and transcription), and reuse (CI's
licence linter). Install `.txt` locks with `--require-hashes`.

## Updating

With `uv` installed on PATH, edit the relevant `.in` pins while preserving their
comments, then run from the repository root:

```text
python scripts/lock_requirements.py
python scripts/lock_requirements.py --check
```

Each set uses `uv pip compile requirements-<set>.in --universal --python-version
3.12 --generate-hashes --no-header --annotation-style line --default-index
https://pypi.org/simple --output-file requirements-<set>.txt`. The transcription
sets compile temporary `pyproject.toml` inputs with an explicit PyTorch index and
per-package sources for only `torch`, `torchaudio` and `torchvision`.
Other packages, including `torchcodec`, resolve from PyPI. The CPU and CUDA
sets retain their explicit `+cpu` and `+cu128` pins.
See [uv's index pinning](https://docs.astral.sh/uv/concepts/indexes/).
The add-on inputs constrain shared packages with `-c requirements-core.txt`.
Compilation also constrains each add-on against the already resolved add-on
sets, excluding the explicitly different CPU/CUDA torch builds. Tests require
every shared pin to agree, including the assistant lock.
Compilation never uses
`--extra-index-url`; generated locks contain no index directives.

Both modes compile in a temporary directory seeded with the current resolutions.
Normal mode replaces locks only after every compilation succeeds. Check mode
prints a unified diff and exits 1 on drift, leaving tracked files untouched.
These commands require network access and do not install application packages.
Review changed versions, markers and hashes before accepting an update; rerun
the consistency tests, audits and repository gates.

The [Getting started](getting-started.md#transcription) CUDA installation uses
`--extra-index-url` to expose the PyTorch
wheels to pip. Exact versions and `--require-hashes` restrict both trusted indexes
to the reviewed artifacts in the lock. Do not drop hash verification.
The in-app installer partitions the flat lock into torch-family and other
packages, removes core entries, and uses one `--index-url` per pip invocation
with `--no-deps`. Hash verification remains mandatory in both invocations.
This avoids cross-index choices for unrelated names. A previous MarkupSafe
3.0.3 resolution recorded only a mirror hash without a matching Windows CPython
3.12 wheel, so binary-only installation failed hash verification. MarkupSafe
3.0.4 is pinned from PyPI. Run `python scripts/lock_requirements.py --check-wheels`
before release. It checks every Windows CPython 3.12 entry in the CPU, GPU and
assistant locks against its installation index and locked SHA-256, using only
PyPI JSON and PyTorch simple-index metadata. It never downloads wheels.
Local version suffixes outside the three reviewed PyTorch packages are refused.

For JavaScript updates, refresh and review each package lock in its own directory,
then use `npm ci` and run its gates. `@napi-rs/canvas` is a runtime dependency of
motion-core because Node export needs it.

GitHub Actions and both npm lockfiles receive reviewed version updates before
each published release, so this repository carries no Dependabot version-update
configuration. Dependabot alerts and security updates stay enabled in the
repository settings. Python updates go through `scripts/lock_requirements.py`;
Dependabot cannot regenerate the universal uv-compiled hash locks correctly.

## CI media tools

The product suite in `.github/workflows/ci.yml` runs on GitHub-hosted
`ubuntu-latest` and `windows-latest` images for pushes to `main`, pull requests
and manual dispatch. Public repositories and forks run by default; private
repositories opt in by setting the repository variable `HOSTED_CI` to `true`.
Both platforms run the environment doctor, Python lint, types and tests, UI
tests, types and build, motion-core tests and the fixture browser harness.
The Windows job also generates SBOMs and checks the licence policy. Actions
use full commit pins and checkout does not persist credentials.
The security workflow below runs separate hosted scans.

CI verifies the downloaded ffmpeg archive before extracting it, including on
cache hits. The cache key includes the archive SHA-256. Windows retains the
GyanD 8.1.1 essentials build; Linux uses the BtbN static GPL 8.1 build. Both are
GPL builds because the tests and the export fallback encode with libx264; they are
CI tools and are never redistributed:

| Platform | Archive URL | SHA-256 |
| --- | --- | --- |
| Windows | https://github.com/GyanD/codexffmpeg/releases/download/8.1.1/ffmpeg-8.1.1-essentials_build.zip | `6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541` |
| Linux | https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n8.1.3-9-g29e619e767-linux64-gpl-8.1.tar.xz | `97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79` |

To bump either tool, select an exact versioned upstream archive (for BtbN, a
month-end autobuild: daily releases are deleted after about two weeks, month-end
releases are kept), download and independently verify its SHA-256, then update the URL, archive filename, expected
hash and cache key together in `.github/workflows/ci.yml`. Update this table and
the checksum assertions in `tests/test_ci_workflow.py`, review upstream licensing,
and run both OS jobs including browser export. Do not use a rolling download URL
or bypass verification for cached archives.

## Public security workflow

Every job in `.github/workflows/security.yml` skips private repositories. Public
pushes to main, pull requests and the weekly schedule run CodeQL with extended
security queries, lockfile vulnerability scans, a verified secret scanner and
OpenSSF Scorecard. Dependency review runs only for pull requests. SARIF uploads
receive job-scoped security-event permission; Scorecard results are uploaded to
the repository without publishing externally, so no OIDC permission is needed.
Scanner findings fail their jobs and need review against the recorded audit
exceptions below. `osv-scanner.toml` lists the accepted advisories by ID, each
with the reviewed package version, a reason and an expiry date; add an entry
only with a row in the table below, and renew it only after repeating the
review. The scanner ignores an advisory by ID alone, so
`tests/test_ci_workflow.py` fails when a listed package's locked version
changes, forcing that review. A new advisory still fails the scan.
pip-audit covers the core, agent, dev and REUSE locks with no exceptions. The
transcription locks pin `+cu128` and `+cpu` torch builds that PyPI does not
host, so pip-audit cannot resolve them; the OSV scan covers those locks.

## Auditing

Use a separately provisioned audit environment with `pip-audit` available.
Run each Python lock through:

```text
pip-audit -r requirements-core.txt --require-hashes
pip-audit -r requirements-agent.txt --require-hashes
pip-audit -r requirements-dev.txt --require-hashes
pip-audit -r requirements-reuse.txt --require-hashes
pip-audit -r requirements-local-ai-nvidia.txt --require-hashes --disable-pip
pip-audit -r requirements-local-ai-cpu.txt --require-hashes --disable-pip
npm --prefix ui audit --omit=dev
npm --prefix motion-core audit --omit=dev
```

The CUDA audit needs a compatible interpreter/platform and the explicit wheel
index. Naming PyTorch's index alongside PyPI is safe here because `--require-hashes` pins
every file: whichever index serves a file, only the locked bytes are accepted. Audit services and dependency resolution require network access. Record
the audit date, affected versions and advisories; hash verification proves file
identity, while audits check known vulnerabilities. Also run full `npm audit`
when assessing development tools.

## Recorded audit outcome and exceptions

Audit of 2026-10-21 (pip-audit 2.10.1, npm 11): core, agent, dev, package and reuse report no known
vulnerabilities. Pillow 13.0.0, certifi 2026.10.19, mcp 1.30.0, claude-agent-sdk 0.2.121, aiohttp 3.14.4
and nltk 3.10.4 replace the versions they supersede. Vite 6.4.5 and Vitest 4.1.12 replace Vite
5.4.21 and Vitest 2.1.9; full `npm audit` is clean for `ui/` and `motion-core/`. The remaining accepted
advisories are the transformers 4.57.6 and torch 2.8.0+cu128 rows below: transformers 5 needs
huggingface-hub 1.x, which WhisperX 3.8.6 does not allow.
The 2026-10-27 refresh replaces opentelemetry-api 1.45.0 (GHSA-xcq7-p3pf-p88h) with 1.45.1 in every
lock, moving the transcription sets' OpenTelemetry SDK and exporters to the matching 1.45.1 and
0.66b1 releases. Those sets also take filelock 4.2.1 (GHSA-v4x8-3wr5-3wq8, GHSA-9vhv-jxww-xfm5,
GHSA-567q-5qvw-pf92) and setuptools 84.1.0 (GHSA-xmj3-9gf7-h52w); setuptools keeps its reviewed
`distutils-precedence.pth` startup hook.
These are recorded results from the refresh, not a guarantee against future
advisories. Repeat the audits for every dependency update and before release.

| Accepted scope | Reason | Revisit trigger |
| --- | --- | --- |
| Optional transcription: transformers 4.57.6 (PYSEC-2025-217, PYSEC-2026-2288, -2289, -2290, -3929, CVE-2026-80047) | WhisperX requires transformers below 5. The advisories need an attacker-controlled model repository, checkpoint or save path. Studio loads only its pinned speech snapshot, versioned torchaudio alignment bundle and wheel-bundled VAD; it never loads a user-supplied model or path. See `docs/models.md`. | A compatible fixed release or WhisperX upgrade; any change allowing untrusted model repositories or paths. |
| Optional transcription: torch 2.8.0+cu128, torchaudio, torchvision (PYSEC-2025-193, -194, -195, -203, -204, -206, PYSEC-2026-139, -2286 on the matching PyPI torch) | pip-audit cannot see the `+cu128` builds, so they are audited as their PyPI versions. The advisories concern loading untrusted model files and specific operators; Studio loads only the speech models it names. WhisperX constrains the torch version. | A torch release WhisperX accepts that fixes them; any path that loads user-supplied models. |

These exceptions are limited to the scopes and assumptions above. Preserve the
full advisory output with release evidence; new findings or changed exposure
require review rather than being covered by these exceptions.

## Source release SBOMs and licence gate

`scripts/sbom.py` generates source-release dependency inventories. Its licence
resolver prefers PEP 639 `License-Expression`, then licence classifiers, then
the legacy `License` field.
Missing metadata produces `UNKNOWN`; generic classifiers retain their actual
labels rather than guessing an SPDX identifier.

Run these offline commands from the repository root using the repository's
Python environment. Each command must succeed before proceeding:

```text
python scripts/model_manifest.py dist/sbom/model-manifest.json
python scripts/sbom.py --output-dir dist/sbom --model-manifest dist/sbom/model-manifest.json
python scripts/license_policy.py --sbom-dir dist/sbom --report dist/sbom/license-policy-report.json
```

The generator writes CycloneDX 1.5 JSON for `requirements-core.txt`,
`requirements-agent.txt`, `requirements-local-ai-nvidia.txt`,
`requirements-local-ai-cpu.txt`, `ui/package-lock.json`
and `motion-core/package-lock.json`. Python versions, environment markers and
all SHA-256 artifact hashes come from the locks, including platform-specific
entries. Licences come from installed distribution metadata only when the
installed version is the locked one; a missing or different installation reads
as `UNKNOWN`, which fails the core gate. Generate release evidence in an
environment installed from the locks. No installation or network request occurs
during generation.

The npm inventories include production transitive and optional dependencies
across platforms, plus `devOptional` entries (optional peers that still install
without development dependencies), exclude `dev` entries, and convert integrity
digests from base64 to CycloneDX hexadecimal hashes. Lockfile licences take
precedence; an installed package's `package.json` is a fallback only when its
name and version match. Components and hashes are sorted. Output has no
timestamps by default; `--timestamp` opts into a current UTC timestamp.

The model manifest is part of the release evidence, so the generator requires a
choice: `--model-manifest <file>` copies the supplied JSON unchanged to
`dist/sbom/model-manifest.json`, and `--no-model-manifest` explicitly produces
incomplete evidence. `scripts/model_manifest.py` emits the checked-in data from
`pipeline/models.py` without importing inference packages or accessing the network.
CI (`.github/workflows/ci.yml`) generates that manifest first, passes it to the
SBOM generator, and uploads `dist/sbom/`, including the manifest and policy report,
as the `sbom` artifact. See [Models](models.md) for model sources and licensing limits.

`licenses.policy.json` gates the core and agent locks and both npm production
inventories. Missing SBOMs or malformed inputs fail closed. GPL, AGPL, SSPL,
BUSL, non-commercial licences, LGPL without a recognized linking exception,
and unknown or unrecognized licence labels fail the core gate. Expressions
containing a restricted alternative also fail. Both optional transcription inventories
(CPU and NVIDIA) are reported with the same rules but do not affect the exit code.
The advisory scan also includes both locks. Allowed SPDX
identifiers and legacy aliases are explicit in the policy; unfamiliar identifiers
require review. `MIT-CMU` is recognized based on the installed Pillow metadata's
PEP 639 expression and its adjacent licence file; no package fallback is used.

Exceptions are bound to an exact versioned purl and the observed licence label.
Each needs a nonempty written reason, verified licence and upstream source URL.
An updated version or changed label requires renewed evidence and review.
The policy currently records these metadata-label exceptions:

| Component | Observed label | Verified licence | Offline evidence |
| --- | --- | --- | --- |
| httpx 0.28.1 | BSD License | BSD-3-Clause | Installed metadata's legacy `License` field and `dist-info/LICENSE.md` contain the three BSD conditions. |
| jsonpatch 1.33 | BSD License | BSD-3-Clause | Installed metadata states Modified BSD License; `dist-info/LICENSE` contains the three BSD conditions. |
| jsonpointer 3.1.1 | BSD License | BSD-3-Clause | Installed metadata states Modified BSD License; `dist-info/LICENSE.txt` contains the three BSD conditions. |

The policy's PyPI source URLs identify the distributions associated with that
installed evidence; they were not fetched during this offline verification.
These entries are reviewable evidence, not a redistribution approval. Preserve
the full report with release artifacts, including excepted components and every
optional-tier finding; human review of licence evidence remains required.
