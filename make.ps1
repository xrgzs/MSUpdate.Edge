#Requires -Version 7
param (
    [string]$Architecture = "x64",
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

# Ensure Administrator privileges
if (-not $CheckOnly -and -not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    throw "This script must be run as an administrator."
}

# Fetch the latest stable Edge release info from the Microsoft Edge Updates API
$edgeUpdates = Invoke-RestMethod -Uri "https://edgeupdates.microsoft.com/api/products"
$edgeStable = $edgeUpdates | Where-Object { $_.Product -eq "Stable" } | Select-Object -First 1
$edgeRelease = $edgeStable.Releases | Where-Object { $_.Platform -eq 'Windows' -and $_.Architecture -eq $Architecture } | Select-Object -First 1
$edgeArtifact = $edgeRelease.Artifacts | Where-Object { $_.ArtifactName -eq 'msi' } | Select-Object -first 1 
$edgeVersion = $edgeRelease.ProductVersion
Write-Host "Latest stable Edge version: $edgeVersion"
$edgeVersion | Out-File -FilePath ".\Edge_Version.txt" -Force -Encoding UTF8 -NoNewline

if ($CheckOnly) {
    Write-Host "Check only mode enabled. Exiting."
    return
}

# Prepare directories
New-Item ".\bin" -ItemType Directory -Force
New-Item ".\temp" -ItemType Directory -Force

# Installing dependencies
function Test-Hashes {
    param (
        [hashtable]$Hashes,
        [string]$Algorithm
    )
    return $Hashes.GetEnumerator() | ForEach-Object {
        $file = $_.Key
        $expectedHash = $_.Value
        Write-Host -ForegroundColor Blue "Verifying $file $Algorithm hash ..."
        Write-Host -ForegroundColor Gray "Expected: $expectedHash"
        $actualHash = (Get-FileHash -Path $file -Algorithm $Algorithm).Hash
        Write-Host -ForegroundColor Gray "Actual  : $actualHash"
        if ($actualHash -ne $expectedHash) {
            # return $false
            Write-Error "$file hash not match."
        } else {
            Write-Host -ForegroundColor Green "$file hash match."
        }
    }
}

function Test-SHA256 ([hashtable]$Hashes) { return Test-Hashes -Hashes $Hashes -Algorithm "SHA256" }

if (-not (Test-Path -Path ".\bin\wimlib-imagex.exe")) {
    Write-Host "wimlib-imagex not found, downloading..."
    Invoke-WebRequest -Uri 'https://github.com/user-attachments/files/25449494/wimlib-1.14.5-windows-x86_64-bin.zip' -OutFile ".\temp\wimlib.zip"
    Expand-Archive -Path ".\temp\wimlib.zip" -DestinationPath ".\temp\wimlib" -Force
    Copy-Item -Path ".\temp\wimlib\wimlib-imagex.exe" -Destination ".\bin\wimlib-imagex.exe"
    Copy-Item -Path ".\temp\wimlib\libwim-15.dll" -Destination ".\bin\libwim-15.dll"
}
Test-SHA256 @{ 
    ".\bin\wimlib-imagex.exe" = "34C0C4165591AD1F592837ED99D08273C58D6ED3FE0ED6360CF34E7B0739B353"
    ".\bin\libwim-15.dll"     = "BA853EE1E3FC5F5798581F02E8E066BA07A0A2375F0BF444FE981431FD508495"
}

$env:PATH = "$((Resolve-Path .\bin).Path);$env:PATH"

# Download the Edge installer MSI
Invoke-WebRequest -Uri $edgeArtifact.Location -OutFile ".\EdgeEnt.msi"
if ((Get-FileHash -Path ".\EdgeEnt.msi" -Algorithm $edgeArtifact.HashAlgorithm).Hash -ne $edgeArtifact.Hash) {
    throw "Hash mismatch for downloaded EdgeEnt.msi"
}

# Extract the Edge installer EXE
7z e -y ".\EdgeEnt.msi" "Binary.MicrosoftEdgeInstaller" || throw "Failed to extract Binary.MicrosoftEdgeInstaller from EdgeEnt.msi"
Rename-Item ".\Binary.MicrosoftEdgeInstaller" ".\EdgeInstaller.exe"
Remove-Item ".\EdgeEnt.msi"

# EdgeInstaller.exe is a self-extracting Google Omaha installer.
# Extract the LZMA resource from PE.
7z e -y -t* ".\EdgeInstaller.exe" ".rsrc\0\B\102" || throw "Failed to extract .rsrc\0\B\102 from EdgeInstaller.exe"
if (-not (Test-Path ".\102")) {
    throw "Failed to extract the LZMA resource from EdgeInstaller.exe"
}
Remove-Item ".\EdgeInstaller.exe"

# This is a LZMA-compressed BCJ2 stream of tarball.
# We temporarily extract it using a Python script written by Claude Opus 4.6.
python extract_resource.py ".\102" ".\EdgeUpdateOffline" || throw "Failed to extract EdgeUpdateOffline from .\102"
Remove-Item ".\102"

# The EdgeUpdateOffline contains:
# - EdgeUpdate all scattered files
# - `MicrosoftEdge_X64_*.*.*.*.exe.{GUID}`: Edge installer without EdgeUpdate
# - `OfflineManifest.gup`: The xml manifest of the Edge installer: install commands, etc. Useless.

# Move the Edge installer without EdgeUpdate to the current directory for packaging
Get-ChildItem ".\EdgeUpdateOffline\MicrosoftEdge_*_*.*.*.*.exe.*" | ForEach-Object {
    Move-Item $_ ".\MicrosoftEdge.exe"
}
Remove-Item ".\EdgeUpdateOffline\OfflineManifest.gup"

# Get the EdgeUpdate version from .\EdgeUpdateOffline\MicrosoftEdgeUpdate.exe
$edgeUpdateVersion = (Get-Item ".\EdgeUpdateOffline\MicrosoftEdgeUpdate.exe").VersionInfo.FileVersion
if ([string]::IsNullOrEmpty($edgeUpdateVersion)) {
    throw "Failed to get EdgeUpdate version from MicrosoftEdgeUpdate.exe"
}
Write-Host "EdgeUpdate version: $edgeUpdateVersion"

# Extract MSEDGE.7Z from Edge installer without EdgeUpdate
7z e -y ".\MicrosoftEdge.exe" "MSEDGE.7z" || throw "Failed to extract MSEDGE.7Z from Edge installer"
Remove-Item ".\MicrosoftEdge.exe"

# Prepare "C:\Program Files (x86)\Microsoft" for packaging Edge.wim
# .\EdgeContent -> C:\Program Files (x86)\Microsoft
New-Item ".\EdgeContent" -ItemType Directory -Force

New-Item ".\EdgeContent\EdgeUpdate\$edgeUpdateVersion" -ItemType Directory -Force
Move-Item ".\EdgeUpdateOffline\*" ".\EdgeContent\EdgeUpdate\$edgeUpdateVersion" -Force
Copy-Item ".\EdgeContent\EdgeUpdate\$edgeUpdateVersion\EdgeUpdate.dat" ".\EdgeContent\EdgeUpdate\EdgeUpdate.dat" -Force
Copy-Item ".\EdgeContent\EdgeUpdate\$edgeUpdateVersion\MicrosoftEdgeUpdate.exe" ".\EdgeContent\EdgeUpdate\MicrosoftEdgeUpdate.exe" -Force
Copy-Item ".\EdgeContent\EdgeUpdate\$edgeUpdateVersion\CopilotUpdate.exe" ".\EdgeContent\EdgeUpdate\CopilotUpdate.exe" -Force

7z x -y ".\MSEDGE.7z" -o".\EdgeContent" || throw "Failed to extract MSEDGE.7z to .\EdgeContent\Chrome-bin"
Rename-Item ".\EdgeContent\Chrome-bin" "EdgeCore"
Remove-Item ".\MSEDGE.7z"

New-Item ".\EdgeContent\Edge\Application\$edgeVersion" -ItemType Directory -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\Edge.dat" ".\EdgeContent\Edge" -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\*" ".\EdgeContent\Edge\Application\$edgeVersion" -Recurse -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\delegatedWebFeatures.sccd" ".\EdgeContent\Edge\Application" -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\msedge.exe" ".\EdgeContent\Edge\Application" -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\msedge_proxy.exe" ".\EdgeContent\Edge\Application" -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\pwahelper.exe" ".\EdgeContent\Edge\Application" -Force
@"
<Application xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'>
  <VisualElements
      ShowNameOnSquare150x150Logo='on'
      Square150x150Logo='$edgeVersion\VisualElements\Logo.png'
      Square70x70Logo='$edgeVersion\VisualElements\SmallLogo.png'
      Square44x44Logo='$edgeVersion\VisualElements\SmallLogo.png'
      ForegroundText='light'
      BackgroundColor='#173A73'
      ShortDisplayName='Edge'/>
</Application>
"@ -replace "`n", "`r`n" | Out-File -FilePath ".\EdgeContent\Edge\Application\msedge.VisualElementsManifest.xml" -Force -Encoding UTF8

New-Item ".\EdgeContent\EdgeWebView\Application\$edgeVersion" -ItemType Directory -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\EdgeWebView.dat" ".\EdgeContent\EdgeWebView" -Force
Copy-Item ".\EdgeContent\EdgeCore\$edgeVersion\*" ".\EdgeContent\EdgeWebView\Application\$edgeVersion" -Recurse -Force

# Package Edge.wim
wimlib-imagex.exe capture ".\EdgeContent" ".\Edge_$Architecture.wim" "EdgeContent" --compress=LZMS --solid || throw "Failed to create Edge.wim"
