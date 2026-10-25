# Authenticode-signs files when a code-signing certificate is configured, otherwise does nothing.
#
# CI provides the certificate through repository secrets:
#   WINDOWS_SIGNING_PFX_BASE64  base64 of a .pfx (OV/EV code-signing certificate)
#   WINDOWS_SIGNING_PFX_PASSWORD
# Usage: ./windows/sign.ps1 file1 [file2 ...]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Files)
$ErrorActionPreference = "Stop"

if (-not $env:WINDOWS_SIGNING_PFX_BASE64) {
    Write-Host "No signing certificate configured; leaving $($Files.Count) file(s) unsigned."
    exit 0
}

$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found" }

$pfx = Join-Path $env:RUNNER_TEMP "signing.pfx"
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_SIGNING_PFX_BASE64))
try {
    foreach ($f in $Files) {
        & $signtool.FullName sign /fd SHA256 /td SHA256 /tr "http://timestamp.digicert.com" `
            /f $pfx /p $env:WINDOWS_SIGNING_PFX_PASSWORD /d "CamAndroid" $f
        if ($LASTEXITCODE -ne 0) { throw "signing failed for $f" }
        & $signtool.FullName verify /pa $f
        if ($LASTEXITCODE -ne 0) { throw "signature verification failed for $f" }
    }
} finally {
    Remove-Item $pfx -Force
}
