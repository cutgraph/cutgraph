param([Parameter(Mandatory)][string]$Destination)
$ErrorActionPreference = 'Stop'

if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Compiler installation requires a disposable hosted runner.'
}
$pin = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'compiler.json') -Raw | ConvertFrom-Json).release
$archive = Join-Path $env:RUNNER_TEMP "innosetup-$($pin.version).exe"
Invoke-WebRequest -Uri $pin.url -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.sha256) {
    throw 'Inno Setup checksum mismatch'
}
$process = Start-Process -FilePath $archive -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$Destination`"") -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Inno Setup installation failed: $($process.ExitCode)" }
if (-not (Test-Path -LiteralPath (Join-Path $Destination 'ISCC.exe'))) { throw 'Inno Setup compiler is missing' }
