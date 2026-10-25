# Fails unless every file has a valid Authenticode signature; prints who signed it.
# Usage: ./windows/verify-signatures.ps1 file1 [file2 ...]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Files)
$ErrorActionPreference = "Stop"

foreach ($f in $Files) {
    $sig = Get-AuthenticodeSignature $f
    $who = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { "no signature" }
    $when = if ($sig.TimeStamperCertificate) { "timestamped" } else { "NOT timestamped" }
    Write-Host "$f : $($sig.Status) ($who, $when)"
    if ($sig.Status -ne "Valid") { throw "$f is not validly signed: $($sig.StatusMessage)" }
}
