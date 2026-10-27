# Privacy

Cutgraph has no product telemetry, analytics, crash reporting or background
usage upload. `studio/app.py` disables framework telemetry.
`pipeline/models.py` sets `HF_HUB_DISABLE_TELEMETRY=1` before inference packages
are imported. It also sets `PYANNOTE_METRICS_ENABLED=false` before pyannote can
load through WhisperX; no request goes to the pyannote metrics host,
`otel.pyannote.ai`. `scripts/agent_host.py` sets
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` for the bundled assistant process.
These settings do not turn requested API calls or model downloads into offline
operations. External services have their own retention and privacy policies.

## Outbound flows

Stock endpoints are fixed to the provider HTTPS hosts. Hugging Face
infrastructure can redirect downloads to its
storage/CDN hosts. A remote service sees the requesting IP address and ordinary
HTTP/TLS metadata for each network request.

| Flow | When | Destination hosts | What is sent | How to avoid it |
| --- | --- | --- | --- | --- |
| Assistant key verification | Connecting or verifying a key, or checking connection status with a configured key while the assistant add-on is available | `api.anthropic.com` | API key in an authentication header and a models-list request; no media | Leave the assistant add-on unavailable, or disconnect the key in Settings and unset `ANTHROPIC_API_KEY`. |
| Assistant inference through the downloaded assistant | Asking for edits, drafting a script or describing an expression | `api.anthropic.com` | API key, prompts, conversation context, and requested tool results such as Video documents, transcript text, notes, channel profiles, preference summaries (including other Videos on the same channel) and local media references | Leave the assistant disconnected and do not use assistant drafting. Raw source video/audio is processed locally, but transcript and document content can be sent. |
| Assistant add-on download | Accepting the linked terms and choosing Install assistant in Settings, Add-ons | `pypi.org`, `files.pythonhosted.org` | Hash-locked package and artifact requests; no API key, media or documents | Leave the assistant uninstalled. The base installer contains neither the assistant SDK nor its CLI. |
| Speech snapshot download and cache checks | English transcription when Hugging Face resolves the pinned snapshot | `huggingface.co`, `cdn-lfs.huggingface.co`, `cdn-lfs-us-1.huggingface.co`, `cas-bridge.xethub.hf.co`, `cas-server.xethub.hf.co`, `transfer.xethub.hf.co` (provider storage hosts may change) | Model identifier/revision, file requests and download metadata; no audio or transcript | Do not transcribe, or provision the pinned cache and configure Hugging Face offline mode (`HF_HUB_OFFLINE=1`). Telemetry is disabled with `HF_HUB_DISABLE_TELEMETRY`. |
| English alignment checkpoint | First transcription without a cached verified checkpoint | `download.pytorch.org` | Request for the versioned alignment bundle; no audio or transcript | Avoid transcription or provision the manifest-pinned checkpoint in the local alignment cache; cached bytes are verified before use. |
| Pexels search | Searching Stock with a configured Pexels key | `api.pexels.com` | API key, search words, media kind and pagination | Do not search Stock; import local media instead. |
| Pixabay search | Searching Stock with a configured Pixabay key (results are cached for 24 hours in process) | `pixabay.com` | API key in query parameters, search words, media kind and pagination | Do not search Stock; import local media instead. |
| Stock thumbnail and preview hotlinks | Displaying stock results or playing their preview in the browser | `images.pexels.com`, `videos.pexels.com`, `cdn.pixabay.com`, `pixabay.com`, `i.vimeocdn.com` | Requests for provider-supplied images/video URLs; IP/browser metadata, without Studio's API keys; Studio sets no-referrer | Avoid opening/searching Stock; use local Library assets. Vimeo supplies Pixabay video thumbnails, not a separate Vimeo search API. |
| Stock import | Importing a chosen stock result into a Workspace Library | `pexels.com`, `pixabay.com` and their subdomains, including `images.pexels.com`, `videos.pexels.com`, `cdn.pixabay.com` | Media URL request from Studio; no Video document or transcript | Import local files instead. Once imported, Library media is served locally. |
| Provider attribution links | Clicking a provider or author link | `www.pexels.com`, `pexels.com`, `pixabay.com` and provider/author links returned by stock search | Browser navigation to the chosen page, subject to browser settings | Do not open external attribution links. |
| Dependency installation and lock maintenance | Installing an add-on in Settings, running the documented pip/npm setup or `scripts/lock_requirements.py`; not during normal editing | `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org`, `registry.npmjs.org` | Package names, versions and artifact requests; no media or documents | Leave add-ons off, or use a separately prepared offline dependency cache and avoid lock updates. The doctor itself makes no network requests. |
| Add-on licence and hardware reference links | Only when a licence or reference link is opened | `www.anthropic.com`, `github.com`, `docs.nvidia.com`, and the model hosts listed above | Ordinary browser page requests; no media or documents | Do not open the links. The app does not fetch these pages in the background. |

The product source implements these flows in `pipeline/agent_credentials.py`,
`scripts/agent_host.py`, `pipeline/models.py`, `pipeline/transcribe.py`,
`pipeline/stock.py` and `ui/src/v3/story/drawer/stock/StockPanel.tsx`.
Loopback requests to `127.0.0.1`, `localhost` or `::1` connect the browser and
local compute bridge to Studio; they do not leave the computer.

SBOM validation uses `cyclonedx.org` schema identifiers with locally supplied
schema files. Validation makes no request to that host.

## Assistant keys

The assistant add-on contains Anthropic software and requires your own Anthropic
API key; API use is billed by Anthropic. Review the
[Commercial Terms](https://www.anthropic.com/legal/commercial-terms) before installing.
On Windows, a saved key is protected with DPAPI for the current account.
`ANTHROPIC_API_KEY`, when set, takes precedence over the saved key.
Health reports only presence/source and known verification state, never key text,
and does not verify keys over the network.
It reads protected-file metadata and caches verification results without decrypting
the key during health probes. Settings, Add-ons always offers **Disconnect saved key**
for a saved key, even when the add-on is absent, removed, awaiting restart or an
update, or its catalog cannot load. Removing the add-on keeps the saved key until
you disconnect it. An environment key must be unset in the launching environment.
While the add-on is unavailable or its catalog cannot load, Settings checks only
key presence and does not send a verification request to Anthropic. Connecting
or verifying a key requires the add-on to be available.

## What stays local

Imported media, transcription inference, word transcripts, Video documents,
Workspace libraries, preview frames, rendered exports, proposals, notes, preference decisions,
conversation logs and jobs live on your computer. Content used in an assistant
request is the exception described above. No workspace or media synchronization
service is built in. The fonts and silence/VAD assets are bundled files and
wheel assets, not runtime downloads. Model pins and licensing limits are in
[Models](models.md).

The assistant's `read_preferences` tool reads compact summaries from
`outputs/decisions`: accepted/rejected decisions, editorial intent, rationale
and timestamps. Video scope includes that Video's decisions; channel scope can
include other Videos on the same channel. Requested summaries become assistant
tool results and can be sent to the provider, including in Plan mode. They
exclude raw footage and full transcripts, but model-written intent titles and
rationales can quote transcript words and contain sensitive information.
Leave the assistant disconnected to keep these summaries local.

State lives in the locations described in
[Getting started](getting-started.md#local-state). Protected keys and assistant
configuration are stored under the state home; see
[Security model](security-model.md#key-storage). Source installations use Hugging
Face's default or user-configured cache. Portable bundles keep model downloads
under their state home. Studio also writes local logs, including media filenames
and ffmpeg commands; conversations can contain transcript content. Stop Studio
before deleting local state, and manage separate model caches and backups too.
Review logs, screenshots and exported documents before attaching them to a bug
report. Remove keys and personal information.
## Automatic update checks

Stable-release update checks are on by default. Requests go to `github.com`,
`release-assets.githubusercontent.com` and `objects.githubusercontent.com`.
Once a public release repository
and verification key are configured, Studio requests `release.json` and
`release.json.sig` from GitHub Releases at most once per day. GitHub and its release
asset delivery service receive your IP address and a plain user agent containing
the Studio version. Studio sends no account, device or installation identifier,
keys, Workspace names, Video content or usage events with these requests.

Turn off **Settings → Updates → Check for updates automatically** to stop all
update requests, including checks and installer downloads. The preference and
last check time are stored locally in the state home's `updates` folder. A missing
release repository or public verification key disables requests. Installers are
downloaded only after you choose Update, and are verified before installation.
