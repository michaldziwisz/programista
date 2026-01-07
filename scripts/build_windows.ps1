$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Output "Project: $root"

& py -3.12 -V

$venvPath = Join-Path $root ".venv-win-build"
if (-not (Test-Path $venvPath)) {
  & py -3.12 -m venv $venvPath
}

$python = Join-Path $venvPath "Scripts\\python.exe"

& $python -m pip install -U pip
& $python -m pip install -e ".[gui]"
& $python -m pip install -U pyinstaller

$distPath = Join-Path $root "dist-windows"
$buildPath = Join-Path $root "build-windows"

$exePath = Join-Path $distPath "Programista.exe"
Get-Process -Name "Programista" -ErrorAction SilentlyContinue | Stop-Process -Force
for ($i = 0; $i -lt 10 -and (Test-Path $exePath); $i++) {
  try {
    Remove-Item -Force $exePath
    break
  } catch {
    Start-Sleep -Milliseconds 300
  }
}

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --name "Programista" `
  --windowed `
  --onefile `
  --distpath $distPath `
  --workpath $buildPath `
  --specpath $buildPath `
  (Join-Path $root "run_programista.py")

Write-Output "Built: $exePath"
