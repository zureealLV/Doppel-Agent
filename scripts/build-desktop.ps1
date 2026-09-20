param([switch]$OneFile)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $root
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}
$python = Join-Path $root '.venv\Scripts\python.exe'
& $python -m pip install -e '.[agent,desktop]' 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$mode = if ($OneFile) { '--onefile' } else { '--onedir' }
$icon = Join-Path $root 'assets\doppel-agent.ico'
& $python -m PyInstaller --noconfirm --clean --windowed $mode `
    --name 'DoppelAgent' --icon $icon --add-data "$icon;assets" --collect-data 'doppel_agent.web' `
    --distpath '.dist' --workpath '.build-tmp\pyinstaller' `
    --specpath '.build-tmp' 'scripts\desktop_entry.py'
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Host "Desktop build: $root\.dist\DoppelAgent"
