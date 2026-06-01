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
$wixToolVersion = "6.0.2"

if (-not (Test-Path $SourceExe)) {
  throw "Nie znaleziono pliku SourceExe: $SourceExe"
}

$outDir = Split-Path -Parent $OutMsi
if ($outDir -and -not (Test-Path $outDir)) {
  New-Item -ItemType Directory -Path $outDir | Out-Null
}

function Add-DotnetToolPath() {
  if (-not $env:USERPROFILE) {
    return
  }

  # GitHub Actions installs global .NET tools here, but the directory may not
  # exist until after the first tool installation.
  $dotnetTools = Join-Path $env:USERPROFILE ".dotnet\\tools"
  $pathSeparator = [System.IO.Path]::PathSeparator
  $pathParts = $env:Path -split [regex]::Escape($pathSeparator)
  if ($pathParts -notcontains $dotnetTools) {
    $env:Path = "$dotnetTools$pathSeparator$env:Path"
  }
}

function Get-WixToolVersion() {
  if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
    return $null
  }

  $raw = (& wix --version 2>$null | Select-Object -First 1)
  if (-not $raw) {
    return $null
  }

  return ($raw.Trim() -split "[^0-9\\.]")[0]
}

function Install-WixTool([string]$RequiredVersion) {
  if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "Nie znaleziono narzędzia 'dotnet' potrzebnego do instalacji WiX Toolset."
  }

  & dotnet tool install --global wix --version $RequiredVersion | Out-Host
  if ($LASTEXITCODE -eq 0) {
    return
  }

  & dotnet tool update --global wix --version $RequiredVersion | Out-Host
  if ($LASTEXITCODE -eq 0) {
    return
  }

  & dotnet tool uninstall --global wix | Out-Host
  & dotnet tool install --global wix --version $RequiredVersion | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "dotnet tool install wix --version $RequiredVersion failed with exit code $LASTEXITCODE"
  }
}

Add-DotnetToolPath

$wixVersion = Get-WixToolVersion
if ($wixVersion -ne $wixToolVersion) {
  Install-WixTool $wixToolVersion
  Add-DotnetToolPath
  $wixVersion = Get-WixToolVersion
}

if ($wixVersion -ne $wixToolVersion) {
  throw "Nie udało się przygotować WiX Toolset $wixToolVersion (aktywny: $wixVersion)."
}

# Install UI extension pinned to the same version as the WiX tool to avoid
# accidentally pulling an incompatible pre-release (e.g. 7.0.0-rc.1).
& wix extension add "WixToolset.UI.wixext/$wixVersion" | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "wix extension add WixToolset.UI.wixext/$wixVersion failed with exit code $LASTEXITCODE"
}

$plWxl = Join-Path $root "installer\\wix\\WixUI_pl-pl.wxl"
if (-not (Test-Path $plWxl)) {
  throw "Nie znaleziono pliku lokalizacji WiX UI: $plWxl"
}

$wxs = Join-Path $root "installer\\wix\\Programista.wxs"
& wix build `
  $wxs `
  -arch $Arch `
  -ext WixToolset.UI.wixext `
  -loc $plWxl `
  -d AppVersion=$appVersion `
  -d SourceExe=$SourceExe `
  -o $OutMsi

if ($LASTEXITCODE -ne 0) {
  throw "wix build failed with exit code $LASTEXITCODE"
}

Write-Output "Built MSI: $OutMsi"
