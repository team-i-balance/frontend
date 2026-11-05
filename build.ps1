# Builds the Windows desktop app into dist\KPGuard\KPGuard.exe
# Keep this file ASCII: Windows PowerShell 5.1 misreads UTF-8 without a BOM.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv' }
}

& $python -m pip install --quiet --disable-pip-version-check -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

& $python -m PyInstaller KPGuard.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

Write-Host 'Built dist\KPGuard\KPGuard.exe'
