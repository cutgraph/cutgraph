# Download for Windows

Cutgraph Studio v0.2 supports Windows 11 x64. Download the unsigned installer
and `SHA256SUMS.txt` from our GitHub release page. Compare the full SHA-256 using
PowerShell's `Get-FileHash`; GitHub attestations also verify build provenance.
Follow [Verify your download](installing.md#verify-your-download) for commands.
These checks do not remove Windows prompts.

Browsers label internet downloads with **Mark of the Web** so Windows can apply
extra checks. SmartScreen may show **Windows protected your PC** because Studio
is new and unsigned; the prompt alone is not a malware finding. After verifying
the file and trusting its source, choose **More info** → **Run anyway**, if
offered. Managed computers may prevent continuing.

Check **Windows Security → App & browser control → Smart App Control**
(**On**, **Evaluation**, **Off**). When **On**, expect this installer to be
blocked with no per-app override. v0.2 does not support that mode; keep protection
enabled.

Future signing would identify the publisher but may still prompt; a future
Microsoft Store install would avoid SmartScreen download warnings.

[SmartScreen](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
and [Smart App Control FAQ](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions):
accessed 2026-10-26.
