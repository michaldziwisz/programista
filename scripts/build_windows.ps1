param(
  [ValidateSet("auto", "x64", "arm64")]
  [string]$Arch = "auto",

  # Optional: full path to python.exe (e.g. ARM64 Python on Windows ARM).
  [string]$PythonExe = "",

  # Recreate venv even if it exists (useful when switching Python arch).
  [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Output "Project: $root"

$basePython = "py"
$basePythonArgs = @("-3.12")
if ($PythonExe) {
  $basePython = $PythonExe
  $basePythonArgs = @()
}

& $basePython @basePythonArgs -V

$machine = (& $basePython @basePythonArgs -c "import platform; print(platform.machine())").Trim()
$resolvedArch = switch ($machine) {
  "ARM64" { "arm64" }
  "AMD64" { "x64" }
  default { "" }
}

if (-not $resolvedArch) {
  throw "Nieznana architektura Pythona: '$machine'."
}

if ($Arch -ne "auto" -and $Arch -ne $resolvedArch) {
  throw "Wybrano Arch='$Arch', ale Python jest '$machine' (=$resolvedArch). Zainstaluj odpowiedni Python lub podaj -PythonExe."
}

$venvPath = Join-Path $root ".venv-win-build-$resolvedArch"
if ($RecreateVenv -and (Test-Path $venvPath)) {
  Remove-Item -Recurse -Force $venvPath
}
if (-not (Test-Path $venvPath)) {
  & $basePython @basePythonArgs -m venv $venvPath
}

$python = Join-Path $venvPath "Scripts\\python.exe"

& $python -m pip install -U pip
& $python -m pip install -e ".[gui]"
& $python -m pip install -U pyinstaller

$distPath = Join-Path $root "dist-windows"
$buildPath = Join-Path $root "build-windows"

$exePath = Join-Path $distPath "programista.exe"
$archExePath = Join-Path $distPath "programista-win-$resolvedArch.exe"

$processNames = @("programista", "programista-win-x64", "programista-win-arm64")
Get-Process -Name $processNames -ErrorAction SilentlyContinue | Stop-Process -Force

$pathsToRemove = @($exePath, $archExePath)
foreach ($p in $pathsToRemove) {
  for ($i = 0; $i -lt 10 -and (Test-Path $p); $i++) {
    try {
      Remove-Item -Force $p
      break
    } catch {
      Start-Sleep -Milliseconds 300
    }
  }
}

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --name "programista" `
  --windowed `
  --onefile `
  --distpath $distPath `
  --workpath $buildPath `
  --specpath $buildPath `
  (Join-Path $root "run_programista.py")

Write-Output "Built: $exePath"
Copy-Item -Force $exePath $archExePath
Write-Output "Built: $archExePath"
