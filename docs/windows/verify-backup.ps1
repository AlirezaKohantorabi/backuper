<#
.SYNOPSIS
  Checks files you extracted from a Backuper zip against the zip's .sha256 file.

.DESCRIPTION
  Every zip in the archive has a "<name>.zip.sha256" file next to it: the SHA-256 of each file *before* it
  was zipped. After extracting the zip (7-Zip / WinRAR), run this script: it hashes the extracted files and
  compares. "All files are identical" means you got back exactly what was backed up.

  Works in Windows PowerShell 5.1 and PowerShell 7, also with very long paths.

.EXAMPLE
  # Extracted Cash_HS_Sandogh_1405-07-25_0800.zip into the folder Cash_HS_Sandogh_1405-07-25_0800 (7-Zip's
  # "Extract to <name>\" does that). The folder can be left out then:
  .\verify-backup.ps1 'D:\Restore\Cash_HS_Sandogh_1405-07-25_0800.zip.sha256'

.EXAMPLE
  .\verify-backup.ps1 -Checksums '\\192.168.10.35\Backuper\1405-07-25\iCan-Upload_1405-07-25_0700.zip.sha256' -Folder 'D:\Restore\Upload'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Checksums,
    [string] $Folder                       # default: the .sha256 path without ".zip.sha256"
)
$ErrorActionPreference = 'Stop'

$Checksums = (Resolve-Path -LiteralPath $Checksums).ProviderPath
if (-not $Folder) { $Folder = $Checksums -replace '\.zip\.sha256$', '' }
$Folder = (Resolve-Path -LiteralPath $Folder).ProviderPath

function LongPath([string] $p) {
    if ($p.StartsWith('\\?\')) { return $p }
    if ($p.StartsWith('\\')) { return '\\?\UNC\' + $p.Substring(2) }
    return '\\?\' + $p
}

$sha = [System.Security.Cryptography.SHA256]::Create()
$ok = 0; $bad = 0
foreach ($line in Get-Content -LiteralPath $Checksums -Encoding UTF8) {
    if ($line -notmatch '^([0-9a-fA-F]{64}) \*(.+)$') { continue }
    $want = $Matches[1].ToLower()
    $rel = $Matches[2]
    $file = Join-Path $Folder ($rel -replace '/', '\')
    try {
        $stream = [System.IO.File]::OpenRead((LongPath $file))
    } catch {
        Write-Host "MISSING   $rel" -ForegroundColor Red; $bad++; continue
    }
    try {
        $got = -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') })
    } finally {
        $stream.Dispose()
    }
    if ($got -eq $want) { $ok++ } else { Write-Host "DIFFERENT $rel" -ForegroundColor Red; $bad++ }
}

if ($bad -eq 0) {
    Write-Host "All $ok file(s) are identical to the originals." -ForegroundColor Green
} else {
    Write-Host "$ok file(s) OK, $bad missing or different." -ForegroundColor Red
    exit 1
}
