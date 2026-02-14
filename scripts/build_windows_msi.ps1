param(
  [ValidateSet("x64", "arm64")]
  [Parameter(Mandatory = $true)]
  [string]$Arch,

  [Parameter(Mandatory = $true)]
  [string]$Version,

  [Parameter(Mandatory = $true)]
  [string]$SourceExe,

  [Parameter(Mandatory = $true)]
  [string]$OutMsi
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Normalize-Version([string]$v) {
  $v = ($v ?? "").Trim()
  if ($v.StartsWith("v")) {
    $v = $v.Substring(1)
  }
  $parts = $v.Split(".") | Where-Object { $_ -ne "" }
  $nums = @()
  foreach ($p in $parts) {
    $n = 0
    [void][int]::TryParse($p, [ref]$n)
    $nums += $n
  }
  while ($nums.Count -lt 4) {
    $nums += 0
  }
  return ($nums[0..3] -join ".")
}

$appVersion = Normalize-Version $Version

if (-not (Test-Path $SourceExe)) {
  throw "Nie znaleziono pliku SourceExe: $SourceExe"
}

$outDir = Split-Path -Parent $OutMsi
if ($outDir -and -not (Test-Path $outDir)) {
  New-Item -ItemType Directory -Path $outDir | Out-Null
}

# Ensure dotnet tools are on PATH (GitHub Actions uses this location for global tools).
$dotnetTools = Join-Path $env:USERPROFILE ".dotnet\\tools"
if (Test-Path $dotnetTools) {
  $env:Path = "$dotnetTools;$env:Path"
}

if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
  if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "Nie znaleziono narzędzia 'dotnet' potrzebnego do instalacji WiX Toolset."
  }
  & dotnet tool install --global wix | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "dotnet tool install wix failed with exit code $LASTEXITCODE"
  }
}

$wixVersionRaw = (& wix --version 2>$null | Select-Object -First 1).Trim()
$wixVersion = ($wixVersionRaw -split "[^0-9\\.]")[0]
if (-not $wixVersion) {
  throw "Nie udało się odczytać wersji WiX Toolset (wix --version)."
}

# Install UI extension pinned to the same version as the WiX tool to avoid
# accidentally pulling an incompatible pre-release (e.g. 7.0.0-rc.1).
& wix extension add "WixToolset.UI.wixext/$wixVersion" | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "wix extension add WixToolset.UI.wixext/$wixVersion failed with exit code $LASTEXITCODE"
}

$wxs = Join-Path $root "installer\\wix\\Programista.wxs"
& wix build `
  $wxs `
  -arch $Arch `
  -ext WixToolset.UI.wixext `
  -d AppVersion=$appVersion `
  -d SourceExe=$SourceExe `
  -o $OutMsi

if ($LASTEXITCODE -ne 0) {
  throw "wix build failed with exit code $LASTEXITCODE"
}

Write-Output "Built MSI: $OutMsi"
