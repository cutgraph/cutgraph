# Local inference models

`pipeline/models.py` owns the machine-readable manifest. Generate it offline with
`python scripts/model_manifest.py dist/sbom/model-manifest.json`. It records each
speech model's source, immutable revision or versioned wheel asset, licence,
gating status and approximate uncompressed size in bytes. CI includes this file
in the source release SBOM artifact.

The transcription add-on also uses faster-whisper 1.2.1 under the
[MIT licence at its pinned tag](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/LICENSE).
Settings → Add-ons links to this licence before downloading either transcription tier.

| Asset | Pin | Licence | Approximate size |
| --- | --- | --- | --- |
| faster-whisper large-v3 | `Systran/faster-whisper-large-v3` at `edaa852ec7e145841d8ffdb056a99866b5f0a478` | MIT | 3.1 GB |
| English alignment | `WAV2VEC2_ASR_BASE_960H`, `wav2vec2_fairseq_base_ls960_asr_ls960.pth`, torchaudio `2.8.0+cpu` or `2.8.0+cu128` | MIT (fairseq) | 380 MB |
| Transcription and silence VAD | `silero-vad==6.2.1`, bundled `data/silero_vad.jit` | MIT | 2.3 MB |
| Frontal face detection | OpenCV `data/haarcascades/haarcascade_frontalface_default.xml`, bundled in `opencv-contrib-python==4.13.0.92`; source: [OpenCV cascade](https://github.com/opencv/opencv/blob/4.13.0/data/haarcascades/haarcascade_frontalface_default.xml) | BSD-style three-clause terms in the asset's embedded upstream licence notice | 930 KB |

These assets are not gated. The speech snapshot uses the full Hugging Face commit
SHA and is passed to WhisperX as a local path with further model downloads
disabled. English alignment selects the explicit torchaudio bundle and refuses a
different torchaudio version (both reviewed CPU and CUDA builds are accepted).
Its checkpoint is stored under the state home's
`models/torchaudio/` directory and checked against SHA-256
`488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d`
before atomic installation and again before every load. A corrupt cached file
is refused without replacement. Other speech models and alignment languages require
new reviewed manifest entries before they can be loaded. Hugging Face telemetry
is disabled before inference packages are imported. Portable bundles place model
caches under the state home's `models/` directory. Source installations retain
the libraries' default caches and honour `HF_HOME`, `TORCH_HOME`, `XDG_CACHE_HOME`
and other user overrides without relocating existing downloads. The verified
alignment checkpoint retains its state-local location in both runtimes.

The torchaudio pin identifies the upstream bundle filename and package version;
the manifest also records the checkpoint's SHA-256. Silero loads
its model from the pinned wheel, without fetching repository code. Transcription
passes `vad_method="silero"` and a wheel-backed Silero instance to WhisperX,
preserving speech-segment merging while avoiding the default loader's repository
download. It never loads WhisperX's bundled Pyannote VAD checkpoint.
One loader restores torch's thread count after importing and constructing
Silero in both transcription and silence detection. Transcription also preserves
the thread budget through CTranslate2 inference and alignment. Speech spans as short as 100 ms are retained instead of using Silero's
250 ms minimum; padding remains 30 ms. Segmentation and boundaries can differ
from Pyannote, particularly for short utterances. A synthetic short-word smoke
clip produced nine identical speech spans at 100 ms and 250 ms, in 0.66 s and
0.69 s respectively. Comparison against the previous VAD on representative
recordings remains unmeasured.
`pipeline/face_detection.py` loads the face cascade from `cv2.data.haarcascades`.
The optional package installation supplies this asset; Studio neither bundles it
in the source tree nor downloads a separate checkpoint. The cascade's embedded
licence notice governs the asset; the installed wheel separately identifies the
OpenCV binary as Apache-2.0 and its Python packaging as MIT.
The pinned `mediapipe==0.10.35` wheel is Apache-2.0 and Tasks-only: its installed
file metadata contains no legacy face-detection checkpoints. This installation
therefore uses the OpenCV fallback. The compatibility path for older MediaPipe
solutions uses that package's bundled face-detection assets; those older wheels
and checkpoint licences are not verified by the current optional lock.

The optional dependency input includes `silero-vad==6.2.1`. Regenerate and review
the optional hash lock using the dependency-audit procedure before installing it
or generating release evidence. Package locks must include all manifest wheel
pins; generating the model manifest itself does not regenerate those locks.

## Transcription add-on

The base app works without transcription. Script Videos, editing and export stay
available; footage and narration import link to **Settings, Add-ons** when
transcription is unavailable.

Choose CPU, NVIDIA GPU, or Off in Settings. CPU uses faster-whisper int8 with a
batch size of four. GPU uses float16 and adjusts its batch to free VRAM, with one
smaller-batch retry for an out-of-memory error. A multi-GPU system selects the
eligible GPU with the largest reported total memory in a portable bundle, then checks free memory on
that device only. The bundle sets CUDA's PCI bus order only while initializing
transcription; source installations keep their existing device order. An explicit
`CUDA_VISIBLE_DEVICES` override disables automatic GPU selection; clear it and
restart to use the GPU tier, or use CPU.

| Tier | Package size estimate, unpacked | Models on first use | Requirements |
| --- | --- | --- | --- |
| CPU | About 4 GB | 3.5 GB | CPU PyTorch wheels; recognition can be slower than the recording |
| NVIDIA GPU | About 8.5 GB | 3.5 GB | CUDA 12.8 PyTorch wheels, an 8 GB class GPU and a supported driver |
| Off | 0 | 0 | No downloads |

Earlier measurements used a synthetic 9.33-second clip with 22 timed words.
The CPU result of 42.6 seconds (4.57× the clip duration) was single-threaded
because Silero's import changed torch's process-wide thread count. A later
synthetic 15.43-second clip produced 26 timed words in 38.2 seconds (2.48× the
clip duration), with 24 threads preserved through silence detection and
transcription. This was a warm CPU int8 smoke run, not sustained throughput.
The earlier package measurements
(3.83 GB CPU, 8.25 GB GPU) included duplicate core distributions and excluded
retained wheel caches; they do not establish the current total footprint.
The current CPU probe installed 3.75 GB of packages without duplicate core
distributions and occupied 7.22 GB including downloaded models and records.
Settings reports cached file bytes across installed versions, temporary retry
downloads and this add-on's owned model caches. Disk allocation can differ from
file bytes. Current GPU package size and sustained throughput remain unmeasured.

The driver gate uses CUDA 12.8 GA's full-support baseline: **570.65 on Windows**
and **570.26 on Linux**, from the [NVIDIA CUDA release notes](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/).
It deliberately does not assume limited CUDA minor-version compatibility. The
8 GB class gate permits reports down to 7680 MiB to accommodate reserved memory;
other applications can still consume memory, so it does not guarantee a batch
will fit. CPU remains available when the GPU gate fails.

Before downloading, Settings lists PyTorch's licence, each speech model's licence,
and the CUDA/cuDNN terms for the GPU tier. Installation requires acceptance of
the exact current declaration, bound to its lock hash and tier. Updated
declarations require renewed acceptance. The linked upstream terms govern each
download.

`pipeline/addons.py` runs the current interpreter's pip with binary-only,
hash-verified requirements, installs into staging and atomically records an active
pointer under `addons/<name>/<install-id>/` in the state home. The 16-character
install id binds the full lock digest, licence acceptance, excluded core versions and interpreter ABI
while keeping native-library paths short. The durable job
reports progress through the existing job/SSE channel. Cancel stops pip; Retry
starts a fresh staging target and reuses completed downloads; pip restarts any
partial wheel download from zero. Successful installation purges the wheel cache.
An interrupted or
failed attempt never activates a partial target. The install record lists the
resolved package versions, Python minor version, ABI, platform, licence
declaration, excluded core versions and timestamp. Core pins constrain every add-on lock. Core packages
are excluded before pip runs with `--no-deps`, preserving wheel RECORD metadata.
Add-ons follow site-packages and the bundle's core target on the import path.

**Close and reopen Studio after installation, update, or removal.** Native
dependencies are activated at startup and cannot be safely hot-swapped. Add-ons
survive app upgrades; a changed lock or declaration shows **Update available**.
A different core dependency set, Python minor version, ABI or platform prevents activation and offers
**Update add-on**. Superseded trees are deleted after switching, with loaded
trees kept until restart. Cleanup retries locked files on later starts; cleanup
failure never prevents Studio from starting. Interrupted pip processes are
identified by PID, creation time and executable image and stopped before cleanup or another install.
Windows installers also run in a process-owned kill-on-close job. Studio locks its
state home before startup work, so a second launch cannot interrupt a live installation.
Remove deletes
package versions, cached wheels and exclusively owned portable model caches; shared
process caches and source-installation model caches are kept. Windows may defer
deleting loaded native libraries until the next startup. A source environment
with transcription already installed
continues to work and reports that its packages are managed externally.
The `localInference` health flag describes transcription dependencies only;
reframing also requires its separate optional face-detection packages, which
the CPU transcription tier does not include.
