# Sideload the HM-KNX DCA AddIn into ETS6's AddIns directory by copying the
# built DLL and manifest to C:\ProgramData\KNX\ETS6\Apps\AddIns\<AppId>\ and
# signing the folder with ETS' own signer so ETS lists the app as signed.
#
# The signature is OPTIONAL: on a *licensed* ETS (Lite/Home/Professional) the
# DCA tab appears even without it. ETS Demo never loads third-party DCAs, with
# or without a signature. Signing only makes ETS report "Signature valid: True"
# and matches how manufacturer apps (MDT, ABB, ...) ship.
#
# Signing uses ETS' own Knx.Ets.XmlSigning.dll (the same signer OpenKNXproducer
# uses for the knxprod) via reflection - no private key is required, just an
# ETS install. Override its location with -EtsPath.
param(
    [string]$EtsPath = 'C:\Program Files (x86)\ETS6'
)
$ErrorActionPreference = 'Stop'

$AppId    = 'M00FA-A4805'
$RepoRoot = Split-Path -Parent $PSScriptRoot          # repo root = parent of scripts/
$Source   = Join-Path $RepoRoot 'dca\bin\Release\net48'
$Manifest = Join-Path $RepoRoot 'dca\AddInManifest.xml'
$AddInsBase = "C:\ProgramData\KNX\ETS6\Apps\AddIns"
$Target     = Join-Path $AddInsBase $AppId
$Signature  = Join-Path $AddInsBase "$AppId.signature"

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

if (Test-Path $Target)    { Write-Host "Removing existing $Target ..."; Remove-Item -Path $Target -Recurse -Force }
if (Test-Path $Signature) { Remove-Item -Path $Signature -Force }
New-Item -ItemType Directory -Path $Target | Out-Null

Copy-Item "$Source\HM-KNX.Dca.dll" "$Target\HM-KNX.Dca.dll" -Force
Copy-Item $Manifest                "$Target\AddInManifest.xml" -Force

# Sign the AddIn folder with ETS' own signer. This writes the sibling file
# <AddIns>\<AppId>.signature (UTF-8 BOM + base64), exactly as ETS expects.
$xmlSigningDll = Join-Path $EtsPath 'Knx.Ets.XmlSigning.dll'
if (Test-Path $xmlSigningDll) {
    try {
        $resolve = [System.ResolveEventHandler]{
            param($s, $e)
            $name = (New-Object System.Reflection.AssemblyName($e.Name)).Name
            $dll  = Join-Path $EtsPath ($name + '.dll')
            if (Test-Path $dll) { return [System.Reflection.Assembly]::LoadFrom($dll) }
            return $null
        }
        [System.AppDomain]::CurrentDomain.add_AssemblyResolve($resolve)

        $asm = [System.Reflection.Assembly]::LoadFrom($xmlSigningDll)
        $m   = $asm.GetType('Knx.Ets.XmlSigning.XmlSigning').GetMethod(
                   'SignDirectory', [System.Reflection.BindingFlags]'Static,NonPublic')
        $argv = [System.Array]::CreateInstance([object], 3)
        $argv.SetValue([string]$Target, 0)   # path
        $argv.SetValue([bool]$false,   1)     # useCasingOfBaggagesXml
        $argv.SetValue([string[]]$null, 2)    # excludeFileEndings
        $m.Invoke($null, $argv) | Out-Null

        [System.AppDomain]::CurrentDomain.remove_AssemblyResolve($resolve)
        if (Test-Path $Signature) {
            Write-Host "Signed: $Signature" -ForegroundColor Green
        } else {
            Write-Host "Signer ran but produced no signature file." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "WARNING: signing failed ($($_.Exception.InnerException.Message))." -ForegroundColor Yellow
        Write-Host "         Sideload continues unsigned - the DCA still works on a licensed ETS." -ForegroundColor Yellow
    }
} else {
    Write-Host "NOTE: $xmlSigningDll not found - installing unsigned." -ForegroundColor Yellow
    Write-Host "      Pass -EtsPath '<ETS install dir>' to sign. Unsigned works on a licensed ETS." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Installed:"
Get-ChildItem $Target | Select-Object Name, @{N='KB';E={[math]::Round($_.Length/1KB,2)}} | Format-Table -AutoSize

Write-Host ""
Write-Host "Start ETS6, open a project containing the HM-KNX device, select it, click the DCA tab."
Write-Host ""
Write-Host "If the DCA tab does NOT appear:"
Write-Host "  - The DCA tab requires a LICENSED ETS (Lite / Home / Professional)."
Write-Host "    ETS Demo never shows DCA tabs for any device - that is an ETS"
Write-Host "    limitation, not a problem with this app (no extra KNX licence is"
Write-Host "    needed, the app is freeware)."
Write-Host "  - Make sure you selected the device (not the building node)."
Write-Host "  - Close and restart ETS6 so it re-reads the AddIns folder."
