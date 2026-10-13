<#
.SYNOPSIS
  Gives the Backuper bot least-privilege access to ONE folder on this Windows server.

.DESCRIPTION
  * Creates a local user (default "backuper") if it doesn't exist: a normal user, NOT an
    administrator, password never expires. Use the SAME password on every server, then one
    credential in the bot works everywhere. (Skipped if -User is a domain account, CORP\user.)
  * Shares the folder as a hidden share (name ending in $) for that user.
  * NTFS: Modify (read + delete old backups for "keep last N"), or Read with -ReadOnly.
  * -SqlBackups : lets the SQL Server service write into the folder (bot-run BACKUP DATABASE).
  * -OpenSqlPort: firewall rule so only the bot server can reach SQL Server on TCP 1433.
  Safe to run again. Run once per folder.

  Run in PowerShell opened with "Run as administrator":
    Set-ExecutionPolicy -Scope Process Bypass
    .\setup-backuper-access.ps1 -Path 'D:\HS_ChoobShar\BackUp' -Name 'BackuperSQL$' -SqlBackups -OpenSqlPort

.EXAMPLE
  # Domain account (nothing is created, only rights are granted); existing share "Arpa" = C:\Arpa
  .\setup-backuper-access.ps1 -Path 'C:\Arpa\Auto Backup' -Name 'Arpa' -User 'HARMONIC\BackuperBot' -SqlBackups -OpenSqlPort

.EXAMPLE
  # Existing share: just grant read access to whatever folder it points to
  .\setup-backuper-access.ps1 -Name 'Edari' -User 'HARMONIC\BackuperBot' -ReadOnly

.EXAMPLE
  # File server 192.168.10.35 — archive (write) and two read-only folders
  .\setup-backuper-access.ps1 -Path 'E:\Backuper' -Name 'Backuper$'
  .\setup-backuper-access.ps1 -Path 'D:\Edari'    -Name 'Edari$'   -ReadOnly
  .\setup-backuper-access.ps1 -Path 'D:\Finance'  -Name 'Finance$' -ReadOnly
#>
[CmdletBinding()]
param(
    [string] $Path,                                     # optional for an existing share: its folder is used
    [Parameter(Mandatory)] [string] $Name,
    [string] $User = 'backuper',
    [switch] $ReadOnly,
    [switch] $SqlBackups,
    [switch] $OpenSqlPort,
    [int]    $SqlPort = 1433,
    [string] $BotServer = '192.168.11.11'
)
$ErrorActionPreference = 'Stop'

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell with "Run as administrator" and run this again.'
}

# ---- 1. account --------------------------------------------------------------------
if ($User.Contains('\') -or $User.Contains('@')) {
    $account = $User                                   # domain account: nothing to create
} else {
    $account = "$env:COMPUTERNAME\$User"
    if (Get-Command Get-LocalUser -ErrorAction SilentlyContinue) {   # Server 2016+
        if (-not (Get-LocalUser -Name $User -ErrorAction SilentlyContinue)) {
            $pw = Read-Host -AsSecureString "Password for the new local user '$User' (same on every server)"
            New-LocalUser -Name $User -Password $pw -PasswordNeverExpires -UserMayNotChangePassword `
                -AccountNeverExpires -Description 'Backuper bot (backup collection)' | Out-Null
            Write-Host "Created local user $account" -ForegroundColor Green
        } else {
            Write-Host "Local user $account already exists (password unchanged)"
        }
    } else {                                                          # Server 2012 R2 and older
        net user $User 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $pw = Read-Host -AsSecureString "Password for the new local user '$User' (same on every server)"
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw))
            net user $User $plain /add /expires:never /passwordchg:no /comment:"Backuper bot" | Out-Null
            $plain = $null
            if ($LASTEXITCODE -ne 0) { throw "Couldn't create user $User" }
            Write-Host "Created local user $account" -ForegroundColor Green
        }
        wmic useraccount where "name='$User'" set PasswordExpires=false | Out-Null
    }
}

# ---- 2. folder + share -------------------------------------------------------------
if (-not $Path) {
    $existing = Get-SmbShare -Name $Name -ErrorAction SilentlyContinue
    if (-not $existing) { throw "Share $Name doesn't exist - give -Path to create it" }
    $Path = $existing.Path
}
if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Write-Host "Created folder $Path"
}
$admins = (New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544').Translate(
    [Security.Principal.NTAccount]).Value
$right = if ($ReadOnly) { 'Read' } else { 'Change' }
$share = Get-SmbShare -Name $Name -ErrorAction SilentlyContinue
if ($share) {
    # An existing share may point to the folder itself or to one of its parents (e.g. share "Arpa" = C:\Arpa
    # and -Path C:\Arpa\Auto Backup): grant access on the share, folder rights only on -Path.
    $sharePath = $share.Path.TrimEnd('\') + '\'
    if (-not ($Path.TrimEnd('\') + '\').StartsWith($sharePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Share $Name points to $($share.Path), which doesn't contain $Path"
    }
    Grant-SmbShareAccess -Name $Name -AccountName $account -AccessRight $right -Force | Out-Null
} else {
    $p = @{ Name = $Name; Path = $Path; FullAccess = $admins; Description = 'Backuper bot' }
    if ($ReadOnly) { $p.ReadAccess = $account } else { $p.ChangeAccess = $account }
    New-SmbShare @p | Out-Null
}

# ---- 3. NTFS permissions -------------------------------------------------------------
$ntfs = if ($ReadOnly) { '(OI)(CI)RX' } else { '(OI)(CI)M' }
icacls $Path /grant "${account}:$ntfs" | Out-Null
if ($SqlBackups) {
    $sql = Get-Service | Where-Object { $_.Name -eq 'MSSQLSERVER' -or $_.Name -like 'MSSQL$*' }
    if (-not $sql) { Write-Warning 'No SQL Server service found on this machine' }
    foreach ($svc in $sql) {
        icacls $Path /grant "NT SERVICE\$($svc.Name):(OI)(CI)M" | Out-Null
        Write-Host "SQL Server service $($svc.Name) can write to $Path"
    }
}

# ---- 4. firewall for SQL ---------------------------------------------------------------
if ($OpenSqlPort) {
    $ruleName = "Backuper - SQL Server $SqlPort"
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $SqlPort `
            -RemoteAddress $BotServer -Action Allow | Out-Null
    }
    Write-Host "Firewall: TCP $SqlPort open for $BotServer"
}

# ---- 5. summary ----------------------------------------------------------------------
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress
Write-Host ''
Write-Host "Done. $account has $(if ($ReadOnly) {'READ'} else {'MODIFY'}) access to $Path via:" -ForegroundColor Green
$sub = if ($share) { $Path.Substring($share.Path.TrimEnd('\').Length) } else { '' }
foreach ($ip in $ips) { Write-Host "    \\$ip\$Name$sub" }
