# Sideload the HM-KNX DCA AddIn into ETS6's AddIns directory by copying the
# built DLL and manifest to C:\ProgramData\KNX\ETS6\Apps\AddIns\<AppId>\.
$ErrorActionPreference = 'Stop'

$AppId    = 'M00FA-A4805'
$RepoRoot = Split-Path -Parent $PSScriptRoot          # repo root = parent of scripts/
$Source   = Join-Path $RepoRoot 'dca\bin\Release\net48'
$Manifest = Join-Path $RepoRoot 'dca\AddInManifest.xml'
$Target   = "C:\ProgramData\KNX\ETS6\Apps\AddIns\$AppId"

Write-Host "Source DLL:      $Source\HM-KNX.Dca.dll"
Write-Host "Source manifest: $Manifest"
Write-Host "Target:          $Target"
Write-Host ""

if (-not (Test-Path "$Source\HM-KNX.Dca.dll")) { throw "DLL not built - run: dotnet build -c Release dca\HM-KNX.Dca.csproj" }
if (-not (Test-Path $Manifest))                { throw "Manifest missing: $Manifest" }

# Make sure ETS6 is closed; otherwise the running instance can hold a lock
# on the AddIn DLLs and the install won't take effect until next start.
$ets = Get-Process ETS6 -EA SilentlyContinue
if ($ets) {
    Write-Host "WARNING: ETS6 is currently running (PID $($ets.Id))." -ForegroundColor Yellow
    Write-Host "         Close ETS6 before sideloading, then re-run this script." -ForegroundColor Yellow
    exit 1
}

if (Test-Path $Target) {
    Write-Host "Removing existing $Target ..."
    Remove-Item -Path $Target -Recurse -Force
}
New-Item -ItemType Directory -Path $Target | Out-Null

Copy-Item "$Source\HM-KNX.Dca.dll" "$Target\HM-KNX.Dca.dll" -Force
Copy-Item $Manifest                "$Target\AddInManifest.xml" -Force

Write-Host ""
Write-Host "Installed:"
Get-ChildItem $Target | Select-Object Name, @{N='KB';E={[math]::Round($_.Length/1KB,2)}} | Format-Table -AutoSize

Write-Host ""
Write-Host "Start ETS6, open a project containing the HM-KNX device, click the DCA tab."
Write-Host "If the DCA does NOT appear, refresh the ETS6 AddIn cache:"
Write-Host "  - close ETS6"
Write-Host "  - delete %LocalAppData%\Knx\ETS6\AddInsCache  (if it exists)"
Write-Host "  - restart ETS6"
