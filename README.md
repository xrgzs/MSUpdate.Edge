# MSUpdate.Edge

Create `Edge.wim` for `DISM.exe /Add-Edge` command. All files are downloaded from MS Official.

## Usage

```cmd
mkdir D:\Edge
curl.exe -sSLo "D:\Edge\Edge.wim" "https://github.com/xrgzs/MSUpdate.Edge/releases/latest/download/Edge_x64.wim"

DISM.exe /Mount-Image /ImageFile:D:\Edge\Edge.wim /Index:1 /MountDir:D:\Mount\Windows

DISM.exe /Image:D:\Mount\Windows /Remove-Edge
DISM.exe /Image:D:\Mount\Windows /Add-Edge /SupportPath:D:\Edge

DISM.exe /Unmount-Image /MountDir:D:\Mount\Windows /Commit
```
