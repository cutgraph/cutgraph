# Security model

## Threat model

Studio serves one local OS user on loopback. It must resist hostile web pages
in the same browser, request tricks that target the local server, untrusted media,
and instructions embedded in transcripts or assistant tool output. Local editing
and export do not require an online assistant.
This assumes single-user machines: other OS accounts on the same computer can
reach the loopback port and are outside this threat model.

## Browser boundary

`scripts/studio_app.py` refuses non-loopback bind addresses.
`pipeline/web_security.py` rejects non-loopback peers and requires an allowed
Host with the configured port, limiting DNS rebinding. It rejects ambiguous
browser context headers and cross-site Fetch-Metadata requests, except document
navigation into the editor. Unsafe methods and WebSockets check Origin;
requests without browser context need an internal-client token. The token gates
those requests; it does not authenticate processes running as the same OS user,
which are outside this threat model and can supply allowed Origin headers.
The local compute bridge reads a token file in `runtime/claude-agent`, a folder
restricted to the current OS user. The file is named with the Studio process id.
Each process writes its file once, at startup or when the assistant first needs
it, and reuses it across
assistant sessions; the file is deleted when that Studio process stops. A
process that is killed leaves a file holding a dead token, which a later start
removes once no running process has that id. A second Studio process has its
own file. MCP configuration and command lines
contain only its path, never the token. The bridge ignores environment proxies.
The browser UI never receives it.

The same middleware sets a Content Security Policy with same-origin scripts,
fonts and connections. Images and media have explicit stock CDN allowances.
It denies framing, objects and foreign form actions, and sets nosniff,
no-referrer and same-origin opener/resource headers. These controls reduce
browser attacks; they are not authentication for a network service.

The UI sends JSON for JSON routes. `studio/helpers.py` bounds and parses JSON
bodies and, as defense in depth, answers 415 when a JSON route receives a
non-JSON Content-Type or a body without one. A request with neither a body nor
a Content-Type reads as an empty object. Upload routes accept their own media
types. Browser request isolation is enforced by the origin and Fetch-Metadata
controls above.

## Files and media

`studio/routes/uploads.py`, `studio/routes/workspaces.py`,
`studio/routes/script_video.py`, `studio/helpers.py` and `studio/multipart.py`
stream uploads with byte caps and clean partial files after failure. Limits in
`config.py` include 20 GiB source uploads, 2 GiB library/narration uploads,
1 MiB ordinary JSON and 16 MiB document/edit bodies.

`pipeline/names.py` validates portable names, rejects reserved device names,
detects case collisions, and requires exact spelling and containment under the
trusted root. `studio/routes/media.py` serves allowed media from configured
roots rather than arbitrary state files. `pipeline/project.py` validates document
media references to supported media extensions under `data/`, Workspace
`workspaces/<id>/library/`, generated `outputs/proxies/`, `outputs/scenes/`,
`outputs/final/` and `outputs/preview/` roots, generated PNG/JPEG files under
`outputs/thumbnails/`, or legacy `media/` and `proxies/` roots. References to keys,
notes and other state files, control characters and dot/space-only path segments
are refused. Unsafe media references and caption styles are refused when a
document is saved or patched and again before export, compile and caption burn
use it. Reading a saved document for display is not a boundary. Media resolution
also validates references. `pipeline/render_inputs.py` checks render dependencies,
including symlinks and reparse points. `pipeline/stock.py` restricts downloads to
HTTPS provider domains, accepts only 2xx import responses and caps downloaded
bytes. Search endpoints are fixed; fixtures inject offline clients. Attribution
links in the UI permit only HTTPS. Caption style values are validated before
subtitle serialization, and drawtext renders text without expansion.

ffmpeg, Node canvas and optional inference libraries parse untrusted media in
local processes. These processes are not a sandbox. Keep dependencies current,
use media from sources you trust and review the recorded
[dependency audit exceptions](dependency-audit.md#recorded-audit-outcome-and-exceptions).
Speech model sources are pinned in `pipeline/models.py`; the English alignment
checkpoint is SHA-256 checked before installation and every load.

## Assistant boundary

`scripts/agent_host.py` removes built-in filesystem, shell and network tools,
uses strict MCP configuration with no filesystem settings sources, and checks
every tool against an explicit allowlist. Plan mode permits inspection tools
only. In Default mode, document edits are proposals from `pipeline/copilot.py`
and `pipeline/proposals.py`; a person must Accept them through the revision-gated
routes in `studio/routes/chat.py`. Some tools can write notes or start local
compute jobs; final rendering has a separate job approval gate in
`pipeline/jobs.py`. Proposal-only describes Video edits, not an absence of all
local side effects.

`pipeline/agent_credentials.py` sets an isolated `CLAUDE_CONFIG_DIR` and clears
inherited provider auth/configuration variables. The host also blanks known
Studio secret variables and disables nonessential assistant traffic. This is a
filter of known variables, not a sandbox or a complete environment allowlist.
Prompt injection can influence proposals and requests; review the proposed
changes before accepting and avoid giving the assistant confidential material
you do not want sent to its provider. See [Privacy](privacy.md).

## Key storage

`pipeline/secret_store.py` protects saved assistant and stock keys using
current-user Windows DPAPI or private POSIX files. On POSIX, secret files must
not grant group/other access; the containing directory is restricted to the
current OS user. POSIX storage uses permissions, not encryption at rest.
`ANTHROPIC_API_KEY` overrides a saved assistant key. Credential responses report
presence/source and connection state, not the key. Credential and error handling
redact known keys from responses and logs. Backups and OS account access remain
the user's responsibility.

## Non-goals

LAN exposure, public hosting and multi-user operation are unsupported. Do not
put Studio behind a proxy or change the bind policy to share it. Malware or
another process running as the same OS user can read media, change documents,
access keys or control child processes; loopback and file permissions do not
defend against it. Studio provides no media encryption at rest, media-parser
sandbox or guarantee against malicious model output. Report vulnerabilities through
[SECURITY](../SECURITY.md).
