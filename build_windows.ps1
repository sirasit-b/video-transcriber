# Build the Windows installer -> dist\VideoTranscriber-Setup-<version>.exe
# Needs Python 3 and Inno Setup 6 (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Check($what) { if ($LASTEXITCODE) { throw "$what failed (exit $LASTEXITCODE)" } }

if (!(Test-Path .venv)) { python -m venv .venv; Check "venv" }
.\.venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller; Check "pip install"
.\.venv\Scripts\python app.py --selftest; Check "selftest"
.\.venv\Scripts\pyinstaller --noconfirm --clean --windowed --name VideoTranscriber --collect-all imageio_ffmpeg app.py
Check "pyinstaller"

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (!$iscc) { throw "Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup" }
& $iscc /Q installer.iss; Check "Inno Setup"
Get-ChildItem dist\*.exe
