# MSUpdate.Edge

Create `Edge.wim` for `DISM.exe /Add-Edge` command. All files are downloaded from MS Official.

## Usage

```cmd
mkdir D:\Edge
curl.exe -sSLo "D:\Edge\Edge.wim" "https://github.com/xrgzs/MSUpdate.Edge/releases/latest/download/Edge_x64.wim"

mkdir D:\Mount
DISM.exe /Mount-Image /ImageFile:D:\Setup\sources\install.wim /Index:1 /MountDir:D:\Mount

DISM.exe /Image:D:\Mount /Remove-Edge
DISM.exe /Image:D:\Mount /Add-Edge /SupportPath:D:\Edge

DISM.exe /Unmount-Image /MountDir:D:\Mount /Commit
```

## Build

Prerequisites:

- 7-Zip (in PATH)
- PowerShell 7 (Not Windows PowerShell)
- Python 3 (in PATH)
- Windows 10/11 x64 host (x86 not supported)
- Internet connection to Microsoft and GitHub

```powershell
git clone https://github.com/xrgzs/MSUpdate.Edge.git
cd MSUpdate.Edge
.\make.ps1 -Architecture x64
# Output: Edge_x64.wim
```

## Thanks

- https://github.com/google/omaha
- https://github.com/abbodi1406/BatUtil/tree/master/EdgeChromiumInstaller
- https://github.com/Bush2021/edge_installer
- https://www.7-zip.org/sdk.html
- https://wimlib.net/
