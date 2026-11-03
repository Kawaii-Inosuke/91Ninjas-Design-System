<#
.SYNOPSIS
  Build 91Ninjas Studio for Windows x64 and package it as an MSI (D-040).
.DESCRIPTION
  Writes $env:DIST\91ninjas-studio-<version>-windows-x64.msi (the asset name craft-update looks for). The MSI holds
  91ninjas-studio.exe (the editor, with the 91Ninjas icon), uv.exe and the agents folder (studio/agents without its
  .venv and tests); the first start builds the Python environment in the user's data folder.
  Needs: Rust (MSVC), WiX v5 (dotnet tool install --global wix --version 5.0.2;
  wix extension add -g WixToolset.UI.wixext/5.0.2 WixToolset.Util.wixext/5.0.2). Unsigned (D-035).
#>
param([switch] $SkipBuild)
$ErrorActionPreference = 'Stop'
$Studio = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Editor = Join-Path $Studio 'editor'
$Agents = Join-Path $Studio 'agents'
function Invoke-Native([string] $What, [scriptblock] $Block) {
  Write-Output "==> $What"; & $Block
  if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}
$Version = $env:STUDIO_VERSION
if (-not $Version) { throw 'set STUDIO_VERSION (e.g. 1.0.0)' }
$MsiVersion = ($Version -split '-')[0]
$Dist = if ($env:DIST) { $env:DIST } else { Join-Path $Studio 'dist' }
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
$Target = 'x86_64-pc-windows-msvc'
$TargetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $Editor 'target' }

if (-not $SkipBuild) {
  $env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS = '-C target-feature=+crt-static'
  $env:VECTORCRAFT_REQUIRE_WINRES = '1'
  Push-Location $Editor
  Invoke-Native 'cargo build' { cargo build --release --locked -p vectorcraft --target $Target }
  Pop-Location
}

$Stage = Join-Path $TargetDir 'studio-windows'
Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $TargetDir "$Target\release\vectorcraft.exe") (Join-Path $Stage "91ninjas-studio.exe")

# uv (Astral, MIT/Apache-2.0): runs the agents; the first start fetches Python and the packages with it.
$UvVersion = if ($env:UV_VERSION) { $env:UV_VERSION } else { '0.13.1' }
$UvZip = Join-Path $env:RUNNER_TEMP 'uv.zip'
Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $UvZip
Expand-Archive $UvZip -DestinationPath (Join-Path $env:RUNNER_TEMP 'uv') -Force
Copy-Item (Join-Path $env:RUNNER_TEMP 'uv\uv.exe') $Stage

# The agents: source and lock file only.
$AgentsStage = Join-Path $Stage 'agents'
robocopy $Agents $AgentsStage /E /XD .venv tests __pycache__ .pytest_cache /XF *.pyc .env | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copying the agents failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0

# Installer art in the 91Ninjas colours.
Add-Type -AssemblyName System.Drawing
$ink = [System.Drawing.ColorTranslator]::FromHtml('#062324'); $mint = [System.Drawing.ColorTranslator]::FromHtml('#7ee8c8')
$icon = [System.Drawing.Image]::FromFile((Join-Path $Editor 'assets\91studio\icon-256.png'))
$Banner = Join-Path $Stage 'banner.bmp'; $Dialog = Join-Path $Stage 'dialog.bmp'
foreach ($spec in @(@($Banner, 493, 58), @($Dialog, 493, 312))) {
  $bmp = New-Object System.Drawing.Bitmap $spec[1], $spec[2]
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = 'HighQualityBicubic'
  $g.Clear([System.Drawing.Color]::White)
  if ($spec[2] -eq 58) { $g.DrawImage($icon, 493 - 54, 6, 46, 46) }
  else {
    $g.FillRectangle((New-Object System.Drawing.SolidBrush $ink), 0, 0, 164, 312)
    $g.FillRectangle((New-Object System.Drawing.SolidBrush $mint), 160, 0, 4, 312)
    $g.DrawImage($icon, 18, 64, 124, 124)
  }
  $g.Dispose(); $bmp.Save($spec[0], [System.Drawing.Imaging.ImageFormat]::Bmp); $bmp.Dispose()
}
$icon.Dispose()

$Msi = Join-Path $Dist "91ninjas-studio-$Version-windows-x64.msi"
Invoke-Native 'wix build' {
  wix build (Join-Path $PSScriptRoot '91ninjas-studio.wxs') -arch x64 -ext WixToolset.UI.wixext -ext WixToolset.Util.wixext -culture en-US `
    -d "Version=$MsiVersion" -d "DisplayVersion=$Version" -d "StageDir=$Stage" -d "IconPath=$(Join-Path $Editor 'assets\91studio\icon.ico')" `
    -d "BannerBmp=$Banner" -d "DialogBmp=$Dialog" -o $Msi
}
Remove-Item -Force -ErrorAction SilentlyContinue ([IO.Path]::ChangeExtension($Msi, '.wixpdb'))
Get-Item $Msi | Format-List Name, Length
